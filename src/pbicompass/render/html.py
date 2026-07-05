"""Render a :class:`Document` to a self-contained, styled HTML file.

Follows the structure of a proper enterprise BI documentation template: every
section that can be extracted from the file is auto-filled; sections that require
human input are populated from user metadata or emitted as placeholders.

Builds only its own section-body HTML (Document Control through Appendix &
Sign-off) and hands it to the shared :func:`_html_shell.page_shell` — the
doctype, fonts/CSS, sidebar TOC, header card, KPI strip, and scroll-spy
script are the same scaffold every other document-type HTML renderer
(audit, executive, user-guide) uses, so a presentation change is written
once instead of once per renderer (A2-2).

Stdlib only. Opens in any browser and prints cleanly to PDF.
"""

from __future__ import annotations

import math
import re

from ..schemas.document import Document
from ._dax_highlight import highlight_dax
from ._html_shell import page_shell
from ._shared import HEALTH_COMPONENT_LABELS
from ._shared import anchor_slug
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_e as _e
from ._shared import html_table as _table
from ._shared import html_todo as _todo
from ._shared import is_local_path as _is_local_path
from ._shared import non_data_note as _non_data_note
from ._shared import slicer_field_label as _slicer_label

_FACT_FILL, _FACT_LINE = "#eef2ff", "#6366f1"
_DIM_FILL, _DIM_LINE = "#ffffff", "#cbd5e1"
_CALC_FILL, _CALC_LINE = "#f5f3ff", "#a78bfa"


def _render_md(text: str | None) -> str:
    if not text:
        return ""
    paras = text.split("\n\n")
    out = []
    for p in paras:
        p_strip = p.strip()
        if not p_strip:
            continue
        p_esc = _e(p_strip)
        p_esc = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", p_esc)
        p_esc = re.sub(r"\*(.*?)\*", r"<em>\1</em>", p_esc)
        if "Warning:" in p_esc:
            out.append(f'<div class="risk">{p_esc}</div>')
        else:
            out.append(f"<p>{p_esc}</p>")
    return "\n".join(out)


