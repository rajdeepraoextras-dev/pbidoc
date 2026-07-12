"""Render an :class:`ExecutiveDocument` to Markdown, HTML, and DOCX.

Six sections (G.1), concise and non-technical by design: no DAX, no table/
column inventories, no relationship diagrams, no raw file paths, and no
model/report statistics tables — those live in the technical document and
the audit report. Reads in under ten minutes and prints to no more than two
pages. Reuses the same low-level primitives as the other renderers
(``_shared``, ``_html_shell``, ``_docx_writer``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..schemas.executive_document import ExecutiveDocument, ExecutiveRisk
from ._docx_writer import _Docx
from ._html_shell import page_shell
from ._shared import HEALTH_COMPONENT_LABELS
from ._shared import action_chip as _chip
from ._shared import doc_subtitle as _doc_subtitle
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_e as _e
from ._shared import html_table as _html_table
from ._shared import md_table as _table
from ._shared import truncate_label as _truncate

_SECTION_TITLES = [
    "1. Purpose & Value",
    "2. Key KPIs",
    "3. Top Risks & Recommended Actions",
    "4. Data & Refresh at a Glance",
    "5. Ownership & Accountability",
    "6. What's Next",
]

# Component order for the mini-bars — same 5 components the audit report's
# score-hero shows, condensed to a bar instead of a table row with a prose
# "why". Labels are the audit doc's own ``HEALTH_COMPONENT_LABELS``, except
# "DAX Quality" — this document never names DAX/implementation terms
# (``_IMPLEMENTATION_JARGON`` in the generator enforces the same rule on
# risk/next-step text), so the calculation-logic component gets its own
# business-safe label here.
_COMPONENT_ORDER = ["modeling", "dax", "governance", "performance", "unused_assets"]
_EXEC_COMPONENT_LABELS = dict(HEALTH_COMPONENT_LABELS, dax="Calculation Quality")


def _risk_line(r: ExecutiveRisk) -> str:
    return f"[{r.severity}] {r.consequence} — **Ask:** {r.ask}"


def _band_class(band: str) -> str:
    return (band or "").strip().lower().replace(" ", "-") or "fair"


def _score_color(score: int) -> str:
    if score >= 90:
        return "#16a34a"
    if score >= 75:
        return "#124fed"
    if score >= 50:
        return "#b45309"
    return "#b42318"


# -- Markdown -------------------------------------------------------------------
def render_markdown(doc: ExecutiveDocument) -> str:
    md = doc.metadata
    subtitle = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle += f" · Score Trend: {md.score_trend}"
    out: list[str] = [f"# {md.report_name} — Executive Summary\n"]
    out.append(f"_{subtitle}_\n")

    out.append(f"\n## {_SECTION_TITLES[0]}\n")
    out.append(doc.purpose + "\n")
    out.append(doc.business_value + "\n")
    if getattr(doc, "requirements_coverage", None):
        out.append(f"**Requirements coverage:** {doc.requirements_coverage}\n")

    h = doc.health
    if h:
        out.append(f"\n**Health Score: {h.overall}/100 — {h.band}**\n")
        out.append(_table(
            ["Component", "Score"],
            [[_EXEC_COMPONENT_LABELS.get(k, k.replace("_", " ").title()), str(h.component_scores.get(k, "—"))]
             for k in _COMPONENT_ORDER if k in h.component_scores],
        ))

    out.append(f"\n## {_SECTION_TITLES[1]}\n")
    if doc.key_kpis:
        for kpi in doc.key_kpis:
            out.append(f"- {kpi}")
    else:
        out.append("_No KPIs identified._")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[2]}\n")
    if doc.top_risks:
        for r in doc.top_risks:
            out.append(f"- {_risk_line(r)}")
    else:
        out.append("_No known risks — the latest audit found nothing to act on._")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[3]}\n")
    if doc.data_source_types:
        out.append("**Data sources:** " + ", ".join(doc.data_source_types) + "\n")
    else:
        out.append("**Data sources:** _None detected._\n")
    out.append(f"**Refresh schedule:** {doc.refresh_schedule or '_Not documented._'}\n")
    if getattr(doc, "refresh_notes", None):
        out.append(f"**Gateway & latency:** {doc.refresh_notes}\n")
    out.append(doc.maintenance_note + "\n")

    out.append(f"\n## {_SECTION_TITLES[4]}\n")
    ownership_rows = [["Owner", md.owner or "⚠ not assigned"]]
    if doc.steward:
        ownership_rows.append(["Steward", doc.steward])
    if doc.classification:
        ownership_rows.append(["Classification", doc.classification])
    out.append(_table(["Field", "Value"], ownership_rows))
    if not md.owner:
        out.append("\n> **⚠ Action needed:** no owner is assigned. Assign someone accountable so refresh "
                    "failures and change requests have a clear point of contact.\n")

    out.append(f"\n## {_SECTION_TITLES[5]}\n")
    if doc.next_steps:
        out.append(_table(
            ["Severity", "Action", "Effort"],
            [[s.severity, s.action, s.effort] for s in doc.next_steps],
        ))
    else:
        out.append("_Nothing outstanding._")
    out.append("")

    return "\n".join(out).rstrip() + "\n"


# -- HTML -------------------------------------------------------------------------
def _risk_href(rule_id: str, audit_href: str | None) -> str:
    """Deep-link a risk to its exact audit finding (I5) when the audit doc
    is a sibling in this job — the finding's recommendation card is
    anchored by rule_id (see render/audit.py). Falls back to the section
    anchor when no rule_id is available (e.g. the "unused assets" risk),
    and to no link at all when audit wasn't generated in this job (2.7)."""
    if not audit_href:
        return ""
    anchor = f"rec-{rule_id}" if rule_id else "sec8"
    return f' — <a href="{_e(audit_href)}#{_e(anchor)}">full detail</a>'


