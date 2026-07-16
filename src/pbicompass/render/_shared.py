"""Small rendering primitives shared by every document-type renderer.

Extracted from ``markdown.py``/``html.py``/``docx.py`` (which previously each
defined their own copy) so new renderers (audit, executive, user-guide) reuse
the same building blocks instead of duplicating them.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape as _escape


# Reader-facing names for the health-score components computed by
# ``agents.audit_rules.compute_health_score`` — shared by every renderer so
# the same component is never labelled two different ways.
HEALTH_COMPONENT_LABELS = {
    "modeling": "Model Design",
    "dax": "DAX Quality",
    "governance": "Governance & Security",
    "performance": "Performance",
    "unused_assets": "Maintainability",
}

OPTIONAL_CONTEXT_FIELDS = (
    "owner", "refresh_schedule", "target_audience", "version", "status",
    "author", "reviewer", "classification", "business_decision", "requirements",
    "security_notes", "refresh_notes", "deployment_notes", "access_notes",
    "glossary", "assumptions", "support_notes",
)


def refresh_policy_summary(rp: dict) -> str:
    """Plain-language one-liner for an extracted incremental-refresh policy
    (``{table, policy_type, mode, rolling_window_*, incremental_*}``). Shared by
    all three renderers so the wording is identical everywhere."""
    parts = []
    rwp, rwg = rp.get("rolling_window_periods"), rp.get("rolling_window_granularity")
    if rwp and rwg:
        parts.append(f"stores the last {rwp} {pluralize(str(rwg), rwp)}")
    ip, ig = rp.get("incremental_periods"), rp.get("incremental_granularity")
    if ip and ig:
        parts.append(f"refreshes the last {ip} {pluralize(str(ig), ip)} incrementally")
    detail = "; ".join(parts) if parts else "incremental refresh configured"
    ptype = rp.get("policy_type")
    prefix = f"{ptype} policy" if ptype else "Incremental refresh"
    return f"{prefix} — {detail}."


def pluralize(word: str, count: int, plural: str | None = None) -> str:
    """Regular-English pluralization for microcopy. Kills the "asset(s)"
    pattern scattered across the audit engine and generators — grammatically
    wrong at count==1 ("1 asset(s)" reads like an unfinished template) and
    the kind of rough edge a Fortune-500 reviewer notices in the first
    minute. Pass an explicit ``plural`` for irregular nouns; otherwise a
    standard English suffix rule is applied (works for every noun this
    codebase actually pluralizes: workbook/database/source/file/finding/
    asset/field/item/check/component/page/risk/step)."""
    if count == 1:
        return word
    if plural is not None:
        return plural
    if re.search(r"(s|x|z|ch|sh)$", word, re.IGNORECASE):
        return word + "es"
    if re.search(r"[^aeiou]y$", word, re.IGNORECASE):
        return word[:-1] + "ies"
    return word + "s"


def pluralize_count(word: str, count: int, plural: str | None = None) -> str:
    """``"{count} {pluralize(word, count, plural)}"`` — the common case of
    ``pluralize`` where the number is shown right next to the noun."""
    return f"{count} {pluralize(word, count, plural)}"


def action_chip(text: str, *, tone: str = "warn") -> str:
    """A small actionable pill for a missing/unassigned governance field
    (owner, steward, classification) — replaces a bare, easy-to-miss "not
    specified" with something that visually reads as an open action item.
    ``tone``: "warn" (amber, the default — something a reader should go
    fill in) or "muted" (a neutral placeholder, no action implied)."""
    return f'<span class="action-chip {html_e(tone)}">{html_e(text)}</span>'


def truncate_label(text: str, limit: int) -> str:
    """Ellipsis-truncate ``text`` to ``limit`` characters for a narrow UI
    slot (e.g. a wireframe thumbnail caption). Callers pair this with a
    ``title="{text}"`` attribute holding the untruncated value so the full
    label is always recoverable (hover / screen reader), never silently
    lost."""
    text = text or ""
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


# Model diagram (§6): re-enabled (Day 6) — ``render._model_diagram`` ships
# the v6 "Studio" redesign the wireframe/lineage diagrams already had. A
# single flag here so every renderer's own claim about where the diagram
# lives stays in sync with whether it's actually rendered, instead of
# three independently-maintained sentences (§6's own markdown aside, §18's
# own note in all three formats) that can silently drift into a false
# claim (P2) — flip this back to ``False`` only if the render call itself
# is ever disabled again.
MODEL_DIAGRAM_RENDERED = True


def non_data_note(count: int) -> str:
    """The standard line for non-data page objects (buttons, images, shapes,
    text labels) — layout elements, not documented individually."""
    return (f"{pluralize_count('non-data object', count)} on this page — buttons, images, shapes, "
            "and text labels used for layout and navigation.")


def format_timestamp(iso_str: str | None) -> str:
    """Human-readable rendering of an ISO-8601 timestamp for report headers —
    e.g. ``"4 July 2026, 11:07 UTC"`` instead of the machine-format
    ``"2026-07-04T11:07:25.853407+00:00"`` a reader would otherwise see.
    Returns the input unchanged if it can't be parsed (never raises on
    malformed or missing input); the machine-readable ISO form still lives in
    ``to_json()`` for anything downstream that needs it."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    tz = "UTC" if dt.utcoffset() is not None and dt.utcoffset().total_seconds() == 0 else dt.strftime("%Z")
    return f"{dt.day} {dt.strftime('%B')} {dt.year}, {dt.strftime('%H:%M')} {tz}".rstrip()


