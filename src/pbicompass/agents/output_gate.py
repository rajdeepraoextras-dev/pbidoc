"""Deterministic quality checks for a generated document bundle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any

from .benchmark import narrative_triples_for, run_benchmark


@dataclass(frozen=True)
class GateIssue:
    check_id: str
    detail: str


class OutputQualityError(RuntimeError):
    def __init__(self, issues: list[GateIssue]):
        self.issues = issues
        summary = "; ".join(f"{i.check_id}: {i.detail}" for i in issues[:8])
        if len(issues) > 8:
            summary += f"; and {len(issues) - 8} more"
        super().__init__(f"Output quality gate failed: {summary}")


_BLOCKING_BENCHMARK_IDS = {
    "T1", "T2", "T3", "T4", "T6", "C1", "C2", "C3", "C4", "C5",
    "C6", "C9", "C10", "V2", "D6", "X3",
}
_ID_RE = re.compile(r'\bid=["\']([^"\']+)["\']', re.IGNORECASE)
_HREF_RE = re.compile(r'\bhref=["\']([^"\']+)["\']', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_RAW_PLACEHOLDER_RE = re.compile(
    r"\b(?:WIP|TBD|TODO|lorem ipsum|fields awaiting input|complete missing fields)\b",
    re.IGNORECASE,
)


def _canonical_maps(model: Any) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    tables = {t.name.casefold(): t.name for t in model.tables}
    measures = {m.name.casefold(): m.name for t in model.tables for m in t.measures}
    pages = {p.display_name.casefold(): p.display_name for p in model.pages}
    return tables, measures, pages


def canonicalize_bundle(docs: dict[str, Any], model: Any) -> None:
    """Apply the model's spelling/casing to structured object-name fields."""
    tables, measures, pages = _canonical_maps(model)
    technical = docs.get("technical")
    if technical:
        for row in technical.semantic_model.tables:
            row["name"] = tables.get(str(row.get("name", "")).casefold(), row.get("name", ""))
        for row in technical.semantic_model.data_dictionary:
            row["table"] = tables.get(str(row.get("table", "")).casefold(), row.get("table", ""))
        for measure in technical.measure_catalog.measures:
            measure.name = measures.get(measure.name.casefold(), measure.name)
            if measure.table:
                measure.table = tables.get(measure.table.casefold(), measure.table)
    audit = docs.get("audit")
    if audit:
        for finding in audit.dax_findings:
            finding.measure = measures.get(finding.measure.casefold(), finding.measure)
            if finding.table:
                finding.table = tables.get(finding.table.casefold(), finding.table)
        for risk in audit.performance_risks:
            risk.object_name = measures.get(
                risk.object_name.casefold(), tables.get(risk.object_name.casefold(), risk.object_name)
            )
            if risk.table:
                risk.table = tables.get(risk.table.casefold(), risk.table)
    guide = docs.get("user-guide")
    if guide:
        for page in guide.pages:
            page.page_title = pages.get(page.page_title.casefold(), page.page_title)


def _render_html_bundle(docs: dict[str, Any], filenames: dict[str, str] | None) -> dict[str, str]:
    from ..render import registry
    from ..render.hub import doc_switcher_links

    names = filenames or {dtype: f"{dtype}.html" for dtype in docs}
    rendered: dict[str, str] = {}
    for dtype, doc in docs.items():
        links = doc_switcher_links(list(docs), dtype, names, "index.html") if len(docs) > 1 else None
        rendered[dtype] = registry.RENDERERS[dtype]["html"](
            doc, doc_links=links, sibling_hrefs=names if len(docs) > 1 else None,
        )
    return rendered


def _narrative_duplicate_issues(docs: dict[str, Any]) -> list[GateIssue]:
    seen: dict[str, tuple[str, str]] = {}
    issues: list[GateIssue] = []
    for dtype, doc in docs.items():
        for location, text, _setter in narrative_triples_for(dtype, doc):
            normalized = re.sub(r"\W+", " ", text.casefold()).strip()
            if len(normalized) < 120:
                continue
            prior = seen.get(normalized)
            if prior and prior[0] != dtype:
                pair = {(prior[0], prior[1]), (dtype, location)}
                intentional_glossary_reuse = any(
                    d == "user-guide" and loc.startswith("glossary[") for d, loc in pair
                ) and any(
                    d == "technical" and (
                        loc.startswith("glossary_entries[")
                        or loc.startswith("measure_catalog.measures[")
                    )
                    for d, loc in pair
                )
                if intentional_glossary_reuse:
                    continue
                issues.append(GateIssue("DEDUP", f"{dtype}:{location} duplicates {prior[0]}:{prior[1]}"))
            else:
                seen[normalized] = (dtype, location)
    return issues


# -- Self-contradicting ask (SENSE) -------------------------------------------
#
# The gate scored a live bundle 59/61 while it shipped this, verbatim:
#
#   consequence: "Since row-level security is not configured, there is no risk
#                 of role misalignment, but all report viewers have unrestricted
#                 access to spend data."
#   ask:         "Review RLS role memberships quarterly and adjust as
#                 departments change."
#
# — i.e. review the memberships of roles the same sentence says don't exist.
# Every structural check passed, because none of them read for *sense*. This
# closes that hole for the provable class: a risk whose ask applies a
# maintenance verb to a thing its own consequence declares absent.
#
# Deliberately narrow. It requires a *definite* absence assertion ("X is not
# configured"), not a hedged one ("without X (if needed) ..."), and it treats
# create-verbs (define/configure/set up) as correct responses to absence. The
# fixed live output — consequence "Without row-level security (if needed),
# department heads could see each other's spend data" + ask "verify that RLS
# roles are correctly applied" — must NOT trip it: "verify" is a legitimate ask
# when an intake note claims RLS exists but the model has none.

