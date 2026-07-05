"""Render an :class:`AuditDocument` to Markdown, HTML, and DOCX.

Evaluation-shaped, not description-shaped: health score, complexity,
DAX/best-practice/performance/governance findings, unused assets, and
prioritized recommendations — a different structure from the 17-section
technical document, so it gets its own small renderer rather than being
forced through the technical renderer's section-by-section body. All three
renderers reuse the same low-level primitives as the technical renderer
(``_shared``, ``_html_shell``, ``_docx_writer``) so nothing is duplicated
beyond what each document shape actually needs.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas.audit_document import AuditDocument
from ._docx_writer import _Docx
from ._html_shell import page_shell
from ._shared import anchor_slug
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_e as _e
from ._shared import html_table as _html_table
from ._shared import md_table as _table

_SECTION_TITLES = [
    "1. Overall Health Score",
    "2. Model Complexity",
    "3. DAX Review",
    "4. Model Best Practices",
    "5. Performance Risks",
    "6. Governance",
    "7. Unused Assets",
    "8. Recommendations",
]

_COMPONENT_LABELS = {
    "modeling": "Modeling", "dax": "DAX", "governance": "Governance",
    "performance": "Performance", "unused_assets": "Unused Assets",
}
_GOVERNANCE_AREA_LABELS = {
    "rls": "RLS", "descriptions": "Descriptions", "ownership": "Ownership",
    "sensitive_columns": "Sensitive Columns", "data_source_consistency": "Data Source Consistency",
}


def _component_label(key: str) -> str:
    return _COMPONENT_LABELS.get(key, key.replace("_", " ").title())


def _area_label(area: str) -> str:
    return _GOVERNANCE_AREA_LABELS.get(area, area.replace("_", " ").title())


def _kind_label(kind: str) -> str:
    return kind.replace("_", " ").title()


def _severity_note() -> str:
    return ("All performance risks below are heuristics inferred from metadata only — no "
            "row-level data is ever extracted, so nothing here reflects a measured runtime.")


def _unused_rows(ua) -> list[list]:
    return [
        ["Measures", len(ua.measures), ", ".join(ua.measures) or "—"],
        ["Columns", len(ua.columns), ", ".join(f"{c['table']}[{c['column']}]" for c in ua.columns) or "—"],
        ["Tables", len(ua.tables), ", ".join(ua.tables) or "—"],
        ["Calculated columns", len(ua.calculated_columns),
         ", ".join(f"{c['table']}[{c['column']}]" for c in ua.calculated_columns) or "—"],
        ["Report pages", len(ua.report_pages), ", ".join(ua.report_pages) or "—"],
    ]


# -- Markdown -------------------------------------------------------------------
def render_markdown(doc: AuditDocument) -> str:
    md = doc.metadata
    h = doc.health
    c = doc.complexity
    out: list[str] = [f"# {md.report_name} — Audit & Health Report\n"]
    out.append(f"_{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}_\n")
    if doc.narrative_overview:
        out.append(f"\n{doc.narrative_overview}\n")

    out.append(f"\n## {_SECTION_TITLES[0]}\n")
    out.append(f"**{h.overall} / 100 — {h.band}**\n")
    _notes = getattr(h, "component_notes", {}) or {}
    out.append(_table(["Component", "Score", "Why"],
                      [[_component_label(k), v, _notes.get(k, "")] for k, v in h.component_scores.items()]))

    out.append(f"\n## {_SECTION_TITLES[1]}\n")
    out.append(f"**{c.level}**\n")
    out.append(c.rationale + "\n")
    out.append(_table(["Metric", "Value"], [
        ["Tables", c.table_count], ["Measures", c.measure_count],
        ["Relationships", c.relationship_count], ["Calculated columns", c.calculated_column_count],
        ["Max relationship depth", c.max_relationship_depth],
    ]))

    out.append(f"\n## {_SECTION_TITLES[2]}\n")
    out.append(_table(
        ["Measure", "Table", "Finding", "Severity", "Detail"],
        [[f.measure, f.table or "—", _kind_label(f.kind), f.severity, f.detail] for f in doc.dax_findings],
        "_No DAX findings — no duplicate logic, overly long expressions, missing descriptions, "
        "or naming issues detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[3]}\n")
    out.append(_table(
        ["Check", "Result", "Detail"],
        [[bp.name, "✅ Pass" if bp.passed else "❌ Fail", bp.detail] for bp in doc.best_practices],
    ))

    out.append(f"\n## {_SECTION_TITLES[4]}\n")
    out.append(f"_{_severity_note()}_\n")
    out.append(_table(
        ["Kind", "Object", "Table", "Severity", "Detail"],
        [[_kind_label(r.kind), r.object_name, r.table or "—", r.severity, r.detail]
         for r in doc.performance_risks],
        "_No performance risk signals detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[5]}\n")
    out.append(_table(
        ["Area", "Severity", "Detail"],
        [[_area_label(g.area), g.severity, g.detail] for g in doc.governance],
        "_No governance gaps detected._",
    ))

    out.append(f"\n## {_SECTION_TITLES[6]}\n")
    out.append(_table(["Asset Type", "Count", "Items"], _unused_rows(doc.unused_assets)))
    out.append("\n_Hierarchies and calculation groups are not yet parsed by PBICompass, so they are "
               "excluded from this audit._\n")

    out.append(f"\n## {_SECTION_TITLES[7]}\n")
    if doc.recommendations:
        for r in doc.recommendations:
            out.append(f"### [{r.priority}] {r.issue}\n")
            out.append(f"**Why it matters:** {r.why_it_matters}\n")
            out.append(f"**Suggested fix:** {r.suggested_fix}\n")
            out.append(f"**Expected benefit:** {r.expected_benefit}\n")
            out.append(f"**Estimated effort:** {getattr(r, 'effort', 'Medium')}\n")
    else:
        out.append("_No recommendations — the model passed every deterministic check._\n")

    return "\n".join(out).rstrip() + "\n"


# -- HTML -------------------------------------------------------------------------
def _severity_pill(severity: str) -> str:
    return f'<span class="pill {severity.lower()}">{_e(severity)}</span>'


def _measure_cell(name: str, technical_href: str | None) -> str:
    """A measure name, linked to its entry in the technical document's
    Measure Catalog when that sibling doc was generated in the same job —
    never a dead link when it wasn't (2.7)."""
    if not technical_href:
        return _e(name)
    return f'<a href="{_e(technical_href)}#measure-{_e(anchor_slug(name))}">{_e(name)}</a>'


