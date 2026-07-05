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


def non_data_note(count: int) -> str:
    """The standard line for non-data page objects (buttons, images, shapes,
    text labels) — layout elements, not documented individually."""
    return (f"{count} non-data object(s) on this page — buttons, images, shapes, "
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


def is_local_path(path_str: str) -> bool:
    return bool(re.search(r"^[A-Za-z]:[\\/]", path_str) or "Users/" in path_str or "Users\\" in path_str)


def md_todo(text: str) -> str:
    """Markdown ``To complete`` placeholder blockquote."""
    return f"> **✎ To complete:** {text}\n"


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
    """HTML ``To complete`` placeholder div. Escapes ``text`` itself."""
    return f'<div class="todo"><b>✎ To complete:</b> {html_e(text)}</div>'


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
    """Count how many human metadata fields are filled vs total, returning (pct, missing_count, missing_list)."""
    fields = [
        "owner", "refresh_schedule", "target_audience", "version", "status",
        "author", "reviewer", "classification", "business_decision", "requirements",
        "security_notes", "refresh_notes", "deployment_notes", "access_notes",
        "glossary", "assumptions", "support_notes"
    ]
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