def _health_mini_html(h) -> str:
    """Condensed band chip + per-component mini-bars — the executive doc's
    own compact take on the audit report's score-hero (Day 5)."""
    if not h:
        return ""
    bars = []
    for k in _COMPONENT_ORDER:
        if k not in h.component_scores:
            continue
        score = h.component_scores[k]
        label = _EXEC_COMPONENT_LABELS.get(k, k.replace("_", " ").title())
        bars.append(
            '<div class="mini-bar-row">'
            f'<span class="mini-bar-label">{_e(label)}</span>'
            '<span class="mini-bar-track"><span class="mini-bar-fill" '
            f'style="width:{max(0, min(100, score))}%; background:{_score_color(score)};"></span></span>'
            f'<span class="mini-bar-value">{_e(score)}</span>'
            '</div>'
        )
    return (
        '<div class="health-mini">'
        f'<div><div class="score-big" style="font-size:2.2rem;">{_e(h.overall)}/100</div>'
        f'<span class="band-chip {_e(_band_class(h.band))}">{_e(h.band)}</span></div>'
        f'<div class="mini-bars">{"".join(bars)}</div>'
        '</div>'
    )


_SVG_LINK_RE = re.compile(r'<a\s+href="[^"]*"[^>]*>|</a>')


def _thumbnails_html(doc: ExecutiveDocument, sibling_hrefs: dict[str, str] | None) -> str:
    """"Report at a glance" (Day 5): a small grid of wireframe thumbnails,
    one per visible page (capped, "+N more" beyond that). Screen-only
    (``.no-print`` — see ``_html_shell.py``) so it never grows the exec
    doc's printed-page count. Each card deep-links into a sibling document's
    full-size page section when one was generated in the same job (2.7)."""
    if not doc.page_thumbnails:
        return ""
    from ._shared import pluralize_count

    target_href = (sibling_hrefs or {}).get("user_guide") or (sibling_hrefs or {}).get("technical")
    cards = []
    for t in doc.page_thumbnails:
        caption = _truncate(t.name, 28)
        # ``t.svg`` is the exact same wireframe the technical doc/user guide
        # build full-size, complete with its own internal per-visual and
        # page-tab <a href="#..."> links to anchors that only exist in
        # *those* documents. Reused verbatim here it would both nest <a>
        # inside the card's own deep-link <a> (invalid HTML) and leave
        # >30% of hrefs dead within this document (I3/G6) — strip the
        # thumbnail's own interactivity since the whole card is already
        # one link to the sibling doc's full, truly-interactive version.
        svg = _SVG_LINK_RE.sub("", t.svg)
        inner = f'{svg}<span class="thumb-caption" title="{_e(t.name)}">{_e(caption)}</span>'
        if target_href:
            cards.append(f'<div class="thumb-card"><a href="{_e(target_href)}#{_e(t.anchor)}">{inner}</a></div>')
        else:
            cards.append(f'<div class="thumb-card">{inner}</div>')
    remaining = doc.page_count - len(doc.page_thumbnails)
    if remaining > 0:
        cards.append(f'<div class="thumb-more">+{_e(pluralize_count("more page", remaining))}</div>')
    return (
        '<div class="no-print">'
        f'<h3>Report at a glance <span class="muted">({_e(pluralize_count("page", doc.page_count))})</span></h3>'
        f'<div class="thumb-grid">{"".join(cards)}</div>'
        '</div>'
    )