def render_html(
    doc: AuditDocument, *,
    doc_links: list[tuple[str, str]] | None = None,
    sibling_hrefs: dict[str, str] | None = None,
) -> str:
    technical_href = (sibling_hrefs or {}).get("technical")
    md = doc.metadata
    h = doc.health
    c = doc.complexity

    toc = [(f"sec{i+1}", title.split(". ", 1)[1]) for i, title in enumerate(_SECTION_TITLES)]
    kpis = [
        ("Health Score", f"{h.overall}/100"), ("Band", h.band), ("Complexity", c.level),
        ("Findings", str(len(doc.dax_findings) + len(doc.performance_risks) + len(doc.governance))),
    ]

    o: list[str] = []
    if doc.narrative_overview:
        o.append(f'<div class="card-section"><p>{_e(doc.narrative_overview)}</p></div>')

    o.append(f'<h2 id="sec1">{_e(_SECTION_TITLES[0])}</h2>')
    o.append('<div class="score-hero">')
    o.append(f'<div class="score-big">{_e(h.overall)}/100</div>')
    o.append(f'<div class="score-band">{_e(h.band)}</div>')
    o.append('</div>')
    _notes = getattr(h, "component_notes", {}) or {}
    o.append(_html_table(["Component", "Score", "Why"],
                         [[_e(_component_label(k)), _e(v), _e(_notes.get(k, ""))]
                          for k, v in h.component_scores.items()]))

    o.append(f'<h2 id="sec2">{_e(_SECTION_TITLES[1])}</h2>')
    o.append(f'<p><strong>{_e(c.level)}</strong></p>')
    o.append(f"<p>{_e(c.rationale)}</p>")
    o.append(_html_table(["Metric", "Value"], [
        ["Tables", f'<span class="num">{c.table_count}</span>'],
        ["Measures", f'<span class="num">{c.measure_count}</span>'],
        ["Relationships", f'<span class="num">{c.relationship_count}</span>'],
        ["Calculated columns", f'<span class="num">{c.calculated_column_count}</span>'],
        ["Max relationship depth", f'<span class="num">{c.max_relationship_depth}</span>'],
    ]))

    o.append(f'<h2 id="sec3">{_e(_SECTION_TITLES[2])}</h2>')
    o.append(_html_table(
        ["Measure", "Table", "Finding", "Severity", "Detail"],
        [[_measure_cell(f.measure, technical_href), _e(f.table or "—"), _e(_kind_label(f.kind)),
          _severity_pill(f.severity), _e(f.detail)]
         for f in doc.dax_findings],
        "No DAX findings — no duplicate logic, overly long expressions, missing descriptions, "
        "or naming issues detected.",
    ))

    o.append(f'<h2 id="sec4">{_e(_SECTION_TITLES[3])}</h2>')
    o.append(_html_table(
        ["Check", "Result", "Detail"],
        [[_e(bp.name), f'<span class="pill {"pass" if bp.passed else "fail"}">'
                       f'{"Pass" if bp.passed else "Fail"}</span>', _e(bp.detail)]
         for bp in doc.best_practices],
    ))

    o.append(f'<h2 id="sec5">{_e(_SECTION_TITLES[4])}</h2>')
    o.append(f'<p class="caveat">{_e(_severity_note())}</p>')
    o.append(_html_table(
        ["Kind", "Object", "Table", "Severity", "Detail"],
        [[_e(_kind_label(r.kind)), _e(r.object_name), _e(r.table or "—"), _severity_pill(r.severity),
          _e(r.detail)] for r in doc.performance_risks],
        "No performance risk signals detected.",
    ))

    o.append(f'<h2 id="sec6">{_e(_SECTION_TITLES[5])}</h2>')
    o.append(_html_table(
        ["Area", "Severity", "Detail"],
        [[_e(_area_label(g.area)), _severity_pill(g.severity), _e(g.detail)] for g in doc.governance],
        "No governance gaps detected.",
    ))

    o.append(f'<h2 id="sec7">{_e(_SECTION_TITLES[6])}</h2>')
    o.append(_html_table(["Asset Type", "Count", "Items"],
                         [[_e(row[0]), _e(row[1]), _e(row[2])] for row in _unused_rows(doc.unused_assets)]))
    o.append('<p class="caveat">Hierarchies and calculation groups are not yet parsed by PBICompass, '
             'so they are excluded from this audit.</p>')

    o.append(f'<h2 id="sec8">{_e(_SECTION_TITLES[7])}</h2>')
    if doc.recommendations:
        for r in doc.recommendations:
            o.append('<div class="card-section">')
            o.append(f'<h3>{_severity_pill(r.priority)} {_e(r.issue)}</h3>')
            o.append(f'<p><strong>Why it matters:</strong> {_e(r.why_it_matters)}</p>')
            o.append(f'<p><strong>Suggested fix:</strong> {_e(r.suggested_fix)}</p>')
            o.append(f'<p><strong>Expected benefit:</strong> {_e(r.expected_benefit)}</p>')
            o.append(f'<p><strong>Estimated effort:</strong> {_e(getattr(r, "effort", "Medium"))}</p>')
            o.append('</div>')
    else:
        o.append('<p class="muted">No recommendations — the model passed every deterministic check.</p>')

    return page_shell(
        title=f"{md.report_name} — Audit & Health Report",
        subtitle=f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}",
        toc=toc, kpis=kpis, body_html="\n".join(o), doc_links=doc_links,
        owner=md.owner, version=md.version, status=md.status,
    )


