"""Render an :class:`ExecutiveDocument` to Markdown, HTML, and DOCX.

Concise and non-technical by design: no DAX, no table/column inventories,
no relationship diagrams. Eleven short sections, mostly prose with a couple
of small stat tables — readable in under ten minutes, matching the
document's purpose. Reuses the same low-level primitives as the other
renderers (``_shared``, ``_html_shell``, ``_docx_writer``).
"""

from __future__ import annotations

from pathlib import Path

from ..schemas.executive_document import ExecutiveDocument
from ._docx_writer import _Docx
from ._html_shell import page_shell
from ._shared import format_timestamp as _fmt_ts
from ._shared import html_e as _e
from ._shared import html_table as _html_table
from ._shared import md_table as _table

_SECTION_TITLES = [
    "1. Business Purpose",
    "2. Key KPIs",
    "3. Data Sources",
    "4. Refresh Schedule",
    "5. Security Overview",
    "6. High-Level Architecture",
    "7. Model & Report Statistics",
    "8. Business Value",
    "9. Known Risks",
    "10. Maintenance Overview",
    "11. Future Recommendations",
]

_STAT_LABELS = {
    "tables": "Tables", "columns": "Columns", "measures": "Measures",
    "relationships": "Relationships", "roles": "Security Roles", "pages": "Pages",
    "visuals": "Visuals", "data_sources": "Data Sources", "visible_pages": "Visible Pages",
    "hidden_pages": "Hidden Pages", "drillthrough_pages": "Drill-through Pages",
}


def _stat_label(key: str) -> str:
    return _STAT_LABELS.get(key, key.replace("_", " ").title())


def _extra_dependencies(doc: ExecutiveDocument) -> list[str]:
    """``doc.dependencies`` is a superset of ``doc.data_sources_summary``
    (data sources + parameters) — return only the entries not already shown
    in the Data Sources bullet list, so the two no longer render as two
    byte-identical sections (1.9)."""
    sources = set(doc.data_sources_summary)
    return [d for d in doc.dependencies if d not in sources]


# -- Markdown -------------------------------------------------------------------
def render_markdown(doc: ExecutiveDocument) -> str:
    md = doc.metadata
    out: list[str] = [f"# {md.report_name} — Executive Summary\n"]
    out.append(f"_{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}_\n")

    out.append(f"\n## {_SECTION_TITLES[0]}\n")
    out.append(doc.business_purpose + "\n")

    out.append(f"\n## {_SECTION_TITLES[1]}\n")
    if doc.key_kpis:
        for kpi in doc.key_kpis:
            out.append(f"- {kpi}")
    else:
        out.append("_No KPIs identified._")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[2]}\n")
    if doc.data_sources_summary:
        for s in doc.data_sources_summary:
            out.append(f"- {s}")
    else:
        out.append("_No external data sources detected._")
    extra_deps = _extra_dependencies(doc)
    if extra_deps:
        out.append(f"\n**Also depends on:** {', '.join(extra_deps)}.")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[3]}\n")
    out.append(doc.refresh_schedule or "_Not documented._")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[4]}\n")
    out.append(doc.security_overview + "\n")

    out.append(f"\n## {_SECTION_TITLES[5]}\n")
    out.append(doc.architecture_overview + "\n")

    out.append(f"\n## {_SECTION_TITLES[6]}\n")
    out.append("**Model**\n")
    out.append(_table(["Metric", "Value"],
                      [[_stat_label(k), v] for k, v in doc.model_statistics.items()]))
    out.append("\n**Report**\n")
    out.append(_table(["Metric", "Value"],
                      [[_stat_label(k), v] for k, v in doc.report_statistics.items()]))

    out.append(f"\n## {_SECTION_TITLES[7]}\n")
    out.append(doc.business_value + "\n")

    out.append(f"\n## {_SECTION_TITLES[8]}\n")
    if doc.known_risks:
        for r in doc.known_risks:
            out.append(f"- {r}")
    else:
        out.append("_No known modeling risks._")
    out.append("")

    out.append(f"\n## {_SECTION_TITLES[9]}\n")
    out.append(doc.maintenance_overview + "\n")

    out.append(f"\n## {_SECTION_TITLES[10]}\n")
    if doc.future_recommendations:
        for r in doc.future_recommendations:
            out.append(f"- {r}")
    else:
        out.append("_No open recommendations — the latest audit found nothing to act on._")
    out.append("")

    return "\n".join(out).rstrip() + "\n"


# -- HTML -------------------------------------------------------------------------
def _bullet_list(items: list[str], empty: str) -> str:
    if not items:
        return f'<p class="muted">{_e(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"


