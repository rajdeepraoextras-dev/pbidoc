"""Requirements Traceability Matrix (Day 4 of the post-launch hardening plan).

Business Requirements is currently a free-text field that just gets echoed
back verbatim in the technical document's §3 — useful as a record, but it
never tells a reader whether the report actually *satisfies* what's written
there. This module turns each requirement line into a RAG (Covered/Partial/
Gap) verdict against the report's real measures, columns, and pages, with
working anchor links back into the document — the signature "did the build
match the ask" deliverable a Big-4 handover pack always includes and no
competitor tool attempts, per the roadmap.

Two layers, the same shape as ``agents/consistency.py``:

- A deterministic keyword-overlap matcher (:func:`match_candidates`) ranks
  every measure/column/page against a requirement's own vocabulary — free,
  exact, and itself enough to produce a real (if coarse) verdict offline.
- An LLM pass (:func:`build_requirements_matrix`, when a client is given)
  judges the *already-matched* candidates rather than reasoning from
  scratch — it may only cite anchors that were actually offered as
  candidates for that requirement, never invent one, which is what makes
  every evidence link in the rendered matrix a real, working link.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from . import io

if TYPE_CHECKING:
    from .context import JobAIContext
    from .llm import LLMClient

Warn = Callable[[str], None]

STATUSES = ("Covered", "Partial", "Gap")


@dataclass
class RequirementEvidence:
    kind: str    # "measure" | "column" | "page"
    name: str    # display name, e.g. "Total Revenue" or "Sales[Region]"
    anchor: str  # e.g. "measure-total-revenue" — an id that exists in the rendered document


@dataclass
class RequirementCoverage:
    text: str
    priority: str = ""    # "Must" | "Should" | ""
    status: str = "Gap"   # "Covered" | "Partial" | "Gap"
    evidence: list[RequirementEvidence] = field(default_factory=list)
    rationale: str = ""


# -- Parsing --------------------------------------------------------------------

_PRIORITY_RE = re.compile(r"^\s*\[(Must|Should)\]\s*", re.IGNORECASE)


def parse_requirements(text: Optional[str]) -> list[tuple[str, str]]:
    """One requirement per line, with an optional leading ``[Must]``/
    ``[Should]`` priority tag. Returns ``(priority, requirement_text)``
    pairs in input order; blank lines are skipped."""
    out: list[tuple[str, str]] = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _PRIORITY_RE.match(line)
        if m:
            priority = m.group(1).capitalize()
            line = line[m.end():].strip()
        else:
            priority = ""
        if line:
            out.append((priority, line))
    return out


# -- Deterministic candidate matching --------------------------------------------

_STOPWORDS = frozenset(
    "the a an is are was were this that these those and or but if of in on at for to with per by as "
    "show shows display displays must should need needs support supports allow allows report reports".split()
)


def _stem(word: str) -> str:
    """Crude suffix-stripping so a requirement's word form doesn't have to
    match a candidate's exactly (P1): a page literally named "IT Spend
    Trend" must match a requirement asking to "track ... spending trends"
    — "spend"/"spending" and "trend"/"trends" are the same concept, but a
    bare-word-overlap match treats them as unrelated tokens. No real
    morphology, just enough to close that gap; never shown to a reader,
    only used to score candidates."""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _significant_words(text: str) -> set[str]:
    return {_stem(w.lower()) for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text) if w.lower() not in _STOPWORDS}


# DAX time-intelligence functions -> the vocabulary a business requirement
# actually uses to ask for them ("track monthly and yearly spending
# trends") — a measure named e.g. "Sale_YTD" whose *expression* wraps
# TOTALYTD carries none of those words in its name/description, so a
# requirement asking for a trend/period comparison was showing a false Gap
# even though the report has exactly the measure it's asking for (P1).
_TIME_INTELLIGENCE_KEYWORDS: dict[str, str] = {
    "TOTALYTD": "year yearly annual ytd trend cumulative",
    "TOTALQTD": "quarter quarterly qtd trend cumulative",
    "TOTALMTD": "month monthly mtd trend cumulative",
    "DATESYTD": "year yearly annual ytd trend",
    "DATESQTD": "quarter quarterly qtd trend",
    "DATESMTD": "month monthly mtd trend",
    "SAMEPERIODLASTYEAR": "year yearly annual trend comparison prior",
    "PARALLELPERIOD": "period trend comparison prior",
    "DATEADD": "trend comparison period",
    "PREVIOUSYEAR": "year yearly annual prior trend",
    "PREVIOUSQUARTER": "quarter quarterly prior trend",
    "PREVIOUSMONTH": "month monthly prior trend",
    "NEXTYEAR": "year yearly annual trend",
    "NEXTQUARTER": "quarter quarterly trend",
    "NEXTMONTH": "month monthly trend",
}


def _time_intelligence_keywords(expression: Optional[str]) -> str:
    expr_upper = (expression or "").upper()
    return " ".join(kw for func, kw in _TIME_INTELLIGENCE_KEYWORDS.items() if func in expr_upper)


def build_candidates(model, translations: Optional[dict] = None) -> list[dict]:
    """One candidate per measure/column/page — built directly from
    ``model`` (plus the job-shared DAX Translator result, when available)
    rather than any one generator's own assembled artifacts, so this runs
    the same way regardless of which document type asks for it first, with
    no ordering dependency on technical.py having already run. Uses
    ``report_facts.report_pages`` for the page/visual text — the same
    grounded facts every renderer already shows (Day 4: "grounded against
    report_facts"). ``{kind, name, anchor, text, used, ...}`` per
    candidate — ``used``/``self_named`` (P1) let :func:`match_candidates`
    prefer real, report-bound evidence over an incidental keyword hit."""
    from ..render._shared import anchor_slug
    from .report_facts import report_pages
    from .usage import used_column_names, used_measure_names

    translations = translations or {}
    used_measures = used_measure_names(model)
    used_columns = used_column_names(model)
    candidates: list[dict] = []
    for m in model.all_measures():
        translated = (translations.get(m.name) or {}).get("plain_english", "")
        time_kw = _time_intelligence_keywords(m.expression)
        text = f"{m.name} {m.description or ''} {translated} {time_kw}".strip()
        candidates.append({
            "kind": "measure", "name": m.name,
            "anchor": f"measure-{anchor_slug(m.name)}", "text": text,
            "used": m.name in used_measures,
        })
    for t in model.tables:
        for c in t.columns:
            if c.is_hidden:
                continue
            candidates.append({
                "kind": "column", "name": f"{t.name}[{c.name}]",
                "anchor": f"column-{anchor_slug(t.name)}-{anchor_slug(c.name)}",
                "text": f"{t.name} {c.name} {c.description or ''}",
                "used": c.name in used_columns,
                # A column named after its own table (e.g. Department[Department])
                # is almost always that dimension's canonical label/attribute —
                # the natural evidence for "grouped/filtered by X" — versus an
                # unrelated column in the same table (Department[VP]) that only
                # coincidentally shares the table-name word.
                "self_named": c.name.strip().lower() == t.name.strip().lower(),
            })
    for page in report_pages(model):
        if page.get("hidden"):
            continue
        visuals = page.get("visuals", [])
        visual_text = " ".join(v.get("label", "") for v in visuals)
        metric_text = " ".join(x for v in visuals for x in v.get("metrics", []))
        dim_text = " ".join(x for v in visuals for x in v.get("dimensions", []))
        name = page.get("name", "")
        candidates.append({
            "kind": "page", "name": name,
            "anchor": f"page-{anchor_slug(name)}",
            "text": f"{name} {visual_text} {metric_text} {dim_text}",
            "used": True,  # a rendered, visible page is inherently "in use"
        })
    return candidates


def match_candidates(requirement_text: str, candidates: list[dict], *, top_n: int = 5) -> list[dict]:
    """Rank ``candidates`` by shared significant words with
    ``requirement_text``, boosted (P1) for candidates that are actually
    bound to a report visual (``used``) and for a column that is its own
    table's canonical dimension attribute (``self_named``) — real,
    report-bound evidence outranks an unused column that only coincidentally
    shares a keyword. Returns the top ``top_n`` with ``score > 0``, each
    candidate dict extended with its ``score``."""
    req_words = _significant_words(requirement_text)
    if not req_words:
        return []
    scored = []
    for c in candidates:
        overlap = len(req_words & _significant_words(c["text"]))
        if overlap == 0:
            continue
        score = overlap
        if c.get("used"):
            score += 1
        if c.get("self_named"):
            score += 1
        scored.append({**c, "score": score})
    scored.sort(key=lambda c: -c["score"])
    return scored[:top_n]


def _deterministic_verdict(matched: list[dict]) -> tuple[str, list[dict]]:
    """Offline verdict tiered by evidence *kind*, not a bare word-count
    threshold (a prior score-threshold version produced false Gaps for
    requirements a dimension-only match should have floored at Partial —
    "false Gap" is worse optics than weak evidence, since it tells the
    report owner their report can't do something it demonstrably does):

    - Gap: nothing matched at all.
    - Covered: at least one *measure* candidate matched — a measure name
      overlapping real requirement vocabulary is itself strong evidence
      (e.g. "Compare actual spend against budget" naming the literal
      Actual/Plan measures), whether or not a dimension also matched;
      when one did, both are shown as corroborating evidence.
    - Partial: only column/page ("dimension") evidence matched, no
      measure — real evidence (the report has the attribute a requirement
      names) but not confirmation a measure quantifies it that way. This
      is the floor for any non-empty match — it can never fall to Gap.

    A real, reproducible verdict without an LLM, upgraded by the LLM pass
    when a client is available."""
    if not matched:
        return "Gap", []
    measures = [c for c in matched if c["kind"] == "measure"]
    if measures:
        dims = [c for c in matched if c["kind"] != "measure"]
        evidence = measures[:1] + (dims[:1] if dims else measures[1:2])
        return "Covered", evidence
    # Partial: prefer a column over a page as the single evidence item — a
    # specific dimension attribute ("Cost Element[Cost element name]") is
    # stronger, more legible evidence than a generic page name that only
    # happens to rank #1 by raw overlap count (a page's combined visual/
    # metric/dimension text is long enough to rack up incidental word
    # overlaps a narrower column candidate can't compete with on count
    # alone, even when the column is the more relevant match).
    columns = [c for c in matched if c["kind"] == "column"]
    return "Partial", (columns[:1] if columns else matched[:1])


# -- Orchestration ----------------------------------------------------------------

def build_requirements_matrix(
    model,
    requirements_text: Optional[str],
    client: Optional["LLMClient"] = None,
    warn: Optional[Warn] = None,
    ai_context: Optional["JobAIContext"] = None,
) -> list[RequirementCoverage]:
    """Parse ``requirements_text``, deterministically match each line
    against the report's own measures/columns/pages, then (when ``client``
    is given) ask the Requirements Traceability agent to judge the matched
    candidates. Returns ``[]`` when there are no requirements to check —
    never a placeholder row.

    Takes ``model`` directly (not any one generator's assembled artifacts)
    so this runs identically no matter which document type computes it
    first in a job — no ordering dependency the way ``audit_verdicts``
    (Day 2) has on the Audit document specifically. Reuses
    ``ai_context.translations`` (the job-shared DAX Translator result) for
    richer measure text when a job context is already available.

    Always at least the deterministic verdict: a failed/offline LLM pass
    degrades to the keyword-overlap result, never an empty matrix."""
    warn = warn or (lambda _msg: None)
    parsed = parse_requirements(requirements_text)
    if not parsed:
        return []

    translations = ai_context.translations if ai_context is not None else None
    candidates = build_candidates(model, translations)

    per_requirement_candidates: list[dict] = []
    results: list[RequirementCoverage] = []
    # A self-named column (Department[Department]) is the strongest signal
    # build_candidates computes — a table's own canonical dimension
    # attribute, not an incidental keyword hit — so a requirement whose
    # deterministic match includes one is protected from an AI "Gap"
    # downgrade below (RF-11: production false Gaps on exactly this shape).
    self_named_protected: dict[str, bool] = {}
    for priority, text in parsed:
        matched = match_candidates(text, candidates)
        per_requirement_candidates.append({"requirement": text, "candidates": matched})
        status, evidence = _deterministic_verdict(matched)
        self_named_protected[text] = any(c.get("self_named") for c in matched)
        results.append(RequirementCoverage(
            text=text, priority=priority, status=status,
            evidence=[RequirementEvidence(kind=e["kind"], name=e["name"], anchor=e["anchor"]) for e in evidence],
        ))

    if client is None:
        return results

    payload = [
        {
            "requirement": r["requirement"],
            "candidates": [{"anchor": c["anchor"], "kind": c["kind"], "name": c["name"]} for c in r["candidates"]],
        }
        for r in per_requirement_candidates
    ]
    try:
        from .generators.base import call_llm  # local import: see consistency.py's identical note
        data = call_llm(
            client, io.TRACEABILITY_SYSTEM, io.traceability_input(payload),
            io.TRACEABILITY_SCHEMA, warn, "Requirements Traceability", ai_context=ai_context,
        )
    except Exception as exc:  # pragma: no cover - defensive, mirrors call_llm's own contract
        warn(f"Requirements Traceability: LLM call failed, using deterministic verdicts only ({exc})")
        return results
    if not data:
        return results

    by_text = {r.text: r for r in results}
    candidate_by_anchor = {c["anchor"]: c for c in candidates}
    allowed_by_text = {r["requirement"]: {c["anchor"] for c in r["candidates"]} for r in per_requirement_candidates}

    for item in data.get("requirements", []):
        req_text = (item.get("requirement") or "").strip()
        target = by_text.get(req_text)
        if target is None:
            continue
        status = item.get("status")
        if status not in STATUSES:
            continue
        if status == "Gap" and target.status != "Gap" and self_named_protected.get(req_text):
            # AI may only improve a verdict backed by a self-named
            # canonical-dimension match, never erase it into a false Gap —
            # the report *does* have the attribute the requirement names
            # (RF-11: exactly this shape shipped in production for
            # "department"/"region" requirements against a real Department/
            # Country Region dimension). Keep the deterministic verdict.
            continue
        allowed = allowed_by_text.get(req_text, set())
        # Grounding: only anchors this requirement was actually offered as a
        # candidate may be cited as evidence — never trust an invented one.
        evidence_anchors = [a for a in item.get("evidence", []) if a in allowed]
        if status != "Gap" and not evidence_anchors:
            # A "Covered"/"Partial" verdict with no real evidence left after
            # grounding is worse than the deterministic fallback already in
            # place — keep it rather than show an unsupported claim.
            continue
        target.status = status
        target.evidence = [
            RequirementEvidence(kind=candidate_by_anchor[a]["kind"], name=candidate_by_anchor[a]["name"], anchor=a)
            for a in evidence_anchors
        ]
        target.rationale = (item.get("rationale") or "").strip()
    return results


def coverage_stat(matrix: list[RequirementCoverage]) -> str:
    """``"7/9"``-style one-line stat: requirements at least Partially
    covered out of the total — the executive doc's own summary line."""
    if not matrix:
        return ""
    covered = sum(1 for r in matrix if r.status in ("Covered", "Partial"))
    return f"{covered}/{len(matrix)}"