# -- DOCX -------------------------------------------------------------------------
def render_docx(doc: AuditDocument, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata
    h = doc.health
    c = doc.complexity

    d.heading(0, f"{md.report_name} — Audit & Health Report")
    d.para([d._run(f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}", italic=True)])
    if doc.narrative_overview:
        d.para(doc.narrative_overview)

    def _t(rows):
        return [[str(cell) for cell in row] for row in rows]

    d.heading(1, _SECTION_TITLES[0])
    d.para([d._run(f"{h.overall} / 100 — {h.band}", bold=True)])
    _notes = getattr(h, "component_notes", {}) or {}
    d.table(["Component", "Score", "Why"],
            _t([[_component_label(k), v, _notes.get(k, "")] for k, v in h.component_scores.items()]))

    d.heading(1, _SECTION_TITLES[1])
    d.para([d._run(c.level, bold=True)])
    d.para(c.rationale)
    d.table(["Metric", "Value"], _t([
        ["Tables", c.table_count], ["Measures", c.measure_count],
        ["Relationships", c.relationship_count], ["Calculated columns", c.calculated_column_count],
        ["Max relationship depth", c.max_relationship_depth],
    ]))

    d.heading(1, _SECTION_TITLES[2])
    d.table(["Measure", "Table", "Finding", "Severity", "Detail"],
            _t([[f.measure, f.table or "—", _kind_label(f.kind), f.severity, f.detail]
                for f in doc.dax_findings]) or [["—", "—", "—", "—", "No DAX findings."]])

    d.heading(1, _SECTION_TITLES[3])
    d.table(["Check", "Result", "Detail"],
            _t([[bp.name, "Pass" if bp.passed else "Fail", bp.detail] for bp in doc.best_practices]))

    d.heading(1, _SECTION_TITLES[4])
    d.para([d._run(_severity_note(), italic=True)])
    d.table(["Kind", "Object", "Table", "Severity", "Detail"],
            _t([[_kind_label(r.kind), r.object_name, r.table or "—", r.severity, r.detail]
                for r in doc.performance_risks]) or [["—", "—", "—", "—", "No performance risk signals."]])

    d.heading(1, _SECTION_TITLES[5])
    d.table(["Area", "Severity", "Detail"],
            _t([[_area_label(g.area), g.severity, g.detail] for g in doc.governance])
            or [["—", "—", "No governance gaps detected."]])

    d.heading(1, _SECTION_TITLES[6])
    d.table(["Asset Type", "Count", "Items"], _t(_unused_rows(doc.unused_assets)))
    d.para([d._run("Hierarchies and calculation groups are not yet parsed by PBICompass, so they are "
                   "excluded from this audit.", italic=True)])

    d.heading(1, _SECTION_TITLES[7])
    if doc.recommendations:
        for r in doc.recommendations:
            d.heading(3, f"[{r.priority}] {r.issue}")
            d.para([d._run("Why it matters: ", bold=True), d._run(r.why_it_matters)])
            d.para([d._run("Suggested fix: ", bold=True), d._run(r.suggested_fix)])
            d.para([d._run("Expected benefit: ", bold=True), d._run(r.expected_benefit)])
            d.para([d._run("Estimated effort: ", bold=True), d._run(getattr(r, "effort", "Medium"))])
    else:
        d.para("No recommendations — the model passed every deterministic check.")

    d.save(out_path)
    return out_path
