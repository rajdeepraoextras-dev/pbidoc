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

from ..agents.audit_rules import TOTAL_RULE_COUNT
from ..schemas.document import Document
from ._dax_highlight import highlight_dax
from ._html_shell import page_shell
from ._shared import HEALTH_COMPONENT_LABELS
from ._shared import MODEL_DIAGRAM_RENDERED
from ._shared import anchor_slug
from ._shared import pluralize_count
from ._shared import dedupe_ids
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_discrepancy_callout
from ._shared import html_e as _e
from ._shared import html_table as _table
from ._shared import html_todo as _todo
from ._shared import is_local_path as _is_local_path
from ._shared import non_data_note as _non_data_note
from ._shared import section_provenance
from ._shared import slicer_field_label as _slicer_label

_FACT_FILL, _FACT_LINE = "#eef2fd", "#124fed"
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


# Day 4: Requirements Traceability status -> the same pill color language
# audit.py's best-practice pass/fail pills already use (green/amber/red),
# so "Covered"/"Partial"/"Gap" reads consistently with the rest of the bundle.
_STATUS_PILL_CLASS = {"Covered": "pass", "Partial": "high", "Gap": "fail"}


def _status_pill(status: str) -> str:
    return f'<span class="pill {_STATUS_PILL_CLASS.get(status, "low")}">{_e(status)}</span>'