def slicer_field_label(slicer: dict) -> str:
    """Slicer field display text, noting multiplicity when more than one
    slicer visual on a page is bound to the same field (see
    ``agents.report_facts.slicers``) instead of repeating an identical row."""
    count = slicer.get("count", 1)
    return f'{slicer["field"]} ({count} slicers)' if count > 1 else slicer["field"]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def anchor_slug(name: str) -> str:
    """URL/anchor-safe slug for an object name (table/measure/column/...):
    lowercase, non-alphanumerics collapsed to a single hyphen, trimmed.
    Shared by the interactive model diagram (click-to-jump) and cross-
    document links so every renderer computes the same id for the same
    object name."""
    slug = _SLUG_RE.sub("-", (name or "").lower()).strip("-")
    return slug or "x"


def dedupe_ids(ids: list[str]) -> list[str]:
    """Make a list of anchor ids collision-safe: repeats get a ``-2``,
    ``-3``, ... suffix (the first occurrence keeps the bare id). Two
    distinct names can collapse to the same slug once symbols are stripped
    — e.g. ``"Var LE1"`` and ``"Var LE1 %"`` both become ``var-le1`` — and a
    duplicate ``id="..."`` breaks in-page links and search (I2). Callers
    must dedupe *once* and reuse the same list everywhere that id is
    referenced (row markup and any search-index entry pointing at it), or
    the two uses drift out of sync."""
    seen: dict[str, int] = {}
    out = []
    for i in ids:
        seen[i] = seen.get(i, 0) + 1
        out.append(i if seen[i] == 1 else f"{i}-{seen[i]}")
    return out


def is_local_path(path_str: str) -> bool:
    return bool(re.search(r"^[A-Za-z]:[\\/]", path_str) or "Users/" in path_str or "Users\\" in path_str)


def md_todo(text: str) -> str:
    """Neutral note for optional context not supplied during generation."""
    return f"> **Not provided during generation:** {text}\n"


def md_table(headers: list[str], rows: list[list[str]], empty: str = "_None._") -> str:
    """Markdown table (or an ``empty`` fallback line if ``rows`` is empty)."""
    if not rows:
        return empty + "\n"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out) + "\n"


def html_e(v) -> str:
    return _escape("" if v is None else str(v))


def html_todo(text: str) -> str:
    """Neutral note for optional context not supplied during generation."""
    return f'<div class="todo"><b>Not provided during generation:</b> {html_e(text)}</div>'


def html_table(
    headers: list[str], rows: list[list[str]], empty: str = "None.",
    row_ids: list[str] | None = None,
) -> str:
    """HTML table. Headers/``empty`` are escaped here; row cells are inserted
    as-is since callers commonly pre-build cell HTML (e.g. ``<span>`` markup).
    ``row_ids``, when given, adds a stable ``id`` per ``<tr>`` (one per row,
    same order) — e.g. so search results and cross-document links can jump
    straight to a specific finding instead of just the section."""
    if not rows:
        return f'<p class="muted">{html_e(empty)}</p>'
    head = "".join(f"<th>{html_e(h)}</th>" for h in headers)
    if row_ids:
        body = "".join(
            f'<tr id="{html_e(rid)}">' + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
            for r, rid in zip(rows, row_ids)
        )
    else:
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def compute_completeness(metadata: Any) -> tuple[int, int, list[str]]:
    """Report optional context coverage without treating absent input as a quality score."""
    fields = OPTIONAL_CONTEXT_FIELDS
    from typing import Any as TypAny
    filled = 0
    missing = []
    for f in fields:
        val = getattr(metadata, f, None)
        if val and "✎" not in str(val) and "TBC" not in str(val) and "not specified" not in str(val).lower():
            filled += 1
        else:
            missing.append(f)
            
    total = len(fields)
    pct = round(100 * filled / total) if total > 0 else 100
    return pct, total - filled, missing