# Concept groups: terms that name the same thing across a consequence/ask pair.
_SENSE_CONCEPTS: dict[str, str] = {
    "row-level security": r"(?:row-?level security|RLS)(?:\s+roles?)?|(?<!\w)roles?(?!\w)",
    "relationships": r"relationships?",
    "descriptions": r"descriptions?",
}
# A definite claim the concept does not exist. Hedges ("without X, if needed")
# are excluded on purpose — they don't assert absence, they suppose it.
_ABSENT_TMPL = (
    r"(?:{t})\b[^.]{{0,30}}?\b(?:is|are|was|were)\s+not\s+(?:configured|defined|set\s?up|enabled|present|in place)"
    r"|\bno\b[^.]{{0,20}}?\b(?:{t})\b[^.]{{0,30}}?\b(?:are|is)?\s*(?:defined|configured|exist|set\s?up|in place)"
    r"|\bnot?\s+(?:{t})\b[^.]{{0,20}}?\b(?:defined|configured|exist)"
)
# Verbs that only make sense if the thing already exists. "verify"/"confirm" are
# excluded (reasonable when a human claim conflicts with the model), as are
# create-verbs (define/configure/add/set up), which absence properly calls for.
_MAINTAIN_TMPL = r"\b(?:review|adjust|update|maintain|re-?validate|audit|reassign|refresh|tune|keep)\w*\b[^.]{{0,45}}?\b(?:{t})\b"


def _self_contradicting_ask_issues(docs: dict[str, Any]) -> list[GateIssue]:
    """Flag a risk whose ask presupposes something its consequence calls absent."""
    issues: list[GateIssue] = []
    for dtype, doc in docs.items():
        risks = getattr(doc, "top_risks", None) or []
        for i, risk in enumerate(risks):
            consequence = (getattr(risk, "consequence", "") or "").strip()
            ask = (getattr(risk, "ask", "") or "").strip()
            if not consequence or not ask:
                continue
            for concept, terms in _SENSE_CONCEPTS.items():
                absent = re.search(_ABSENT_TMPL.format(t=terms), consequence, re.IGNORECASE)
                if not absent:
                    continue
                presupposed = re.search(_MAINTAIN_TMPL.format(t=terms), ask, re.IGNORECASE)
                if presupposed:
                    issues.append(GateIssue(
                        "SENSE",
                        f"{dtype}:top_risks[{i}] contradicts itself — the consequence says "
                        f"{concept} is not configured, but the ask is to "
                        f"'{presupposed.group(0).strip()}'. An ask must not maintain what the "
                        f"same risk says does not exist.",
                    ))
                    break
    return issues


def validate_bundle(docs: dict[str, Any], model: Any, *,
                    html_filenames: dict[str, str] | None = None,
                    ai_context: Any = None) -> dict[str, str]:
    """Validate and return the already-rendered HTML, or raise with issues."""
    canonicalize_bundle(docs, model)
    issues: list[GateIssue] = []
    benchmark = run_benchmark(docs, model=model, ai_context=ai_context)
    for result in benchmark.failing():
        if result.check_id in _BLOCKING_BENCHMARK_IDS:
            location_text = f" Locations: {', '.join(result.locations)}." if result.locations else ""
            issues.append(GateIssue(result.check_id, result.detail + location_text))
    issues.extend(_narrative_duplicate_issues(docs))
    issues.extend(_self_contradicting_ask_issues(docs))

    rendered = _render_html_bundle(docs, html_filenames)
    names = html_filenames or {dtype: f"{dtype}.html" for dtype in docs}
    by_filename = {names[dtype]: html for dtype, html in rendered.items()}
    for dtype, html in rendered.items():
        ids = _ID_RE.findall(html)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        if duplicates:
            issues.append(GateIssue("HTML-ID", f"{dtype} has duplicate IDs: {', '.join(duplicates[:5])}"))
        id_set = set(ids)
        for href in _HREF_RE.findall(html):
            if href.startswith("#") and href[1:] and href[1:] not in id_set and "${" not in href:
                issues.append(GateIssue("HTML-NAV", f"{dtype} has broken link {href}"))
            elif ".html#" in href:
                filename, anchor = href.split("#", 1)
                target = by_filename.get(filename)
                if target is not None and anchor not in set(_ID_RE.findall(target)):
                    issues.append(GateIssue("HTML-XREF", f"{dtype} has broken cross-link {href}"))
        visible = unescape(_TAG_RE.sub(" ", re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", "", html,
                                                    flags=re.IGNORECASE | re.DOTALL)))
        if len(re.sub(r"\s+", " ", visible).strip()) < 120:
            issues.append(GateIssue("HTML-EMPTY", f"{dtype} rendered without substantial content"))
        if _RAW_PLACEHOLDER_RE.search(visible):
            issues.append(GateIssue("PLACEHOLDER", f"{dtype} contains a raw placeholder"))
    if issues:
        raise OutputQualityError(issues)
    return rendered