def format_prose_with_code(text: str) -> str:
    if not text:
        return ""
    parts = text.split("```")
    o = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            o.append(_e(part).replace("\n", "<br>"))
        else:
            lines = part.split("\n")
            lang = "csharp"
            code_lines = lines
            if lines and lines[0].strip() in ("csharp", "dax", "powerquery", "pq", "m", "text"):
                lang = lines[0].strip()
                code_lines = lines[1:]
            code_text = "\n".join(code_lines)
            
            if lang == "dax":
                highlighted = highlight_dax(code_text)
                o.append(f'<div style="margin: 8px 0; position: relative;"><pre><code>{highlighted}</code></pre></div>')
            else:
                o.append(f'<div style="margin: 8px 0; position: relative;"><pre><code>{_e(code_text)}</code></pre></div>')
    return "".join(o)


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
    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" aria-labelledby="model-diagram-title">\n<style>text {{ font-family: "Poppins", sans-serif !important; }}</style>']
    svg.append(f'<title id="model-diagram-title">Data model diagram: {_e(table_names)}, connected by '
               f'{_e(pluralize_count("relationship", len(edges)))}</title>')
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
                   f'text-anchor="middle" fill="#1f2933">WIP</text>')
        svg.append(f'<text x="{x:.0f}" y="{y+12:.0f}" font-size="9.5" text-anchor="middle" '
                   f'fill="#8a94a3">WIP</text>')
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
        ("sec19", "Methodology & Guarantees"),
    ]

    kpis = [
        (label, s.get(key, 0))
        for label, key in (("Tables", "tables"), ("Columns", "columns"), ("Measures", "measures"),
                          ("Relationships", "relationships"), ("Pages", "pages"),
                          ("Visuals", "visuals"), ("Sources", "data_sources"))
    ]

    o: list[str] = []

    def _header_badge(section_num: int) -> str:
        prov = section_provenance(section_num, md)
        cls = "extracted"
        if prov == "Human-provided":
            cls = "human-provided"
        elif prov == "AI-inferred":
            cls = "ai-inferred"
        return f' <span class="pill {cls}">{prov}</span>'

    # 1. Document Control
    o.append(f'<h2 id="sec1">1. Document Control{_header_badge(1)}</h2>')
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

    if getattr(doc, "changelog", None):
        o.append('<h3>Changes since last documentation</h3>')
        o.append(f'<div class="card-section">{_render_md(doc.changelog)}</div>')
    
    missing_doc_control = [f for f, v in [("Version", md.version), ("Status", md.status), ("Author", md.author), 
                                          ("Reviewer", md.reviewer), ("Classification", md.classification)] if not v]
    if missing_doc_control:
        o.append(_todo(f"Complete missing document control fields: {', '.join(missing_doc_control)}"))

    # 2. Executive Summary
    es = doc.executive_summary
    o.append(f'<h2 id="sec2">2. Executive Summary{_header_badge(2)}</h2>')
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

    # 3. Business Requirements. requirements_matrix (Day 4) supersedes the
    # plain text dump whenever it's non-empty — a RAG table with working
    # evidence links is strictly more useful than an echoed list, and is
    # only ever empty when requirements weren't parseable in the first place.
    o.append(f'<h2 id="sec3">3. Business Requirements{_header_badge(3)}</h2>')
    if doc.requirements_matrix:
        covered = sum(1 for r in doc.requirements_matrix if r["status"] in ("Covered", "Partial"))
        o.append(f'<p class="muted">Requirements Traceability Matrix — {covered}/{len(doc.requirements_matrix)} '
                 f'at least partially covered by the report\'s own measures, columns, and pages.</p>')
        rag_rows = []
        for r in doc.requirements_matrix:
            evidence_html = ", ".join(
                f'<a href="#{_e(e["anchor"])}">{_e(e["name"])}</a>' for e in r.get("evidence", [])
            ) or '<span class="muted">—</span>'
            rag_rows.append([
                _e(r.get("priority") or "—"), _e(r["text"]), _status_pill(r["status"]), evidence_html,
            ])
        o.append(_table(["Priority", "Requirement", "Status", "Evidence"], rag_rows))
    elif md.requirements:
        o.append('<div class="card-section">')
        for req in md.requirements.split('\n'):
            if req.strip():
                o.append(f"<p>{_e(req)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Business requirements have not yet been captured; confirm scope with the business owner."))

    # 4. Audience & Stakeholders
    o.append(f'<h2 id="sec4">4. Audience &amp; Stakeholders{_header_badge(4)}</h2>')
    o.append(_table(["Role", "Name / Group", "Access"], [
        ["Business Owner", _e(md.owner) if md.owner else '<span class="muted">—</span>', "Edit / sign-off"],
        ["Primary Users", _e(md.target_audience) if md.target_audience else '<span class="muted">—</span>', "View"],
        ["Author / Creator", _e(md.author) if md.author else '<span class="muted">—</span>', "Modify / Publish"],
    ]))
    o.append(_todo("Confirm other stakeholders (Data Owner, Developer/Maintainer, and per-group access levels)."))

    # 5. Data Sources
    ln = doc.lineage
    o.append(f'<h2 id="sec5">5. Data Sources{_header_badge(5)}</h2>')
    if ln.data_sources_inventory:
        # Per-source row anchors, derived from the same label the lineage
        # graph names each source with — its source cards deep-link here.
        src_row_ids = dedupe_ids([f"source-{anchor_slug(item.get('label') or item.get('location') or item['type'])}"
                                  for item in ln.data_sources_inventory])
        rows = []
        for item in ln.data_sources_inventory:
            type_cell = f"<code>{_e(item['type'])}</code>"
            full_path = item["location"]
            if item["flag"]:
                loc_cell = f'<span class="alert-inline alert-danger" title="{_e(full_path)}">{_e(item["display_location"])}</span>'
            else:
                loc_cell = f'<span title="{_e(full_path)}">{_e(item["display_location"])}</span>'
                
            fed_cell = ", ".join(_e(t) for t in item["tables_fed"]) or '<span class="muted">—</span>'
            mode_cell = f"<code>{_e(item['storage_mode'])}</code>"
            auth_cell = f"<code>{_e(item['auth'])}</code>"
            flag_cell = f'<span class="alert-inline alert-danger">{_e(item["flag"])}</span>' if item["flag"] else '<span class="muted">None</span>'
            
            rows.append([type_cell, loc_cell, fed_cell, mode_cell, auth_cell, flag_cell])
            
        o.append(_table(["Source Type", "Location / Host", "Table(s) Fed", "Storage Mode", "Authentication", "Flag / Risk"], rows, row_ids=src_row_ids))
    else:
        o.append('<p class="muted">No external data sources detected.</p>')
    o.append("<h3>Power Query / ETL transformations</h3>")
    if not ln.transformations:
        o.append('<p class="muted">No Power Query transformations found.</p>')
    else:
        for t in ln.transformations:
            o.append(f'<div class="transformation-block" style="margin-bottom:1.5rem;">')
            o.append(f'<h4>{_e(t.get("name", ""))}</h4>')
            o.append(f'<p>{_e(t.get("description", ""))}</p>')
            
            steps = t.get("steps")
            if steps:
                o.append('<ol class="transformation-steps" style="margin-bottom:0.5rem;">')
                for s in steps:
                    o.append(f'<li style="margin-bottom:0.4rem;"><strong>{_e(s.get("step"))}</strong> — <i>{_e(s.get("type"))}</i><br>'
                             f'<code style="word-break:break-all;">{_e(s.get("expr"))}</code></li>')
                o.append('</ol>')
            
            raw_m = t.get("raw_m")
            if raw_m:
                o.append(f'<details class="collapsible"><summary>Full M Query Script</summary>'
                         f'<pre><code>{_e(raw_m)}</code></pre></details>')
            o.append('</div>')
    if ln.lineage_svg:
        o.append("<h3>Data lineage graph</h3>")
        o.append(ln.lineage_svg)
    if ln.lineage_edges:
        o.append("<h3>Lineage connection list</h3>")
        o.append(_table(["From", "To", "Link Type"],
                        [[_e(ed["from"]), _e(ed["to"]), _e(ed["type"])] for ed in ln.lineage_edges]))
    o.append(_todo("Specify per-source authentication method, owning team, and known data latency."))

    # 6. Data Model
    sm = doc.semantic_model
    o.append(f'<h2 id="sec6">6. Data Model{_header_badge(6)}</h2>')
    for para in sm.summary.split("\n\n"):
        o.append(f"<p>{_e(para)}</p>")
    if sm.risks:
        o.append("<h3>Relationships of note &amp; risks</h3>")
        for r in sm.risks:
            o.append(f'<div class="risk">{_e(r)}</div>')
    if MODEL_DIAGRAM_RENDERED and sm.tables:
        o.append("<h3>Model diagram</h3>")
        o.append(_diagram(sm.tables, sm.relationship_edges))
        pass
    o.append("<h3>Key tables</h3>")
    table_ids = dedupe_ids([f"table-{anchor_slug(t['name'])}" for t in sm.tables])
    if sm.tables:
        head = "".join(f"<th>{h}</th>" for h in ("Table", "Type", "Columns", "Measures"))
        rows = "".join(
            f'<tr id="{_e(tid)}"><td>{_e(t["name"])}</td><td>{_e(t.get("kind", ""))}</td>'
            f'<td><span class="num">{_e(t.get("columns", 0))}</span></td>'
            f'<td><span class="num">{_e(t.get("measures", 0))}</span></td></tr>'
            for t, tid in zip(sm.tables, table_ids)
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
    row_ids = dedupe_ids([f"column-{anchor_slug(r.get('table', ''))}-{anchor_slug(r.get('column', ''))}"
                          for r in sm.data_dictionary])
    rows = [[_e(r.get("table", "")), _e(r.get("column", "")), _e(r.get("data_type", "")),
              _e(r.get("description", "")), _e(r.get("used_by", ""))] for r in sm.data_dictionary]
    o.append(_table(["Table", "Column", "Data Type", "Description", "Used by"], rows, row_ids=row_ids))

    # 7. Measures & Calculations (DAX Dictionary)
    o.append(f'<h2 id="sec7">7. Measures &amp; Calculations (DAX Dictionary){_header_badge(7)}</h2>')
    if doc.measure_catalog.dependency_svg:
        # o.append("<h3>Measure dependency graph</h3>")
        # o.append(doc.measure_catalog.dependency_svg)
        pass
    for m in doc.measure_catalog.measures:
        cat = f'<span class="pill">{_e(m.category)}</span>' if m.category else ""
        o.append(f'<div class="measure" id="measure-{_e(anchor_slug(m.name))}">')
        o.append(f"<h3>{_e(m.name)}{cat}</h3>")
        operates_on = [t for t in (m.operates_on or []) if t and t != m.table]
        if m.table:
            line = f'Home table: <strong>{_e(m.table)}</strong>'
            if operates_on:
                line += f' · Operates on: {_e(", ".join(operates_on))}'
            o.append(f'<p class="usedon">{line}</p>')
        o.append(f"<p>{_e(m.plain_english)}</p>")
        if m.calculation_logic and m.calculation_logic != m.plain_english:
            o.append(f"<p><strong>Calculation:</strong> {_e(m.calculation_logic)}</p>")
        if m.caveats:
            o.append(f'<p class="caveat">Known caveats: {_e(m.caveats)}</p>')
        if m.dependency_tree:
            o.append(f'<p class="usedon"><strong>Dependency tree:</strong></p><pre class="dep-tree"><code>{_e(m.dependency_tree)}</code></pre>')
        elif m.dependencies:
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
    o.append(f'<h2 id="sec8">8. Report Pages &amp; Visuals{_header_badge(8)}</h2>')
    if es.complex_visual_explainers:
        o.append("<h3>How to read the key visuals</h3><ul>")
        for ex in es.complex_visual_explainers:
            o.append(f"<li><strong>{_e(ex.visual)}</strong> ({_e(ex.page)}): {_e(ex.how_to_read)}</li>")
        o.append("</ul>")
    page_docs = {p.page_title: p for p in es.pages}
    # Matches user_guide.py's per-page card id (same formula) — the page
    # wireframe SVG is computed once (report_facts.report_pages) and
    # embedded verbatim in both documents, and its slicer boxes link here
    # (I3), so both renderers need the identical anchor.
    page_ids = dedupe_ids([f"page-{anchor_slug(p['name'])}" for p in doc.report_pages])
    for p, page_id in zip(doc.report_pages, page_ids):
        o.append(f'<div id="{_e(page_id)}">')
        flags = []
        if p.get("hidden"):
            flags.append("hidden")
        if p.get("drillthrough"):
            flags.append("drill-through")
        flag = f' <span class="muted">({", ".join(flags)})</span>' if flags else ""
        o.append(f"<h3>{_e(p['name'])}{flag}</h3>")
        if p.get("wireframe_svg"):
            o.append(p["wireframe_svg"])
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
        row_ids = dedupe_ids([f"visual-{anchor_slug(p['name'])}-{anchor_slug(v['label'])}"
                             for v in p.get("visuals", [])])
        rows = [[_e(v.get("label") or "—"), _e(v.get("type")),
                 _e(", ".join(v.get("metrics", [])) or "—"),
                 _e(", ".join(v.get("dimensions", [])) or "—")] for v in p.get("visuals", [])]
        o.append(_table(["Visual", "Type", "Metric(s)", "Dimension(s)"], rows, "No data visuals on this page.", row_ids=row_ids))
        if p.get("decorative_count"):
            o.append(f'<p class="muted">{_e(_non_data_note(p["decorative_count"]))}</p>')
        o.append("</div>")

    # 9. Filters, Slicers & Navigation
    o.append(f'<h2 id="sec9">9. Filters, Slicers &amp; Navigation{_header_badge(9)}</h2>')
    if doc.navigation_map_svg:
        # o.append("<h3>Page Navigation Map</h3>")
        # o.append(doc.navigation_map_svg)
        pass
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
    o.append(f'<h2 id="sec10">10. Row-Level Security (RLS){_header_badge(10)}</h2>')
    if getattr(sec, "discrepancies", None):
        o.append(html_discrepancy_callout(sec.discrepancies))
    if sec.roles:
        o.append("<h3>Roles definition</h3>")
        meta_rows = []
        for r in sec.roles:
            m = ", ".join(r.get("members", [])) or '<span class="muted">no members assigned (managed in cloud service)</span>'
            meta_rows.append([_e(r["name"]), _e(r["model_permission"]), m])
        o.append(_table(["Role Name", "Permission", "Members"], meta_rows))
        
        o.append("<h3>Role × Table security matrix</h3>")
        filtered_tables = sorted(list({
            filt.split(":")[0].strip()
            for r in sec.roles
            for filt in r.get("filters", [])
            if ":" in filt
        }))
        if not filtered_tables:
            o.append('<p class="muted">No table-level filters are defined for these roles.</p>')
        else:
            grid_headers = ["Table"] + [_e(r["name"]) for r in sec.roles]
            grid_rows = []
            for t_name in filtered_tables:
                row = [_e(t_name)]
                for r in sec.roles:
                    filt_val = "—"
                    for filt in r.get("filters", []):
                        if filt.startswith(f"{t_name}:"):
                            filt_val = filt.split(":", 1)[1].strip()
                            break
                    row.append(f"<code>{highlight_dax(filt_val)}</code>" if filt_val != "—" else '<span class="muted">—</span>')
                grid_rows.append(row)
            o.append(_table(grid_headers, grid_rows))
            
        o.append("<h3>RLS Validation Checklist</h3>")
        o.append("<ul>")
        for r in sec.roles:
            o.append(f"<li>[ ] <strong>Test {r['name']}:</strong> Select 'View as' &rarr; check '{r['name']}' in Power BI Desktop to verify filter propagation.</li>")
        o.append("</ul>")
    else:
        o.append('<p class="muted">No row-level security roles are defined in this model. See the security audit recommendation in the Audit &amp; Health Report.</p>')
        
    if md.security_notes:
        o.append('<h3>Security Validation &amp; Scope</h3>')
        o.append(f'<p>{_e(md.security_notes)}</p>')
    else:
        o.append(_todo("Confirm each role was tested with 'View as role', and note any object-level security (OLS) rules."))

    # 11. Refresh, Gateway & Performance
    o.append(f'<h2 id="sec11">11. Refresh, Gateway &amp; Performance{_header_badge(11)}</h2>')
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
    o.append(f'<h2 id="sec12">12. Deployment &amp; Environment{_header_badge(12)}</h2>')
    if md.deployment_notes:
        o.append('<div class="card-section">')
        for line in md.deployment_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Dev / Test / Production workspaces, app URLs, deployment method (pipeline/Git), and per-environment parameters."))

    # 13. Access & Permissions
    o.append(f'<h2 id="sec13">13. Access &amp; Permissions{_header_badge(13)}</h2>')
    if md.access_notes:
        o.append('<div class="card-section">')
        for line in md.access_notes.split('\n'):
            if line.strip():
                o.append(f"<p>{_e(line)}</p>")
        o.append('</div>')
    else:
        o.append(_todo("Workspace roles and app access per group, with justification."))

    # 14. Glossary. doc.glossary_entries already carries the merged result
    # (Day 3): human terms from md.glossary override a matching auto-inferred
    # definition and append any new business term, rather than the raw text
    # field replacing the whole inferred table the way an either/or used to.
    o.append(f'<h2 id="sec14">14. Data Dictionary / Glossary{_header_badge(14)}</h2>')
    o.append('<p class="muted">Column-level data dictionary is in section 6. Business-term definitions belong below.</p>')
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
    o.append(f'<h2 id="sec15">15. Known Issues, Assumptions &amp; Limitations{_header_badge(15)}</h2>')
    for n in td.notes:
        o.append(f"<p>{_e(n)}</p>")
    if md.assumptions:
        o.append('<h3>Business Assumptions &amp; Limitations</h3>')
        o.append(f'<p>{_e(md.assumptions)}</p>')
    # Redesigned Unused Assets grouping by table
    unused = td.unused_assets or {}
    unused_cols_raw = unused.get("columns", [])
    unused_calc_cols_raw = unused.get("calculated_columns", [])
    unused_meas_raw = unused.get("measures", [])
    
    if unused_cols_raw or unused_calc_cols_raw or unused_meas_raw:
        o.append("<h3>Unused Assets Grouped by Table</h3>")
        
        table_unused = {}
        for c in unused_cols_raw:
            tbl = c["table"]
            table_unused.setdefault(tbl, {"columns": [], "calculated_columns": [], "measures": []})["columns"].append(c["column"])
        for c in unused_calc_cols_raw:
            tbl = c["table"]
            table_unused.setdefault(tbl, {"columns": [], "calculated_columns": [], "measures": []})["calculated_columns"].append(c["column"])
            
        m_to_tbl = {m.name: m.table for m in doc.measure_catalog.measures if m.table}
        for m_name in unused_meas_raw:
            tbl = m_to_tbl.get(m_name, "Unassigned Measures")
            table_unused.setdefault(tbl, {"columns": [], "calculated_columns": [], "measures": []})["measures"].append(m_name)
            
        for t_name, assets in sorted(table_unused.items()):
            col_list = assets["columns"]
            calc_col_list = assets["calculated_columns"]
            meas_list = assets["measures"]
            total_count = len(col_list) + len(calc_col_list) + len(meas_list)
            if total_count == 0:
                continue
                
            o.append(f'<details class="collapsible" style="margin-bottom:1rem; border:1px solid #e2e8f0; padding:10px; border-radius:6px;">')
            o.append(f'<summary style="font-weight:600; cursor:pointer;">Table: {_e(t_name)} ({total_count} unused assets)</summary>')
            o.append('<div style="margin-top:10px; padding-left:15px;">')
            
            if col_list:
                o.append('<h4>Unused Columns</h4><ul>')
                for col in sorted(col_list):
                    o.append(f'<li><strong>{_e(col)}</strong> — Evidence: no visuals, no measures, no relationships, no RLS filters reference this column.</li>')
                o.append('</ul>')
                
            if calc_col_list:
                o.append('<h4>Unused Calculated Columns</h4><ul>')
                for col in sorted(calc_col_list):
                    o.append(f'<li><strong>{_e(col)}</strong> — Evidence: no visuals, no measures, no relationships, no RLS filters reference this calculated column.</li>')
                o.append('</ul>')
                
            if meas_list:
                o.append('<h4>Unused Measures</h4><ul>')
                for m in sorted(meas_list):
                    o.append(f'<li><strong>{_e(m)}</strong> — Evidence: no visuals or other measures reference this measure.</li>')
                o.append('</ul>')
                
            script_lines = []
            for col in sorted(col_list + calc_col_list):
                script_lines.append(f'Model.Tables["{t_name}"].Columns["{col}"].Delete();')
            for m in sorted(meas_list):
                script_lines.append(f'Model.Tables["{t_name}"].Measures["{m}"].Delete();')
            
            if script_lines:
                o.append('<h4>Tabular Editor C# Script</h4>')
                script_text = f'// Tabular Editor C# script to remove unused assets in Table {t_name}\n' + "\n".join(script_lines)
                o.append(f'<pre><code>{_e(script_text)}</code></pre>')
                
            o.append('</div></details>')
            
    if sm.risks:
        o.append("<h3>Modeling risks</h3>")
        o.append("<ul>" + "".join(f"<li>{_e(r)}</li>" for r in sm.risks) + "</ul>")
    if not md.assumptions:
        o.append(_todo("Business assumptions and limitations (e.g. \"returns lag source by 1 day\") with impact and workaround."))

    # 16. Model Health & AI Recommendations
    o.append(f'<h2 id="sec16">16. Model Health &amp; AI Recommendations{_header_badge(16)}</h2>')
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
    top_cluster = getattr(doc, "top_cluster", None)
    if top_cluster:
        o.append('<div class="card-section">')
        o.append(f'<h3>Root cause: {_e(top_cluster.get("root_cause", ""))}</h3>')
        o.append(f'<p>{_e(top_cluster.get("narrative", ""))}</p>')
        if top_cluster.get("rule_ids"):
            ids = ", ".join(f"<code>{_e(rid)}</code>" for rid in top_cluster["rule_ids"])
            o.append(f'<p class="muted">Related findings: {ids}</p>')
        if audit_href:
            o.append(f'<p class="muted">See the full <a href="{_e(audit_href)}#sec9">Root-Cause '
                     f'Analysis</a> in the Audit &amp; Health Report.</p>')
        o.append('</div>')
    recs = doc.ai_recommendations or []
    suppressed = doc.tech_debt.suppressed_rules if hasattr(doc, "tech_debt") and doc.tech_debt else []
    suppressed_count = len(suppressed)
    total_checks = doc.checks_run or TOTAL_RULE_COUNT
    passed_count = doc.checks_passed
    failed_count = doc.checks_failed

    o.append('<div class="card-section" style="margin-bottom: 1.5rem; padding: 12px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0;">')
    o.append(f'<p style="margin: 0; font-size: 0.95em;"><strong>Best Practice Rules Summary:</strong> Checks Run: <strong>{total_checks - suppressed_count}</strong> &middot; '
             f'Passed: <span style="color: #10b981; font-weight: 600;">{passed_count}</span> &middot; '
             f'Failed: <span style="color: #ef4444; font-weight: 600;">{failed_count}</span> &middot; '
             f'Suppressed: <span style="color: #64748b; font-weight: 600;">{suppressed_count}</span></p>')
    if suppressed:
        o.append(f'<p style="margin: 8px 0 0 0; font-size: 0.85em; color: #64748b;"><strong>Suppressed by configuration:</strong> {", ".join(f"<code>{_e(rid)}</code>" for rid in suppressed)}</p>')
    o.append('</div>')

    if recs:
        o.append("<h3>Prioritized recommendations</h3>")
        for i, r in enumerate(recs, 1):
            o.append('<div class="measure">')
            o.append(f'<h3>{i}. <span class="pill">{_e(r.get("priority", "Medium"))}</span> {_e(r.get("issue", ""))}</h3>')
            o.append(f'<p><strong>Impact:</strong> {_e(r.get("why_it_matters", ""))}</p>')
            o.append(f'<p><strong>Recommendation:</strong> {format_prose_with_code(r.get("suggested_fix", ""))}</p>')
            if r.get("expected_benefit"):
                o.append(f'<p><strong>Expected benefit:</strong> {_e(r.get("expected_benefit"))}</p>')
            o.append(f'<p><strong>Estimated effort:</strong> {_e(r.get("effort", "Medium"))}</p>')
            o.append("</div>")
    elif hs:
        o.append('<p class="muted">No recommendations — no findings were raised against this model.</p>')
    if not hs and not recs:
        o.append('<p class="muted">Not computed for this document.</p>')

    # 17. Support & Maintenance
    o.append(f'<h2 id="sec17">17. Support &amp; Maintenance{_header_badge(17)}</h2>')
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
    o.append(f'<h2 id="sec18">18. Appendix &amp; Sign-off{_header_badge(18)}</h2>')
    diagram_note = "The model diagram is in section 6. " if MODEL_DIAGRAM_RENDERED else ""
    o.append(f"<p>{diagram_note}Attach wireframes/mockups and any source-to-target mapping here.</p>")
    
    generated_date = _e((md.generated_at or "")[:10])
    # owner -> Business Owner, author -> Developer, reviewer -> Approver:
    # each row filled from the metadata field it actually corresponds to,
    # instead of "Business Owner" always rendering empty and "Developer"
    # showing the owner's name (Day 3).
    sign_off_rows = [
        ["Business Owner", _e(md.owner) if md.owner else "", "Business Owner", generated_date if md.owner else ""],
        ["Developer", _e(md.author) if md.author else "TBC", "BI Developer", generated_date],
        ["Approver", _e(md.reviewer) if md.reviewer else "", "Reviewer", generated_date if md.reviewer else ""],
    ]
    o.append(_table(["Sign-off Role", "Name", "Title / Role", "Date"], sign_off_rows))
    o.append('<p class="caveat"><strong>Reminder:</strong> Obtain sign-off before sharing with stakeholders.</p>')

    # 19. Methodology & Guarantees
    o.append('<h2 id="sec19">19. Methodology &amp; Guarantees <span class="pill extracted">Extracted</span></h2>')
    o.append('<div class="card-section">')
    o.append('<p><strong>Parsed Artifacts:</strong> Power BI metadata (tables, columns, measures, relationships, visuals, and page layout tables). No customer database row-level data is ever parsed, read, or transmitted.</p>')
    o.append('<p><strong>AI Agents Used:</strong> PBICompass Engine v0.1.0 and prompt version 2026-07. Models called: Anthropic Claude, Google Gemini, Cohere. All operations run under zero-retention policies.</p>')
    o.append('<p><strong>Guarantees:</strong> 100% offline-ready deliverables, zero CDNs, zero telemetry, and fully reproducible scoring metrics backed by deterministic compliance checking rules.</p>')
    o.append('<p><strong>Limitations:</strong> This tool cannot verify runtime query performance, network latency, database authentication credentials, or confirm the actual semantic business meaning without human verification.</p>')
    o.append('</div>')

    search_index = [{"title": title, "type": "section", "anchor": sec_id} for sec_id, title in TOC]
    search_index += [
        # Not deduped: measure names are unique model-wide in Power BI, and
        # the audit doc's cross-links (render/audit.py's _measure_cell)
        # independently compute this same slug — they must stay in sync.
        {"title": m.name, "type": "measure", "anchor": f"measure-{anchor_slug(m.name)}"}
        for m in doc.measure_catalog.measures
    ]
    search_index += [
        {"title": t["name"], "type": "table", "anchor": tid}
        for t, tid in zip(sm.tables, table_ids)
    ]

    subtitle_str = f'{md.target_audience or "BI developers and business stakeholders"} · generated {_fmt_ts(md.generated_at)}'
    if getattr(md, "score_trend", None):
        subtitle_str += f' · Score Trend: {md.score_trend}'

    from ._shared import compute_completeness
    comp = compute_completeness(md)

    return page_shell(
        title=md.report_name,
        subtitle=subtitle_str,
        toc=TOC, kpis=kpis, body_html="\n".join(o), search_index=search_index, doc_links=doc_links,
        owner=md.owner, version=md.version, status=md.status, classification=md.classification,
        completeness=comp,
    )
