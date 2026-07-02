"""Render a :class:`Document` to a Word ``.docx`` — with no third-party deps.

A ``.docx`` is just a ZIP of XML parts (OOXML). We hand-write the minimal valid
set (content types, relationships, styles, and the document body) so a real,
editable Word document is produced without ``python-docx``/``lxml``/Pandoc. The
named heading styles make it navigable and TOC-able in Word.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..schemas.document import Document
from ._docx_writer import _Docx
from ._shared import is_local_path as _is_local_path


def _add_para_with_md(d: _Docx, text: str, style_name: str | None = None):
    paras = text.split("\n\n")
    for p in paras:
        p_strip = p.strip()
        if not p_strip:
            continue
        is_warning = "Warning:" in p_strip
        runs = []
        parts = re.split(r"(\*\*.*?\*\*)", p_strip)
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                runs.append(d._run(part[2:-2], bold=True, italic=is_warning))
            else:
                runs.append(d._run(part, italic=is_warning, bold=is_warning))
        d.para(runs, style=style_name)


def render_docx(doc: Document, out_path) -> Path:
    """Write ``doc`` to a ``.docx`` at ``out_path`` and return the path."""
    out_path = Path(out_path)
    d = _Docx()
    md = doc.metadata
    d.heading(0, f"{md.report_name} — Documentation")
    d.para([d._run(f"{md.target_audience or ''} · generated {md.generated_at or ''}", italic=True)])

    def todo(t):
        d.para([d._run("✎ To complete: " + t, italic=True)])

    def _t(rows):
        return [[str(c) for c in r] for r in rows]

    # 1. Document Control
    d.heading(1, "1. Document Control")
    d.label("Dashboard / Report Name", md.report_name)
    d.label("Source format", md.source_format or "unknown")
    d.label("Owner", md.owner or "—")
    d.label("Author", md.author or "—")
    d.label("Reviewer / Approver", md.reviewer or "—")
    d.label("Version", md.version or "—")
    d.label("Status", md.status or "—")
    d.label("Classification", md.classification or "—")
    d.label("Target audience", md.target_audience or "—")
    d.label("Refresh schedule", md.refresh_schedule or "—")
    d.label("Generated", md.generated_at or "")
    
    missing_doc_control = [f for f, v in [("Version", md.version), ("Status", md.status), ("Author", md.author), 
                                          ("Reviewer", md.reviewer), ("Classification", md.classification)] if not v]
    if missing_doc_control:
        todo(f"Complete missing document control fields: {', '.join(missing_doc_control)}")

    # 2. Executive Summary
    es = doc.executive_summary
    d.heading(1, "2. Executive Summary")
    _add_para_with_md(d, es.core_purpose)
    if md.business_decision:
        d.heading(2, "Primary Business Decision / Impact")
        d.para(md.business_decision)
    headline = [m.name for m in doc.measure_catalog.measures][:6]
    if headline:
        d.para([d._run("Headline metrics: ", bold=True), d._run(", ".join(headline) + ".")])
    if not md.business_decision:
        todo("The primary business decision this dashboard drives (e.g. weekly sales planning).")

    # 3. Business Requirements
    d.heading(1, "3. Business Requirements")
    if md.requirements:
        for req in md.requirements.split('\n'):
            if req.strip():
                d.para(req)
    else:
        req_rows = [
            [r["id"], r["requirement"], r["source"], r["priority"], r["status"]]
            for r in doc.inferred_requirements
        ]
        d.table(["ID", "Inferred Requirement", "Source Visual", "Priority", "Status"], _t(req_rows))

    # 4. Audience & Stakeholders
    d.heading(1, "4. Audience & Stakeholders")
    d.table(["Role", "Name / Group", "Access"],
            [["Business Owner", md.owner or "—", "Edit / sign-off"],
             ["Primary Users", md.target_audience or "—", "View"],
             ["Author / Creator", md.author or "—", "Modify / Publish"]])
    todo("Confirm other stakeholders (Data Owner, Developer/Maintainer, and per-group access levels).")

    # 5. Data Sources
    ln = doc.lineage
    d.heading(1, "5. Data Sources")
    for s in ln.source_systems or ["No external data sources detected."]:
        if _is_local_path(s):
            d.bullet(s + " ⚠️ [Hardcoded local path]")
        else:
            d.bullet(s)
    d.heading(2, "Power Query / ETL transformations")
    d.table(["Object", "Transformation"],
            _t([[t.get("name", ""), t.get("description", "")] for t in ln.transformations]) or [["—", "None found."]])
    todo("Per source: authentication, owning team, and known data latency.")

    # 6. Data Model
    sm = doc.semantic_model
    d.heading(1, "6. Data Model")
    for para in sm.summary.split("\n\n"):
        for line in para.split("\n"):
            d.para(line)
    if sm.risks:
        d.heading(2, "Relationships of note & risks")
        for r in sm.risks:
            d.bullet(r)
    d.heading(2, "Key tables")
    d.table(["Table", "Type", "Columns", "Measures"],
            _t([[t["name"], t.get("kind", ""), t.get("columns", 0), t.get("measures", 0)] for t in sm.tables]))
    d.heading(2, "Relationships")
    d.table(["From", "To", "Cardinality", "Cross-filter", "Active"],
            _t([[ed["from"], ed["to"], f'{ed.get("from_card")}-to-{ed.get("to_card")}', ed.get("cross_filter"),
                 "Yes" if ed.get("is_active") else "No"] for ed in sm.relationship_edges]) or [["—", "—", "—", "—", "—"]])
    d.heading(2, "Data dictionary")
    d.table(["Table", "Column", "Data Type", "Description"],
            _t([[r.get("table", ""), r.get("column", ""), r.get("data_type", ""), r.get("description", "")]
                for r in sm.data_dictionary]))

    # 7. Measures
    d.heading(1, "7. Measures & Calculations (DAX Dictionary)")
    for m in doc.measure_catalog.measures:
        suffix = (f" · {m.table}" if m.table else "") + (f" · {m.category}" if m.category else "")
        d.heading(3, m.name + suffix)
        d.para(m.plain_english)
        if m.caveats:
            d.para([d._run("Note: " + m.caveats, italic=True)])
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        d.para([d._run("Used on: " + used, italic=True)])
        d.code(m.dax)
    if doc.calculated_columns:
        d.heading(2, "Calculated columns")
        d.table(["Table", "Column", "Expression"],
                _t([[c.get("table", ""), c.get("column", ""), (c.get("expression", "") or "").replace("\n", " ")]
                    for c in doc.calculated_columns]))

    # 8. Report Pages & Visuals
    d.heading(1, "8. Report Pages & Visuals")
    page_summaries = {p.page_title: p.summary for p in es.pages}
    for p in doc.report_pages:
        flags = [f for f, on in (("hidden", p.get("hidden")), ("drill-through", p.get("drillthrough"))) if on]
        d.heading(3, p["name"] + (f" ({', '.join(flags)})" if flags else ""))
        if page_summaries.get(p["name"]):
            d.para(page_summaries[p["name"]])
        d.table(["Visual", "Type", "Metric(s)", "Dimension(s)"],
                _t([[v.get("label") or "—", v.get("type"), ", ".join(v.get("metrics", [])) or "—",
                     ", ".join(v.get("dimensions", [])) or "—"] for v in p.get("visuals", [])]) or [["—", "—", "—", "—"]])
        if p.get("decorative_count"):
            d.para([d._run(f"Plus {p['decorative_count']} decorative element(s) — images, shapes, text boxes.", italic=True)])

    # 9. Filters, Slicers & Navigation
    d.heading(1, "9. Filters, Slicers & Navigation")
    d.table(["Slicer field", "Page"], _t([[x["field"], x["page"]] for x in doc.slicers]) or [["—", "—"]])
    drill = [p["name"] for p in doc.report_pages if p.get("drillthrough")]
    if drill:
        d.para([d._run("Drill-through pages: ", bold=True), d._run(", ".join(drill) + ".")])
    todo("Bookmarks, button navigation logic, and drill-through fields.")

    # 10. RLS
    sec = doc.security
    d.heading(1, "10. Row-Level Security (RLS)")
    if sec.roles:
        d.table(["Role", "Rule (DAX filter)", "Members"],
                _t([[r.get("name", ""), "; ".join(r.get("filters", [])) or "—", ", ".join(r.get("members", [])) or "—"]
                    for r in sec.roles]))
    else:
        d.para("No row-level security roles are defined in this model.")
        
    if md.security_notes:
        d.heading(2, "Security Validation & Scope")
        d.para(md.security_notes)
    else:
        todo("Confirm each role was tested with 'View as role', and note OLS rules.")

    # 11. Refresh, Gateway & Performance
    d.heading(1, "11. Refresh, Gateway & Performance")
    if md.refresh_schedule:
        d.label("Refresh schedule", md.refresh_schedule)
    if md.refresh_notes:
        for line in md.refresh_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        placeholder_rows = [
            ["Refresh Type", "✎ To complete"],
            ["Gateway Name", "✎ To complete"],
            ["Typical Duration", "✎ To complete"],
            ["Dataset Size", "✎ To complete"],
            ["Failure Alert Contact", "✎ To complete"],
        ]
        d.table(["Field", "Value / Status"], _t(placeholder_rows))
        todo("Detail performance considerations and gateway configurations.")
        
    # 12. Deployment
    d.heading(1, "12. Deployment & Environment")
    if md.deployment_notes:
        for line in md.deployment_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Dev / Test / Production workspaces, app URLs, deployment method, parameters.")
        
    # 13. Access & Permissions
    d.heading(1, "13. Access & Permissions")
    if md.access_notes:
        for line in md.access_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Workspace roles and app access per group, with justification.")
        
    # 14. Glossary
    d.heading(1, "14. Data Dictionary / Glossary")
    d.para("Column-level data dictionary is in section 6.")
    if md.glossary:
        for line in md.glossary.split('\n'):
            if line.strip():
                d.para(line)
    else:
        glossary_rows = []
        for r in doc.glossary_entries:
            typo = f"⚠️ {r['typo_flag']}" if r["typo_flag"] else "—"
            glossary_rows.append([
                r["term"],
                r["type"],
                r["definition"],
                typo
            ])
        d.table(["Term", "Type", "Plain-English Definition", "Typo / Rename Flag"], _t(glossary_rows))

    # 15. Known Issues
    td = doc.tech_debt
    d.heading(1, "15. Known Issues, Assumptions & Limitations")
    for n in td.notes:
        d.bullet(n)
    if md.assumptions:
        d.heading(2, "Business Assumptions & Limitations")
        d.para(md.assumptions)
    if td.orphaned_measures:
        d.heading(2, "Orphaned measures (defined but not used on any page)")
        for m in td.orphaned_measures:
            d.bullet(m)
    if not md.assumptions:
        todo("Business assumptions and limitations with impact and workaround.")
        
    # 16. Support & Maintenance
    d.heading(1, "16. Support & Maintenance")
    if md.owner:
        d.label("First-line contact", md.owner)
    if md.support_notes:
        for line in md.support_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Escalation contact, SLA, backup location, decommission criteria.")
        
    # 17. Appendix & Sign-off
    d.heading(1, "17. Appendix & Sign-off")
    d.para("The model diagram is in the HTML / section 6.")
    developer_name = md.owner if md.owner else "TBC"
    generated_date = (md.generated_at or "")[:10]
    sign_off_rows = [
        ["Business Owner", "", "", ""],
        ["Data Owner", "", "", ""],
        ["Developer", developer_name, "BI Developer", generated_date],
    ]
    d.table(["Sign-off Role", "Name", "Title / Role", "Date"], _t(sign_off_rows))
    d.para([d._run("Reminder: ", bold=True), d._run("Obtain sign-off before sharing with stakeholders.")])

    d.save(out_path)
    return out_path