# -- model diagram ------------------------------------------------------------
def _diagram(tables: list[dict], edges: list[dict]) -> str:
    if not tables:
        return ""
    facts = [t for t in tables if t.get("kind") == "fact"]
    others = [t for t in tables if t.get("kind") != "fact"]
    bw, bh = 150, 50
    W = 880
    n = len(others)
    R = max(165, int(n * (bw + 26) / (2 * math.pi))) if n else 0
    H = max(360, 2 * (R + bh) + 90)
    cx, cy = W / 2, H / 2
    pos: dict[str, tuple[float, float]] = {}

    if len(facts) == 1:
        pos[facts[0]["name"]] = (cx, cy)
    else:
        for i, t in enumerate(facts):
            pos[t["name"]] = (cx + (i - (len(facts) - 1) / 2) * (bw + 24), cy)
    ring = others if facts else tables
    for i, t in enumerate(ring):
        ang = -math.pi / 2 + 2 * math.pi * i / max(1, len(ring))
        pos[t["name"]] = (cx + R * math.cos(ang), cy + R * math.sin(ang))
    if not facts:
        pos = {t["name"]: pos[t["name"]] for t in tables}

    table_names = ", ".join(t["name"] for t in tables)
    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="inherit" '
           f'role="img" aria-labelledby="model-diagram-title">']
    svg.append(f'<title id="model-diagram-title">Data model diagram: {_e(table_names)}, connected by '
               f'{len(edges)} relationship(s)</title>')
    svg.append('<defs><marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
               '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
               '<marker id="arro" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
               '<path d="M0,0 L7,3 L0,6 Z" fill="#d97706"/></marker></defs>')

    # edges first — grouped so hover-highlight/tooltip JS can target one edge
    # by its endpoints (data-from/data-to) without re-deriving geometry.
    for ed in edges:
        a, b = pos.get(ed.get("from")), pos.get(ed.get("to"))
        if not a or not b:
            continue
        both = ed.get("cross_filter") == "both"
        color = "#d97706" if both else "#94a3b8"
        dash = ' stroke-dasharray="5 4"' if not ed.get("is_active", True) else ""
        marker = "arro" if both else "arr"
        join = ""
        if ed.get("from_column") and ed.get("to_column"):
            join = (f'{ed["from"]}[{ed["from_column"]}] → {ed["to"]}[{ed["to_column"]}]')
        svg.append(f'<g class="dm-edge" data-from="{_e(ed.get("from"))}" data-to="{_e(ed.get("to"))}">')
        if join:
            svg.append(f"<title>{_e(join)}</title>")
        svg.append(f'<line x1="{a[0]:.0f}" y1="{a[1]:.0f}" x2="{b[0]:.0f}" y2="{b[1]:.0f}" '
                   f'stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#{marker})"/>')
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        card = f'{"∞" if ed.get("from_card")=="many" else "1"}:{"1" if ed.get("to_card")=="one" else "∞"}'
        svg.append(f'<text x="{mx:.0f}" y="{my-3:.0f}" font-size="10" fill="{color}" '
                   f'text-anchor="middle">{card}</text>')
        svg.append("</g>")

    # boxes on top — each a clickable, hoverable node keyed by table name.
    for t in tables:
        x, y = pos[t["name"]]
        kind = t.get("kind", "")
        if kind == "fact":
            fill, line = _FACT_FILL, _FACT_LINE
        elif kind in ("calculation", "calculation-group"):
            fill, line = _CALC_FILL, _CALC_LINE
        else:
            fill, line = _DIM_FILL, _DIM_LINE
        rx, ry = x - bw / 2, y - bh / 2
        name = t["name"] if len(t["name"]) <= 20 else t["name"][:19] + "…"
        sub = f'{t.get("columns",0)} col · {t.get("measures",0)} meas'
        svg.append(f'<g class="dm-node" data-table="{_e(t["name"])}">')
        svg.append(f"<title>{_e(t['name'])} — {_e(sub)} (click to jump to its row)</title>")
        svg.append(f'<rect x="{rx:.0f}" y="{ry:.0f}" width="{bw}" height="{bh}" rx="9" '
                   f'fill="{fill}" stroke="{line}" stroke-width="1.6"/>')
        svg.append(f'<text x="{x:.0f}" y="{y-4:.0f}" font-size="12.5" font-weight="600" '
                   f'text-anchor="middle" fill="#1f2933">{_e(name)}</text>')
        svg.append(f'<text x="{x:.0f}" y="{y+12:.0f}" font-size="9.5" text-anchor="middle" '
                   f'fill="#8a94a3">{_e(sub)}</text>')
        svg.append("</g>")
    svg.append("</svg>")

    legend = (
        '<div class="legend">'
        f'<span><i class="swatch" style="background:{_FACT_FILL};border:1px solid {_FACT_LINE}"></i>Fact</span>'
        f'<span><i class="swatch" style="background:{_DIM_FILL};border:1px solid {_DIM_LINE}"></i>Dimension</span>'
        f'<span><i class="swatch" style="background:{_CALC_FILL};border:1px solid {_CALC_LINE}"></i>Calculated</span>'
        '<span><i class="swatch" style="background:#94a3b8"></i>Single-direction</span>'
        '<span><i class="swatch" style="background:#d97706"></i>Bi-directional</span>'
        '<span>– – – inactive</span></div>'
        '<p class="muted diagram-hint">Scroll/pinch to zoom, drag to pan. '
        'Hover a table to highlight its relationships; click it to jump to its row below.</p>'
    )
    return f'<div class="diagram">{"".join(svg)}</div>{legend}'