def render_html(
    doc: ExecutiveDocument, *,
    doc_links: list[tuple[str, str]] | None = None,
    sibling_hrefs: dict[str, str] | None = None,
) -> str:
    md = doc.metadata
    audit_href = (sibling_hrefs or {}).get("audit")

    toc = [(f"sec{i+1}", title.split(". ", 1)[1]) for i, title in enumerate(_SECTION_TITLES)]
    kpis = []
    if doc.health:
        kpis.append(("Health Score", f"{doc.health.overall}/100 · {doc.health.band}"))
    kpis += [
        ("Key KPIs", len(doc.key_kpis)),
        ("Top Risks", len(doc.top_risks)),
        ("Data Sources", len(doc.data_source_types)),
        ("Next Steps", len(doc.next_steps)),
    ]

    o: list[str] = []
    o.append(f'<h2 id="sec1">{_e(_SECTION_TITLES[0])}</h2>')
    o.append(f"<p>{_e(doc.purpose)}</p>")
    o.append(f"<p>{_e(doc.business_value)}</p>")
    if getattr(doc, "requirements_coverage", None):
        o.append(f'<p><strong>Requirements coverage:</strong> {_e(doc.requirements_coverage)}</p>')
    o.append(_health_mini_html(doc.health))
    o.append(_thumbnails_html(doc, sibling_hrefs))

    kpi_ids = [f"kpi-{i}" for i in range(len(doc.key_kpis))]
    o.append(f'<h2 id="sec2">{_e(_SECTION_TITLES[1])}</h2>')
    if doc.key_kpis:
        o.append("<ul>" + "".join(f'<li id="{_e(kid)}">{_e(k)}</li>' for k, kid in zip(doc.key_kpis, kpi_ids))
                 + "</ul>")
    else:
        o.append('<p class="muted">No KPIs identified.</p>')

    risk_ids = [f"risk-{i}" for i in range(len(doc.top_risks))]
    o.append(f'<h2 id="sec3">{_e(_SECTION_TITLES[2])}</h2>')
    if doc.top_risks:
        for r, rid in zip(doc.top_risks, risk_ids):
            suffix = _risk_href(r.rule_id, audit_href)
            o.append(f'<div class="card-section" id="{_e(rid)}">')
            o.append(f'<p><span class="pill {_e(r.severity.lower())}">{_e(r.severity)}</span> {_e(r.consequence)}</p>')
            o.append(f'<p><strong>Ask:</strong> {_e(r.ask)}{suffix}</p>')
            o.append("</div>")
    else:
        o.append('<p class="muted">No known risks — the latest audit found nothing to act on.</p>')

    o.append(f'<h2 id="sec4">{_e(_SECTION_TITLES[3])}</h2>')
    if doc.data_source_types:
        o.append(f'<p><strong>Data sources:</strong> {_e(", ".join(doc.data_source_types))}</p>')
    else:
        o.append('<p><strong>Data sources:</strong> <span class="muted">None detected.</span></p>')
    refresh_html = _e(doc.refresh_schedule) if doc.refresh_schedule else '<span class="muted">not documented</span>'
    o.append(f'<p><strong>Refresh schedule:</strong> {refresh_html}</p>')
    if getattr(doc, "refresh_notes", None):
        o.append(f'<p><strong>Gateway &amp; latency:</strong> {_e(doc.refresh_notes)}</p>')
    o.append(f"<p>{_e(doc.maintenance_note)}</p>")

    owner_html = _e(md.owner) if md.owner else _chip("⚠ Not assigned", tone="warn")
    ownership_items = [f"<li><strong>Owner:</strong> {owner_html}</li>"]
    if doc.steward:
        ownership_items.append(f"<li><strong>Steward:</strong> {_e(doc.steward)}</li>")
    if doc.classification:
        ownership_items.append(f"<li><strong>Classification:</strong> {_e(doc.classification)}</li>")
    o.append(f'<h2 id="sec5">{_e(_SECTION_TITLES[4])}</h2>')
    o.append("<ul>" + "".join(ownership_items) + "</ul>")
    if not md.owner:
        o.append(
            '<div class="card-section" style="border-left: 4px solid #b45309;">'
            '<p><strong>⚠ Action needed:</strong> no owner is assigned. Assign someone accountable so '
            'refresh failures and change requests have a clear point of contact.</p></div>'
        )

    o.append(f'<h2 id="sec6">{_e(_SECTION_TITLES[5])}</h2>')
    if doc.next_steps:
        o.append(_html_table(
            ["Severity", "Action", "Effort"],
            [[f'<span class="pill {_e(s.severity.lower())}">{_e(s.severity)}</span>', _e(s.action), _e(s.effort)]
             for s in doc.next_steps],
        ))
    else:
        o.append('<p class="muted">Nothing outstanding.</p>')

    search_index = [{"title": sec_title, "type": "section", "anchor": sec_id} for sec_id, sec_title in toc]
    search_index += [
        {"title": kpi.split(" — ", 1)[0], "type": "KPI", "anchor": kid}
        for kpi, kid in zip(doc.key_kpis, kpi_ids)
    ]
    search_index += [
        {"title": r.consequence, "type": "risk", "anchor": rid}
        for r, rid in zip(doc.top_risks, risk_ids)
    ]

    subtitle = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle += f' · Score Trend: {md.score_trend}'

    return page_shell(
        title=f"{md.report_name} — Executive Summary",
        subtitle=subtitle,
        toc=toc, kpis=kpis, body_html="\n".join(o), doc_links=doc_links, search_index=search_index,
        owner=md.owner, version=md.version, status=md.status, classification=doc.classification,
    )