def render_html(
    doc: ExecutiveDocument, *,
    doc_links: list[tuple[str, str]] | None = None,
    sibling_hrefs: dict[str, str] | None = None,
) -> str:
    md = doc.metadata
    audit_href = (sibling_hrefs or {}).get("audit")

    toc = [(f"sec{i+1}", title.split(". ", 1)[1]) for i, title in enumerate(_SECTION_TITLES)]
    kpis = [
        ("Tables", doc.model_statistics.get("tables", 0)),
        ("Measures", doc.model_statistics.get("measures", 0)),
        ("Report Pages", doc.report_statistics.get("visible_pages", 0)),
        ("Known Risks", len(doc.known_risks)),
    ]

    o: list[str] = []
    o.append(f'<h2 id="sec1">{_e(_SECTION_TITLES[0])}</h2>')
    o.append(f"<p>{_e(doc.business_purpose)}</p>")

    o.append(f'<h2 id="sec2">{_e(_SECTION_TITLES[1])}</h2>')
    o.append(_bullet_list(doc.key_kpis, "No KPIs identified."))

    o.append(f'<h2 id="sec3">{_e(_SECTION_TITLES[2])}</h2>')
    o.append(_bullet_list(doc.data_sources_summary, "No external data sources detected."))
    extra_deps = _extra_dependencies(doc)
    if extra_deps:
        o.append(f'<p><strong>Also depends on:</strong> {_e(", ".join(extra_deps))}.</p>')

    o.append(f'<h2 id="sec4">{_e(_SECTION_TITLES[3])}</h2>')
    o.append(f"<p>{_e(doc.refresh_schedule) if doc.refresh_schedule else '<span class=\"muted\">Not documented.</span>'}</p>")

    o.append(f'<h2 id="sec5">{_e(_SECTION_TITLES[4])}</h2>')
    o.append(f"<p>{_e(doc.security_overview)}</p>")

    o.append(f'<h2 id="sec6">{_e(_SECTION_TITLES[5])}</h2>')
    o.append(f"<p>{_e(doc.architecture_overview)}</p>")

    o.append(f'<h2 id="sec7">{_e(_SECTION_TITLES[6])}</h2>')
    o.append("<h3>Model</h3>")
    o.append(_html_table(["Metric", "Value"],
                         [[_e(_stat_label(k)), f'<span class="num">{_e(v)}</span>']
                          for k, v in doc.model_statistics.items()]))
    o.append("<h3>Report</h3>")
    o.append(_html_table(["Metric", "Value"],
                         [[_e(_stat_label(k)), f'<span class="num">{_e(v)}</span>']
                          for k, v in doc.report_statistics.items()]))

    o.append(f'<h2 id="sec8">{_e(_SECTION_TITLES[7])}</h2>')
    o.append(f"<p>{_e(doc.business_value)}</p>")

    o.append(f'<h2 id="sec9">{_e(_SECTION_TITLES[8])}</h2>')
    if doc.known_risks:
        # Every risk here is sourced from the audit engine's recommendations
        # (1.10) — link to the full write-up there when audit was generated
        # in the same job, never a dead link otherwise (2.7).
        suffix = f' — <a href="{_e(audit_href)}#sec8">full detail</a>' if audit_href else ""
        o.append("<ul>" + "".join(f"<li>{_e(r)}{suffix}</li>" for r in doc.known_risks) + "</ul>")
    else:
        o.append('<p class="muted">No known modeling risks.</p>')

    o.append(f'<h2 id="sec10">{_e(_SECTION_TITLES[9])}</h2>')
    o.append(f"<p>{_e(doc.maintenance_overview)}</p>")

    o.append(f'<h2 id="sec11">{_e(_SECTION_TITLES[10])}</h2>')
    o.append(_bullet_list(doc.future_recommendations,
                          "No open recommendations — the latest audit found nothing to act on."))

    return page_shell(
        title=f"{md.report_name} — Executive Summary",
        subtitle=f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}",
        toc=toc, kpis=kpis, body_html="\n".join(o), doc_links=doc_links,
        owner=md.owner, version=md.version, status=md.status,
    )


# -- DOCX -------------------------------------------------------------------------
def render_docx(doc: ExecutiveDocument, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata

    d.heading(0, f"{md.report_name} — Executive Summary")
    d.para([d._run(f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}", italic=True)])

    def _t(rows):
        return [[str(cell) for cell in row] for row in rows]

    def _bullets_or_none(items: list[str], empty: str) -> None:
        if items:
            for item in items:
                d.bullet(item)
        else:
            d.para([d._run(empty, italic=True)])

    d.heading(1, _SECTION_TITLES[0])
    d.para(doc.business_purpose)

    d.heading(1, _SECTION_TITLES[1])
    _bullets_or_none(doc.key_kpis, "No KPIs identified.")

    d.heading(1, _SECTION_TITLES[2])
    _bullets_or_none(doc.data_sources_summary, "No external data sources detected.")
    extra_deps = _extra_dependencies(doc)
    if extra_deps:
        d.para([d._run("Also depends on: ", bold=True), d._run(", ".join(extra_deps) + ".")])

    d.heading(1, _SECTION_TITLES[3])
    d.para(doc.refresh_schedule or "Not documented.")

    d.heading(1, _SECTION_TITLES[4])
    d.para(doc.security_overview)

    d.heading(1, _SECTION_TITLES[5])
    d.para(doc.architecture_overview)

    d.heading(1, _SECTION_TITLES[6])
    d.heading(2, "Model")
    d.table(["Metric", "Value"], _t([[_stat_label(k), v] for k, v in doc.model_statistics.items()]))
    d.heading(2, "Report")
    d.table(["Metric", "Value"], _t([[_stat_label(k), v] for k, v in doc.report_statistics.items()]))

    d.heading(1, _SECTION_TITLES[7])
    d.para(doc.business_value)

    d.heading(1, _SECTION_TITLES[8])
    _bullets_or_none(doc.known_risks, "No known modeling risks.")

    d.heading(1, _SECTION_TITLES[9])
    d.para(doc.maintenance_overview)

    d.heading(1, _SECTION_TITLES[10])
    _bullets_or_none(doc.future_recommendations,
                     "No open recommendations — the latest audit found nothing to act on.")

    d.save(out_path)
    return out_path