# Which human-fillable metadata fields, when overridden, flip a section's
# provenance badge to "Human-provided" (5.6). Sections absent here have no
# override-able field and always render their section default.
SECTION_PROVENANCE_FIELDS: dict[int, list[str]] = {
    1: ["version", "status", "author", "reviewer", "classification", "target_audience", "refresh_schedule", "owner"],
    2: ["business_decision"],
    3: ["requirements"],
    4: ["owner", "target_audience", "author"],
    10: ["security_notes"],
    11: ["refresh_notes"],
    12: ["deployment_notes"],
    13: ["access_notes"],
    14: ["glossary"],
    15: ["assumptions"],
    17: ["support_notes"],
    18: ["owner"],
}

# Default provenance label per section (1-18; 19 "Methodology & Guarantees"
# is a static, hand-written section every renderer labels "Extracted" directly).
SECTION_DEFAULT_PROVENANCE: dict[int, str] = {
    1: "Extracted", 2: "AI-inferred", 3: "Human-provided", 4: "Human-provided",
    5: "Extracted", 6: "Extracted", 7: "Extracted", 8: "Extracted", 9: "Extracted",
    10: "Extracted", 11: "Extracted", 12: "Extracted", 13: "Extracted",
    14: "Extracted", 15: "Extracted", 16: "AI-inferred", 17: "Extracted",
    18: "Extracted",
}


def doc_subtitle(metadata: Any) -> str:
    """Standard document-header subtitle base: target audience + generation
    timestamp, with a classification badge appended when set (Day 3) — so
    the audit/executive/user-guide headers show classification too, not
    just the technical document's own Document Control table. Callers that
    append their own extra segment (audit's Score Trend) do so after this."""
    subtitle = f"{metadata.target_audience or ''} · generated {format_timestamp(metadata.generated_at)}"
    classification = getattr(metadata, "classification", None)
    if classification:
        subtitle += f" · Classification: {classification}"
    return subtitle


def html_discrepancy_callout(discrepancies: list[dict]) -> str:
    """Day 3: "You stated X; the model shows Y" — a human-stated intake fact
    that contradicts the model's own metadata is never silently resolved
    one way or the other; both sides are shown side by side. Returns ``""``
    when there's nothing to show (the common case)."""
    if not discrepancies:
        return ""
    cards = []
    for d in discrepancies:
        cards.append(
            '<div class="card-section discrepancy-callout" style="border-left: 4px solid #d97706;">'
            '<p style="margin: 0 0 6px 0;"><strong>⚠ Discrepancy — human input vs. model</strong></p>'
            f'<p><strong>You stated:</strong> {html_e(d.get("human_claim", ""))}</p>'
            f'<p><strong>The model shows:</strong> {html_e(d.get("model_finding", ""))}</p>'
            f'<p class="muted">{html_e(d.get("explanation", ""))}</p>'
            '</div>'
        )
    return "".join(cards)


def md_discrepancy_callout(discrepancies: list[dict]) -> str:
    if not discrepancies:
        return ""
    parts = []
    for d in discrepancies:
        parts.append(
            f"\n> **⚠ Discrepancy — human input vs. model**\n"
            f"> **You stated:** {d.get('human_claim', '')}\n"
            f">\n> **The model shows:** {d.get('model_finding', '')}\n"
            f">\n> {d.get('explanation', '')}\n"
        )
    return "".join(parts)


def section_provenance(section_num: int, metadata: Any) -> str:
    """Bare-text provenance label (``"Extracted"``/``"AI-inferred"``/
    ``"Human-provided"``, no icon) for a technical-doc H2 section: the
    section's default, upgraded to "Human-provided" when any of its
    override-able metadata fields appear in ``metadata.overridden_fields``.
    Shared by html.py/markdown.py/docx.py so all three renderers agree."""
    for f in SECTION_PROVENANCE_FIELDS.get(section_num, []):
        if f in getattr(metadata, "overridden_fields", []):
            return "Human-provided"
    return SECTION_DEFAULT_PROVENANCE.get(section_num, "Extracted")