# -- main ---------------------------------------------------------------------
def render_html(
    doc: Document, *,
    doc_links: list[tuple[str, str]] | None = None,
    sibling_hrefs: dict[str, str] | None = None,
) -> str:
    md = doc.metadata
    s = doc.stats

    TOC = [
        ("sec1", "Document Control"),
        ("sec2", "Executive Summary"),
        ("sec3", "Business Requirements"),
        ("sec4", "Audience & Stakeholders"),
        ("sec5", "Data Sources"),
        ("sec6", "Data Model"),
        ("sec7", "Measures (DAX)"),
        ("sec8", "Pages & Visuals"),
        ("sec9", "Filters & Navigation"),
        ("sec10", "Row-Level Security"),
        ("sec11", "Refresh & Gateway"),
        ("sec12", "Deployment & Environments"),
        ("sec13", "Access & Permissions"),
        ("sec14", "Glossary"),
        ("sec15", "Issues & Assumptions"),
        ("sec16", "Health & AI Recommendations"),
        ("sec17", "Support & Maintenance"),
        ("sec18", "Appendix & Sign-off"),
    ]

    kpis = [
        (label, s.get(key, 0))
        for label, key in (("Tables", "tables"), ("Columns", "columns"), ("Measures", "measures"),
                          ("Relationships", "relationships"), ("Pages", "pages"),
                          ("Visuals", "visuals"), ("Sources", "data_sources"))
    ]

    o: list[str] = []

    # 1. Document Control
    o.append('<h2 id="sec1">1. Document Control</h2>')
    doc_control = [
        ["Dashboard / Report Name", _e(md.report_name)],
        ["Source format", _e(md.source_format or "unknown")],
        ["Owner", _e(md.owner) if md.owner else '<span class="muted">not specified</span>'],
        ["Author", _e(md.author) if md.author else '<span class="muted">not specified</span>'],
        ["Reviewer / Approver", _e(md.reviewer) if md.reviewer else '<span class="muted">not specified</span>'],
        ["Version", _e(md.version) if md.version else '<span class="muted">not specified</span>'],
        ["Status", _e(md.status) if md.status else '<span class="muted">not specified</span>'],
        ["Classification", _e(md.classification) if md.classification else '<span class="muted">not specified</span>'],
        ["Target audience", _e(md.target_audience or "")],
        ["Refresh schedule", _e(md.refresh_schedule) if md.refresh_schedule else '<span class="muted">not specified</span>'],
        ["Generated", _e(_fmt_ts(md.generated_at))],
    ]
    o.append(_table(["Field", "Value"], doc_control))
    
    missing_doc_control = [f for f, v in [("Version", md.version), ("Status", md.status), ("Author", md.author), 
                                          ("Reviewer", md.reviewer), ("Classification", md.classification)] if not v]
    if missing_doc_control:
        o.append(_todo(f"Complete missing document control fields: {', '.join(missing_doc_control)}"))

    # 2. Executive Summary
    es = doc.executive_summary
    o.append('<h2 id="sec2">2. Executive Summary</h2>')
    o.append('<div class="card-section">')
    o.append(_render_md(es.core_purpose))
    
    if md.business_decision:
        o.append('<h3>Primary Business Decision / Impact</h3>')
        o.append(f'<p>{_e(md.business_decision)}</p>')
    
    headline = [m.name for m in doc.measure_catalog.measures][:6]
    if headline:
        o.append("<p><strong>Headline metrics:</strong> " + ", ".join(_e(h) for h in headline) + ".</p>")
    o.append('</div>')
    
    if not md.business_decision:
        o.append(_todo("Specify the primary business decision this dashboard drives (e.g. weekly sales planning)."))

    # 3. Business Requirements
    o.append('<h2 id="sec3">3. Business Requirements</h2>')
    if md.requirements:
        o.append('<div class="card-section">')
        for req in md.requirements.split('\n'):
            if req.strip():
                o.append(f"<p>{_e(req)}</p>")
        o.append('</div>')
    else:
        req_rows = [
            [_e(r["id"]), _e(r["requirement"]), _e(r["source"]), _e(r["priority"]), _e(r["status"])]
            for r in doc.inferred_requirements
        ]
        o.append(_table(["ID", "Inferred Requirement", "Source Visual", "Priority", "Status"], req_rows))

    # 4. Audience & Stakeholders
    o.append('<h2 id="sec4">4. Audience &amp; Stakeholders</h2>')
    o.append(_table(["Role", "Name / Group", "Access"], [
        ["Business Owner", _e(md.owner) if md.owner else '<span class="muted">—</span>', "Edit / sign-off"],
        ["Primary Users", _e(md.target_audience) if md.target_audience else '<span class="muted">—</span>', "View"],
        ["Author / Creator", _e(md.author) if md.author else '<span class="muted">—</span>', "Modify / Publish"],
    ]))
    o.append(_todo("Confirm other stakeholders (Data Owner, Developer/Maintainer, and per-group access levels)."))

    # 5. Data Sources
    ln = doc.lineage
    o.append('<h2 id="sec5">5. Data Sources</h2>')
    if ln.source_systems:
        list_items = []
        for x in ln.source_systems:
            if _is_local_path(x):
                list_items.append(f'<li>{_e(x)} <span class="alert-inline alert-danger">⚠️ Hardcoded local path</span></li>')
            else:
                list_items.append(f'<li>{_e(x)}</li>')
        o.append("<ul>" + "".join(list_items) + "</ul>")
    else:
        o.append('<p class="muted">No external data sources detected.</p>')
    o.append("<h3>Power Query / ETL transformations</h3>")
    o.append(_table(["Object", "Transformation"],
                    [[_e(t.get("name", "")), _e(t.get("description", ""))] for t in ln.transformations],
                    "No Power Query transformations found."))
    o.append(_todo("Specify per-source authentication method, owning team, and known data latency."))

    # 6. Data Model
    sm = doc.semantic_model
    o.append('<h2 id="sec6">6. Data Model</h2>')
    for para in sm.summary.split("\n\n"):
        o.append(f"<p>{_e(para)}</p>")
    if sm.risks:
        o.append("<h3>Relationships of note &amp; risks</h3>")
        for r in sm.risks:
            o.append(f'<div class="risk">{_e(r)}</div>')
    if sm.tables:
        o.append("<h3>Model diagram</h3>")
        o.append(_diagram(sm.tables, sm.relationship_edges))
    o.append("<h3>Key tables</h3>")
    if sm.tables:
        head = "".join(f"<th>{h}</th>" for h in ("Table", "Type", "Columns", "Measures"))
        rows = "".join(
            f'<tr id="table-{_e(anchor_slug(t["name"]))}"><td>{_e(t["name"])}</td><td>{_e(t.get("kind", ""))}</td>'
            f'<td><span class="num">{_e(t.get("columns", 0))}</span></td>'
            f'<td><span class="num">{_e(t.get("measures", 0))}</span></td></tr>'
            for t in sm.tables
        )
        o.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
    else:
        o.append('<p class="muted">None.</p>')
    o.append("<h3>Relationships</h3>")
    o.append(_table(["From", "To", "Cardinality", "Cross-filter", "Active"],
                    [[_e(ed["from"]), _e(ed["to"]),
                      _e(f'{ed.get("from_card")}-to-{ed.get("to_card")}'),
                      _e(ed.get("cross_filter")), "Yes" if ed.get("is_active") else "No"]
                     for ed in sm.relationship_edges], "No relationships defined."))
    o.append("<h3>Data dictionary</h3>")
    o.append(_table(["Table", "Column", "Data Type", "Description"],
                    [[_e(r.get("table", "")), _e(r.get("column", "")), _e(r.get("data_type", "")),
                      _e(r.get("description", ""))] for r in sm.data_dictionary]))

    # 7. Measures & Calculations (DAX Dictionary)
    o.append('<h2 id="sec7">7. Measures &amp; Calculations (DAX Dictionary)</h2>')
    for m in doc.measure_catalog.measures:
        home = f" · {_e(m.table)}" if m.table else ""
        cat = f'<span class="pill">{_e(m.category)}</span>' if m.category else ""
        o.append(f'<div class="measure" id="measure-{_e(anchor_slug(m.name))}">')
        o.append(f"<h3>{_e(m.name)}{home}{cat}</h3>")
        o.append(f"<p>{_e(m.plain_english)}</p>")
        if m.calculation_logic and m.calculation_logic != m.plain_english:
            o.append(f"<p><strong>Calculation:</strong> {_e(m.calculation_logic)}</p>")
        if m.caveats:
            o.append(f'<p class="caveat">Known caveats: {_e(m.caveats)}</p>')
        if m.dependencies:
            o.append(f'<p class="usedon">Depends on: {_e(", ".join(m.dependencies))}</p>')
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        fmt = f" · format <code>{_e(m.format_string)}</code>" if m.format_string else ""
        o.append(f'<p class="usedon">Used on: {_e(used)}{fmt}</p>')
        if m.confidence and m.confidence != "High":
            suffix = " — review with the business owner" if m.confidence == "Low" else ""
            o.append(f'<p class="caveat">Confidence in inferred business meaning: {_e(m.confidence)}{_e(suffix)}.</p>')
        code_block = ('<div class="code-block">'
                     '<button type="button" class="copy-btn">Copy</button>'
                     f"<pre><code>{highlight_dax(m.dax)}</code></pre></div>")
        line_count = (m.dax or "").count("\n") + 1
        if line_count > 10:
            o.append(f'<details class="collapsible"><summary>{_e(m.name)} — '
                     f"{line_count} lines (click to expand)</summary>{code_block}</details>")
        else:
            o.append(code_block)
        o.append("</div>")
    if doc.calculated_columns:
        o.append("<h3>Calculated columns</h3>")
        o.append(_table(["Table", "Column", "Expression"],
                        [[_e(c.get("table", "")), _e(c.get("column", "")),
                          f'<code>{highlight_dax(c.get("expression", ""))}</code>'] for c in doc.calculated_columns]))

    # 8. Report Pages & Visuals
    o.append('<h2 id="sec8">8. Report Pages &amp; Visuals</h2>')
    if es.complex_visual_explainers:
        o.append("<h3>How to read the key visuals</h3><ul>")
        for ex in es.complex_visual_explainers:
            o.append(f"<li><strong>{_e(ex.visual)}</strong> ({_e(ex.page)}): {_e(ex.how_to_read)}</li>")
        o.append("</ul>")
    page_docs = {p.page_title: p for p in es.pages}
    for p in doc.report_pages:
        flags = []
        if p.get("hidden"):
            flags.append("hidden")
        if p.get("drillthrough"):
            flags.append("drill-through")
        flag = f' <span class="muted">({", ".join(flags)})</span>' if flags else ""
        o.append(f"<h3>{_e(p['name'])}{flag}</h3>")
        pd = page_docs.get(p["name"])
        if pd:
            if pd.summary:
                o.append(f"<p>{_e(pd.summary)}</p>")
            if pd.users:
                o.append(f"<p><strong>Who uses it:</strong> {_e(pd.users)}</p>")
            if pd.business_questions:
                o.append("<p><strong>Business questions answered:</strong></p><ul>"
                         + "".join(f"<li>{_e(q)}</li>" for q in pd.business_questions) + "</ul>")
            if pd.decisions:
                o.append(f"<p><strong>Decision supported:</strong> {_e(pd.decisions)}</p>")
            if pd.confidence == "Low":
                o.append('<p class="caveat">Purpose inferred with low confidence — requires business review.</p>')
        rows = [[_e(v.get("label") or "—"), _e(v.get("type")),
                 _e(", ".join(v.get("metrics", [])) or "—"),
                 _e(", ".join(v.get("dimensions", [])) or "—")] for v in p.get("visuals", [])]
        o.append(_table(["Visual", "Type", "Metric(s)", "Dimension(s)"], rows, "No data visuals on this page."))
        if p.get("decorative_count"):
            o.append(f'<p class="muted">{_e(_non_data_note(p["decorative_count"]))}</p>')

    # 9. Filters, Slicers & Navigation
    o.append('<h2 id="sec9">9. Filters, Slicers &amp; Navigation</h2>')
    if es.navigation_guide:
        o.append("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in es.navigation_guide) + "</ul>")
    o.append(_table(["Slicer field", "Page"],
                    [[_e(_slicer_label(s_)), _e(s_["page"])] for s_ in doc.slicers],
                    "No slicers found."))
    drill = [p["name"] for p in doc.report_pages if p.get("drillthrough")]
    if drill:
        o.append("<p><strong>Drill-through pages:</strong> " + ", ".join(_e(d) for d in drill) + ".</p>")
    o.append(_todo("Detail bookmarks, button navigation logic, and the fields passed on each drill-through."))

    # 10. Row-Level Security
    sec = doc.security
    o.append('<h2 id="sec10">10. Row-Level Security (RLS)</h2>')
    if sec.roles:
        rows = []
        for r in sec.roles:
            rules = "<br>".join(f"<code>{highlight_dax(f)}</code>" for f in r.get("filters", [])) or '<span class="muted">—</span>'
            members = ", ".join(r.get("members", [])) or '<span class="muted">—</span>'
            rows.append([_e(r.get("name", "")), rules, members])
        o.append(_table(["Role", "Rule (DAX filter)", "Members"], rows))
    else:
        o.append('<p class="muted">No row-level security roles are defined in this model.</p>')
        
    if md.security_notes:
        o.append('<h3>Security Validation &amp; Scope</h3>')
        o.append(f'<p>{_e(md.security_notes)}</p>')
    else:
        o.append(_todo("Confirm each role was tested with 'View as role', and note any object-level security (OLS) rules."))

    # 11. Refresh, Gateway & Performance
    o.append('<h2 id="sec11">11. Refresh, Gateway &amp; Performance</h2>')
    if md.refresh_schedule:
        o.append(f"<p><strong>Refresh schedule:</strong> {_e(md.refresh_schedule)}.</p>")
    if md.refresh_notes:
        o.append('<div class="card-section">')
        for line in md.refresh_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        # Render placeholder table (Fix 8)
        placeholder_rows = [
            ["Refresh Type", '<span class="todo">✎ To complete</span>'],
            ["Gateway Name", '<span class="todo">✎ To complete</span>'],
            ["Typical Duration", '<span class="todo">✎ To complete</span>'],
            ["Dataset Size", '<span class="todo">✎ To complete</span>'],
            ["Failure Alert Contact", '<span class="todo">✎ To complete</span>'],
        ]
        o.append(_table(["Field", "Value / Status"], placeholder_rows))
        o.append(_todo("Detail performance considerations and gateway configurations."))

    # 12. Deployment & Environment
    o.append('<h2 id="sec12">12. Deployment &amp; Environment</h2>')
    if md.deployment_notes:
        o.append('<div class="card-section">')
        for line in md.deployment_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Dev / Test / Production workspaces, app URLs, deployment method (pipeline/Git), and per-environment parameters."))

    # 13. Access & Permissions
    o.append('<h2 id="sec13">13. Access &amp; Permissions</h2>')
    if md.access_notes:
        o.append('<div class="card-section">')
        for line in md.access_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Workspace roles and app access per group, with justification."))

    # 14. Glossary
    o.append('<h2 id="sec14">14. Data Dictionary / Glossary</h2>')
    o.append('<p class="muted">Column-level data dictionary is in section 6. Business-term definitions belong below.</p>')
    if md.glossary:
        o.append('<div class="card-section">')
        for line in md.glossary.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        # Render auto-inferred glossary table (Fix 6)
        glossary_rows = []
        for r in doc.glossary_entries:
            typo = f'<span class="alert-inline alert-danger">⚠️ {r["typo_flag"]}</span>' if r["typo_flag"] else '<span class="muted">—</span>'
            glossary_rows.append([
                f'<code>{_e(r["term"])}</code>',
                _e(r["type"]),
                _e(r["definition"]),
                typo
            ])
        o.append(_table(["Term", "Type", "Plain-English Definition", "Typo / Rename Flag"], glossary_rows))

    # 15. Known Issues, Assumptions & Limitations
    td = doc.tech_debt
    o.append('<h2 id="sec15">15. Known Issues, Assumptions &amp; Limitations</h2>')
    for n in td.notes:
        o.append(f"<p>{_e(n)}</p>")
    if md.assumptions:
        o.append('<h3>Business Assumptions &amp; Limitations</h3>')
        o.append(f'<p>{_e(md.assumptions)}</p>')
    if td.orphaned_measures:
        o.append("<h3>Orphaned measures (defined but not used on any page)</h3>")
        o.append("<ul>" + "".join(f"<li>{_e(m)}</li>" for m in td.orphaned_measures) + "</ul>")
    if sm.risks:
        o.append("<h3>Modeling risks</h3>")
        o.append("<ul>" + "".join(f"<li>{_e(r)}</li>" for r in sm.risks) + "</ul>")
    if not md.assumptions:
        o.append(_todo("Business assumptions and limitations (e.g. \"returns lag source by 1 day\") with impact and workaround."))

    # 16. Model Health & AI Recommendations
    o.append('<h2 id="sec16">16. Model Health &amp; AI Recommendations</h2>')
    audit_href = (sibling_hrefs or {}).get("audit")
    if audit_href:
        o.append(f'<p class="muted">Same deterministic rule engine as the full '
                 f'<a href="{_e(audit_href)}">Audit &amp; Health Report</a> for this model.</p>')
    hs = doc.health_score or {}
    if hs:
        o.append(f'<h3>Health Score: {_e(hs.get("overall", 0))} / 100 ({_e(hs.get("band", ""))})</h3>')
        notes = hs.get("component_notes", {})
        o.append(_table(["Component", "Score", "Why"],
                        [[_e(HEALTH_COMPONENT_LABELS.get(k, k)), f'<span class="num">{_e(v)}</span>',
                          _e(notes.get(k, ""))]
                         for k, v in hs.get("component_scores", {}).items()]))
        o.append('<p class="muted">Scored by deterministic rules over the model metadata — reproducible, not an AI guess.</p>')
    recs = doc.ai_recommendations or []
    if recs:
        o.append("<h3>Prioritized recommendations</h3>")
        for i, r in enumerate(recs, 1):
            o.append('<div class="measure">')
            o.append(f'<h3>{i}. <span class="pill">{_e(r.get("priority", "Medium"))}</span> {_e(r.get("issue", ""))}</h3>')
            o.append(f'<p><strong>Impact:</strong> {_e(r.get("why_it_matters", ""))}</p>')
            o.append(f'<p><strong>Recommendation:</strong> {_e(r.get("suggested_fix", ""))}</p>')
            if r.get("expected_benefit"):
                o.append(f'<p><strong>Expected benefit:</strong> {_e(r.get("expected_benefit"))}</p>')
            o.append(f'<p><strong>Estimated effort:</strong> {_e(r.get("effort", "Medium"))}</p>')
            o.append("</div>")
    elif hs:
        o.append('<p class="muted">No recommendations — no findings were raised against this model.</p>')
    if not hs and not recs:
        o.append('<p class="muted">Not computed for this document.</p>')

    # 17. Support & Maintenance
    o.append('<h2 id="sec17">17. Support &amp; Maintenance</h2>')
    if md.owner:
        o.append(f"<p><strong>First-line contact:</strong> {_e(md.owner)}.</p>")
    if md.support_notes:
        o.append('<div class="card-section">')
        for line in md.support_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Escalation contact, SLA for fixes, .pbix/.pbip backup location, and decommission criteria."))

    # 18. Appendix & Sign-off
    o.append('<h2 id="sec18">18. Appendix &amp; Sign-off</h2>')
    o.append("<p>The model diagram is in section 6. Attach wireframes/mockups and any source-to-target mapping here.</p>")
    
    developer_name = _e(md.owner) if md.owner else "TBC"
    generated_date = _e((md.generated_at or "")[:10])
    sign_off_rows = [
        ["Business Owner", "", "", ""],
        ["Data Owner", "", "", ""],
        ["Developer", developer_name, "BI Developer", generated_date],
    ]
    o.append(_table(["Sign-off Role", "Name", "Title / Role", "Date"], sign_off_rows))
    o.append('<p class="caveat"><strong>Reminder:</strong> Obtain sign-off before sharing with stakeholders.</p>')

    search_index = [{"title": title, "type": "section", "anchor": sec_id} for sec_id, title in TOC]
    search_index += [
        {"title": m.name, "type": "measure", "anchor": f"measure-{anchor_slug(m.name)}"}
        for m in doc.measure_catalog.measures
    ]
    search_index += [
        {"title": t["name"], "type": "table", "anchor": f"table-{anchor_slug(t['name'])}"}
        for t in sm.tables
    ]

    return page_shell(
        title=md.report_name,
        subtitle=f'{md.target_audience or "BI developers and business stakeholders"} · generated {_fmt_ts(md.generated_at)}',
        toc=TOC, kpis=kpis, body_html="\n".join(o), search_index=search_index, doc_links=doc_links,
        owner=md.owner, version=md.version, status=md.status, classification=md.classification,
    )
