"""Render a :class:`Document` to a self-contained, styled HTML file.

Follows the structure of a proper enterprise BI documentation template: every
section that can be extracted from the file is auto-filled; sections that require
human input are populated from user metadata or emitted as placeholders.

Stdlib only. Opens in any browser and prints cleanly to PDF.
"""

from __future__ import annotations

import math
import re

from ..schemas.document import Document
from ._shared import html_e as _e
from ._shared import html_table as _table
from ._shared import html_todo as _todo
from ._shared import is_local_path as _is_local_path

_CSS = """
:root {
  --font-sans: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  --bg-main: #f8fafc;
  --bg-card: #ffffff;
  --text-main: #0f172a;
  --text-muted: #475569;
  --text-faint: #94a3b8;
  --border-color: #e2e8f0;
  --primary: #4f46e5;
  --primary-hover: #4338ca;
  --primary-light: #eef2ff;
  --secondary: #0ea5e9;
  --success: #10b981;
  --success-light: #ecfdf5;
  --warning: #f59e0b;
  --warning-light: #fef3c7;
  --danger: #ef4444;
  --danger-light: #fef2f2;
  --code-bg: #0f172a;
  --sidebar-w: 280px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  color: var(--text-main);
  background-color: var(--bg-main);
  line-height: 1.6;
  display: flex;
  min-height: 100vh;
}

/* Sidebar styling */
.sidebar {
  width: var(--sidebar-w);
  background: var(--bg-card);
  border-right: 1px solid var(--border-color);
  padding: 32px 20px;
  position: fixed;
  top: 0;
  bottom: 0;
  left: 0;
  overflow-y: auto;
  z-index: 100;
}
.sidebar-logo {
  font-weight: 800;
  font-size: 1.3rem;
  color: var(--primary);
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  letter-spacing: -0.02em;
}
.sidebar-logo svg {
  width: 26px;
  height: 26px;
  fill: currentColor;
}
.toc-list {
  list-style: none;
}
.toc-item {
  margin-bottom: 4px;
}
.toc-link {
  display: block;
  padding: 8px 12px;
  color: var(--text-muted);
  text-decoration: none;
  font-size: 0.85rem;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.toc-link:hover {
  background: var(--primary-light);
  color: var(--primary);
}
.toc-link.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}

/* Main Content Area */
.content-wrapper {
  margin-left: var(--sidebar-w);
  flex-grow: 1;
  padding: 48px 56px;
  max-width: calc(100vw - var(--sidebar-w));
}
.main-content {
  max-width: 900px;
  margin: 0 auto;
}

/* Header Cards */
.header-card {
  background: linear-gradient(135deg, #1e1b4b 0%, #311042 100%);
  color: #ffffff;
  border-radius: 16px;
  padding: 44px;
  margin-bottom: 32px;
  position: relative;
  overflow: hidden;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.05);
}
.header-card::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -20%;
  width: 350px;
  height: 350px;
  background: radial-gradient(circle, rgba(79, 70, 229, 0.3) 0%, rgba(0,0,0,0) 70%);
  border-radius: 50%;
  pointer-events: none;
}
.header-card h1 {
  font-size: 2.2rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  margin-bottom: 8px;
  line-height: 1.2;
}
.header-card .subtitle {
  color: rgba(255, 255, 255, 0.75);
  font-size: 0.98rem;
  margin: 0;
  font-weight: 400;
}

/* KPIs / Stats grid */
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 16px;
  margin-bottom: 36px;
}
.kpi {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  text-align: left;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.kpi:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}
.kpi .n {
  font-size: 1.8rem;
  font-weight: 700;
  color: var(--primary);
  line-height: 1.2;
}
.kpi .l {
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-top: 4px;
}

/* Typography & Section Styles */
h2 {
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--text-main);
  margin: 44px 0 18px;
  padding-bottom: 8px;
  border-bottom: 2px solid var(--border-color);
  letter-spacing: -0.02em;
  scroll-margin-top: 24px;
}
h3 {
  font-size: 1.08rem;
  font-weight: 600;
  color: var(--text-main);
  margin: 24px 0 12px;
}
p {
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 0.94rem;
}
ul, ol {
  margin-left: 20px;
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 0.94rem;
}
li {
  margin-bottom: 6px;
}

/* Card-style Containers */
.card-section {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}

/* Tables */
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 16px 0 24px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}
th, td {
  padding: 10px 14px;
  text-align: left;
  vertical-align: middle;
  font-size: 0.86rem;
}
th {
  background-color: #f8fafc;
  font-weight: 600;
  color: var(--text-main);
  border-bottom: 1px solid var(--border-color);
  text-transform: uppercase;
  font-size: 0.72rem;
  letter-spacing: 0.05em;
}
td {
  border-bottom: 1px solid var(--border-color);
  color: var(--text-muted);
  background: var(--bg-card);
}
tr:last-child td {
  border-bottom: none;
}
tr:hover td {
  background-color: #fafbfd;
}
td.num {
  font-family: monospace;
  font-weight: 500;
}

/* Code & Pre */
pre {
  background: var(--code-bg);
  color: #e2e8f0;
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
  margin: 12px 0 20px;
}
code {
  font-family: Consolas, "SF Mono", Menlo, monospace;
  font-size: 0.82rem;
  background: #f1f5f9;
  color: #0f172a;
  padding: 2px 6px;
  border-radius: 4px;
}
pre code {
  background: transparent;
  color: inherit;
  padding: 0;
}

/* Badges & Pills */
.pill {
  display: inline-block;
  background: var(--primary-light);
  color: var(--primary);
  font-size: 0.7rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  vertical-align: middle;
  margin-left: 6px;
}

/* Todo items */
.todo {
  border: 1px dashed #fbbf24;
  background-color: #fffbeb;
  color: #b45309;
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 0.86rem;
  margin: 16px 0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.todo b {
  font-weight: 700;
}

/* Risk / Warning Alerts */
.risk {
  background-color: #fef2f2;
  border-left: 4px solid var(--danger);
  border-radius: 4px;
  padding: 12px 16px;
  margin: 12px 0;
  font-size: 0.86rem;
  color: #991b1b;
}

/* Caveat / Notes */
.caveat {
  font-size: 0.82rem;
  color: var(--text-muted);
  background: #f1f5f9;
  border-left: 3px solid var(--text-faint);
  padding: 6px 12px;
  border-radius: 0 4px 4px 0;
  margin: 8px 0;
}

/* Diagram styling */
.diagram {
  background: #ffffff;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  margin: 16px 0;
}
.legend {
  font-size: 0.72rem;
  color: var(--text-muted);
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 12px;
}
.legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  display: inline-block;
}

/* Measure catalog entries */
.measure {
  background: var(--bg-card);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 20px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.01);
}
.measure h3 {
  margin: 0 0 10px;
  font-size: 1.1rem;
}
.usedon {
  font-size: 0.76rem;
  color: var(--text-faint);
  margin-top: 6px;
  margin-bottom: 12px;
}

/* Responsiveness & Print settings */
@media (max-width: 1024px) {
  .sidebar {
    display: none;
  }
  .content-wrapper {
    margin-left: 0;
    max-width: 100%;
    padding: 32px 24px;
  }
}

@media print {
  body {
    background-color: #ffffff;
    display: block;
  }
  .sidebar {
    display: none;
  }
  .content-wrapper {
    margin-left: 0;
    padding: 0;
    max-width: 100%;
  }
  h2 {
    page-break-before: always;
  }
  h2:first-of-type {
    page-break-before: avoid;
  }
  pre, table, .measure, .diagram, .card-section {
    page-break-inside: avoid;
  }
}
"""

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

    svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="inherit">']
    svg.append('<defs><marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
               '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
               '<marker id="arro" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">'
               '<path d="M0,0 L7,3 L0,6 Z" fill="#d97706"/></marker></defs>')

    # edges first
    for ed in edges:
        a, b = pos.get(ed.get("from")), pos.get(ed.get("to"))
        if not a or not b:
            continue
        both = ed.get("cross_filter") == "both"
        color = "#d97706" if both else "#94a3b8"
        dash = ' stroke-dasharray="5 4"' if not ed.get("is_active", True) else ""
        marker = "arro" if both else "arr"
        svg.append(f'<line x1="{a[0]:.0f}" y1="{a[1]:.0f}" x2="{b[0]:.0f}" y2="{b[1]:.0f}" '
                   f'stroke="{color}" stroke-width="1.6"{dash} marker-end="url(#{marker})"/>')
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        card = f'{"∞" if ed.get("from_card")=="many" else "1"}:{"1" if ed.get("to_card")=="one" else "∞"}'
        svg.append(f'<text x="{mx:.0f}" y="{my-3:.0f}" font-size="10" fill="{color}" '
                   f'text-anchor="middle">{card}</text>')

    # boxes on top
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
        svg.append(f'<rect x="{rx:.0f}" y="{ry:.0f}" width="{bw}" height="{bh}" rx="9" '
                   f'fill="{fill}" stroke="{line}" stroke-width="1.6"/>')
        svg.append(f'<text x="{x:.0f}" y="{y-4:.0f}" font-size="12.5" font-weight="600" '
                   f'text-anchor="middle" fill="#1f2933">{_e(name)}</text>')
        svg.append(f'<text x="{x:.0f}" y="{y+12:.0f}" font-size="9.5" text-anchor="middle" '
                   f'fill="#8a94a3">{_e(sub)}</text>')
    svg.append("</svg>")

    legend = (
        '<div class="legend">'
        f'<span><i class="swatch" style="background:{_FACT_FILL};border:1px solid {_FACT_LINE}"></i>Fact</span>'
        f'<span><i class="swatch" style="background:{_DIM_FILL};border:1px solid {_DIM_LINE}"></i>Dimension</span>'
        f'<span><i class="swatch" style="background:{_CALC_FILL};border:1px solid {_CALC_LINE}"></i>Calculated</span>'
        '<span><i class="swatch" style="background:#94a3b8"></i>Single-direction</span>'
        '<span><i class="swatch" style="background:#d97706"></i>Bi-directional</span>'
        '<span>– – – inactive</span></div>'
    )
    return f'<div class="diagram">{"".join(svg)}</div>{legend}'