# -- DOCX -------------------------------------------------------------------------
def render_docx(doc: ExecutiveDocument, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata

    subtitle = _doc_subtitle(md)
    if getattr(md, "score_trend", None):
        subtitle += f" · Score Trend: {md.score_trend}"
    d.heading(0, f"{md.report_name} — Executive Summary")
    d.para([d._run(subtitle, italic=True)])

    def _bullets_or_none(items: list[str], empty: str) -> None:
        if items:
            for item in items:
                d.bullet(item)
        else:
            d.para([d._run(empty, italic=True)])

    d.heading(1, _SECTION_TITLES[0])
    d.para(doc.purpose)
    d.para(doc.business_value)
    if getattr(doc, "requirements_coverage", None):
        d.para([d._run("Requirements coverage: ", bold=True), d._run(doc.requirements_coverage)])
    if doc.health:
        h = doc.health
        d.para([d._run(f"Health Score: {h.overall}/100 — {h.band}", bold=True)])
        d.table(
            ["Component", "Score"],
            [[_EXEC_COMPONENT_LABELS.get(k, k.replace("_", " ").title()), str(h.component_scores.get(k, "—"))]
             for k in _COMPONENT_ORDER if k in h.component_scores],
        )

    d.heading(1, _SECTION_TITLES[1])
    _bullets_or_none(doc.key_kpis, "No KPIs identified.")

    d.heading(1, _SECTION_TITLES[2])
    if doc.top_risks:
        for r in doc.top_risks:
            d.bullet(_risk_line(r))
    else:
        d.para([d._run("No known risks — the latest audit found nothing to act on.", italic=True)])

    d.heading(1, _SECTION_TITLES[3])
    d.para([d._run("Data sources: ", bold=True),
           d._run(", ".join(doc.data_source_types) if doc.data_source_types else "None detected.")])
    d.para([d._run("Refresh schedule: ", bold=True), d._run(doc.refresh_schedule or "Not documented.")])
    if getattr(doc, "refresh_notes", None):
        d.para([d._run("Gateway & latency: ", bold=True), d._run(doc.refresh_notes)])
    d.para(doc.maintenance_note)

    d.heading(1, _SECTION_TITLES[4])
    ownership_rows = [["Owner", md.owner or "⚠ Not assigned"]]
    if doc.steward:
        ownership_rows.append(["Steward", doc.steward])
    if doc.classification:
        ownership_rows.append(["Classification", doc.classification])
    d.table(["Field", "Value"], ownership_rows)
    if not md.owner:
        d.para([d._run(
            "⚠ Action needed: no owner is assigned. Assign someone accountable so refresh "
            "failures and change requests have a clear point of contact.", bold=True,
        )])

    d.heading(1, _SECTION_TITLES[5])
    if doc.next_steps:
        d.table(
            ["Severity", "Action", "Effort"],
            [[s.severity, s.action, s.effort] for s in doc.next_steps],
        )
    else:
        d.para([d._run("Nothing outstanding.", italic=True)])

    d.save(out_path)
    return out_path