# -- main ---------------------------------------------------------------------
def render_html(doc: Document) -> str:
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
        ("sec16", "Support & Maintenance"),
        ("sec17", "Appendix & Sign-off"),
    ]

    o: list[str] = ["<!DOCTYPE html>", '<html lang="en"><head><meta charset="utf-8">']
    o.append(f"<title>{_e(md.report_name)} — Documentation</title>")
    o.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    o.append('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
    o.append('<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">')
    o.append(f"<style>{_CSS}</style></head><body>")

    # Render Sidebar TOC
    o.append('<div class="sidebar">')
    o.append('<div class="sidebar-logo">')
    o.append('<svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>')
    o.append('<span>PBICompass</span>')
    o.append('</div>')
    o.append('<ul class="toc-list">')
    for sec_id, title in TOC:
        o.append(f'<li class="toc-item"><a href="#{sec_id}" class="toc-link">{_e(title)}</a></li>')
    o.append('</ul></div>')

    # Main wrapper
    o.append('<div class="content-wrapper">')
    o.append('<div class="main-content">')

    # Header card
    o.append('<div class="header-card">')
    o.append(f"<h1>{_e(md.report_name)}</h1>")
    o.append(f'<p class="subtitle">{_e(md.target_audience or "BI developers and business stakeholders")} · generated {_e(md.generated_at or "")}</p>')
    o.append("</div>")

    # At-a-glance stats
    o.append('<div class="kpis">')
    for label, key in (("Tables", "tables"), ("Columns", "columns"), ("Measures", "measures"),
                       ("Relationships", "relationships"), ("Pages", "pages"),
                       ("Visuals", "visuals"), ("Sources", "data_sources")):
        o.append(f'<div class="kpi"><div class="n">{_e(s.get(key, 0))}</div><div class="l">{_e(label)}</div></div>')
    o.append("</div>")

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
        ["Generated", _e(md.generated_at or "")],
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
    o.append(_table(["Table", "Type", "Columns", "Measures"],
                    [[_e(t["name"]), _e(t.get("kind", "")), f'<span class="num">{t.get("columns", 0)}</span>',
                      f'<span class="num">{t.get("measures", 0)}</span>'] for t in sm.tables]))
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
        o.append('<div class="measure">')
        o.append(f"<h3>{_e(m.name)}{home}{cat}</h3>")
        o.append(f"<p>{_e(m.plain_english)}</p>")
        if m.caveats:
            o.append(f'<p class="caveat">Note: {_e(m.caveats)}</p>')
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        fmt = f" · format <code>{_e(m.format_string)}</code>" if m.format_string else ""
        o.append(f'<p class="usedon">Used on: {_e(used)}{fmt}</p>')
        o.append(f"<pre><code>{_e(m.dax)}</code></pre>")
        o.append("</div>")
    if doc.calculated_columns:
        o.append("<h3>Calculated columns</h3>")
        o.append(_table(["Table", "Column", "Expression"],
                        [[_e(c.get("table", "")), _e(c.get("column", "")),
                          f'<code>{_e(c.get("expression", ""))}</code>'] for c in doc.calculated_columns]))

    # 8. Report Pages & Visuals
    o.append('<h2 id="sec8">8. Report Pages &amp; Visuals</h2>')
    if es.complex_visual_explainers:
        o.append("<h3>How to read the key visuals</h3><ul>")
        for ex in es.complex_visual_explainers:
            o.append(f"<li><strong>{_e(ex.visual)}</strong> ({_e(ex.page)}): {_e(ex.how_to_read)}</li>")
        o.append("</ul>")
    page_summaries = {p.page_title: p.summary for p in es.pages}
    for p in doc.report_pages:
        flags = []
        if p.get("hidden"):
            flags.append("hidden")
        if p.get("drillthrough"):
            flags.append("drill-through")
        flag = f' <span class="muted">({", ".join(flags)})</span>' if flags else ""
        o.append(f"<h3>{_e(p['name'])}{flag}</h3>")
        if page_summaries.get(p["name"]):
            o.append(f"<p>{_e(page_summaries[p['name']])}</p>")
        rows = [[_e(v.get("label") or "—"), _e(v.get("type")),
                 _e(", ".join(v.get("metrics", [])) or "—"),
                 _e(", ".join(v.get("dimensions", [])) or "—")] for v in p.get("visuals", [])]
        o.append(_table(["Visual", "Type", "Metric(s)", "Dimension(s)"], rows, "No data visuals on this page."))
        if p.get("decorative_count"):
            o.append(f'<p class="muted">Plus {p["decorative_count"]} decorative element(s) — images, shapes, text boxes.</p>')

    # 9. Filters, Slicers & Navigation
    o.append('<h2 id="sec9">9. Filters, Slicers &amp; Navigation</h2>')
    if es.navigation_guide:
        o.append("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in es.navigation_guide) + "</ul>")
    o.append(_table(["Slicer field", "Page"],
                    [[_e(s_["field"]), _e(s_["page"])] for s_ in doc.slicers],
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
            rules = "<br>".join(f"<code>{_e(f)}</code>" for f in r.get("filters", [])) or '<span class="muted">—</span>'
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

    # 16. Support & Maintenance
    o.append('<h2 id="sec16">16. Support &amp; Maintenance</h2>')
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

    # 17. Appendix & Sign-off
    o.append('<h2 id="sec17">17. Appendix &amp; Sign-off</h2>')
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

    o.append("</div></div>")
    
    # Active Link Highlighter Script
    o.append("""
<script>
document.addEventListener('DOMContentLoaded', () => {
  const links = document.querySelectorAll('.toc-link');
  const sections = document.querySelectorAll('h2[id]');
  
  function changeActiveLink() {
    let index = sections.length;
    while(--index && window.scrollY + 100 < sections[index].offsetTop) {}
    links.forEach((link) => link.classList.remove('active'));
    if (sections[index]) {
      const activeLink = document.querySelector(`.toc-link[href="#${sections[index].id}"]`);
      if (activeLink) activeLink.classList.add('active');
    }
  }
  
  changeActiveLink();
  window.addEventListener('scroll', changeActiveLink);
});
</script>
""")
    o.append("</body></html>")
    return "\n".join(o)
