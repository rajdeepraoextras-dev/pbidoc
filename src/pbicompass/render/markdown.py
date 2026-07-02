"""Render a :class:`Document` to Markdown, following the enterprise BI
documentation template (the same 17 sections as the HTML renderer).

Extracted sections are auto-filled; human-only sections are populated from
user metadata or emitted as placeholders.

Stdlib only.
"""

from __future__ import annotations

from ..schemas.document import Document
from ._shared import is_local_path as _is_local_path
from ._shared import md_table as _table
from ._shared import md_todo as _todo


def render_markdown(doc: Document) -> str:
    md = doc.metadata
    s = doc.stats
    out: list[str] = [f"# {md.report_name} — Power BI Documentation\n"]
    out.append(
        f"_{md.target_audience or ''} · generated {md.generated_at or ''}_\n\n"
        f"**At a glance:** {s.get('tables',0)} tables · {s.get('columns',0)} columns · "
        f"{s.get('measures',0)} measures · {s.get('relationships',0)} relationships · "
        f"{s.get('pages',0)} pages · {s.get('visuals',0)} visuals\n"
    )

    # 1. Document Control
    out.append("## 1. Document Control\n")
    doc_control = [
        ["Dashboard / Report Name", md.report_name],
        ["Source format", md.source_format or "unknown"],
        ["Owner", md.owner or "_not specified_"],
        ["Author", md.author or "_not specified_"],
        ["Reviewer / Approver", md.reviewer or "_not specified_"],
        ["Version", md.version or "_not specified_"],
        ["Status", md.status or "_not specified_"],
        ["Classification", md.classification or "_not specified_"],
        ["Target audience", md.target_audience or "—"],
        ["Refresh schedule", md.refresh_schedule or "_not specified_"],
        ["Generated", md.generated_at or ""],
    ]
    out.append(_table(["Field", "Value"], doc_control))
    
    missing_doc_control = [f for f, v in [("Version", md.version), ("Status", md.status), ("Author", md.author), 
                                          ("Reviewer", md.reviewer), ("Classification", md.classification)] if not v]
    if missing_doc_control:
        out.append(_todo(f"Complete missing document control fields: {', '.join(missing_doc_control)}"))

    # 2. Executive Summary
    es = doc.executive_summary
    out.append("\n## 2. Executive Summary\n")
    out.append(es.core_purpose + "\n")
    
    if md.business_decision:
        out.append(f"\n### Primary Business Decision / Impact\n\n{md.business_decision}\n")
        
    headline = [m.name for m in doc.measure_catalog.measures][:6]
    if headline:
        out.append(f"\n**Headline metrics:** {', '.join(headline)}.\n")
        
    if not md.business_decision:
        out.append(_todo("The primary business decision this dashboard drives (e.g. weekly sales planning)."))

    # 3. Business Requirements
    out.append("\n## 3. Business Requirements\n")
    if md.requirements:
        out.append(md.requirements + "\n")
    else:
        req_rows = [
            [r["id"], r["requirement"], r["source"], r["priority"], r["status"]]
            for r in doc.inferred_requirements
        ]
        out.append(_table(["ID", "Inferred Requirement", "Source Visual", "Priority", "Status"], req_rows))

    # 4. Audience & Stakeholders
    out.append("\n## 4. Audience & Stakeholders\n")
    out.append(_table(["Role", "Name / Group", "Access"], [
        ["Business Owner", md.owner or "—", "Edit / sign-off"],
        ["Primary Users", md.target_audience or "—", "View"],
        ["Author / Creator", md.author or "—", "Modify / Publish"],
    ]))
    out.append(_todo("Data Owner, Developer/Maintainer, and per-group access levels."))

    # 5. Data Sources
    ln = doc.lineage
    out.append("\n## 5. Data Sources\n")
    for sysm in ln.source_systems:
        if _is_local_path(sysm):
            out.append(f"- {sysm} ⚠️ *Hardcoded local path*")
        else:
            out.append(f"- {sysm}")
    if not ln.source_systems:
        out.append("_No external data sources detected._")
    out.append("\n\n**Power Query / ETL transformations**\n")
    out.append(_table(["Object", "Transformation"],
                      [[t.get("name", ""), t.get("description", "")] for t in ln.transformations],
                      "_None found._"))
    out.append(_todo("Per source: authentication method, owning team, and known data latency."))

    # 6. Data Model
    sm = doc.semantic_model
    out.append("\n## 6. Data Model\n")
    out.append(sm.summary + "\n")
    if sm.risks:
        out.append("\n**Relationships of note & risks:**\n")
        for r in sm.risks:
            out.append(f"- {r}")
        out.append("")
    out.append("\n**Key tables**\n")
    out.append(_table(["Table", "Type", "Columns", "Measures"],
                      [[t["name"], t.get("kind", ""), t.get("columns", 0), t.get("measures", 0)] for t in sm.tables]))
    out.append("\n**Relationships**\n")
    out.append(_table(["From", "To", "Cardinality", "Cross-filter", "Active"],
                      [[ed["from"], ed["to"], f'{ed.get("from_card")}-to-{ed.get("to_card")}',
                        ed.get("cross_filter"), "Yes" if ed.get("is_active") else "No"]
                       for ed in sm.relationship_edges], "_No relationships defined._"))
    out.append("\n_(See the HTML version for the model diagram.)_\n")
    out.append("\n**Data dictionary**\n")
    out.append(_table(["Table", "Column", "Data Type", "Description"],
                      [[r.get("table", ""), r.get("column", ""), r.get("data_type", ""), r.get("description", "")]
                       for r in sm.data_dictionary]))

    # 7. Measures & Calculations
    out.append("\n## 7. Measures & Calculations (DAX Dictionary)\n")
    for m in doc.measure_catalog.measures:
        home = f" · {m.table}" if m.table else ""
        cat = f" · _{m.category}_" if m.category else ""
        out.append(f"### {m.name}{home}{cat}\n")
        out.append(m.plain_english)
        if m.caveats:
            out.append(f"\n_Note: {m.caveats}_")
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        out.append(f"\n_Used on: {used}_\n")
        out.append("```dax")
        out.append(m.dax)
        out.append("```\n")
    if doc.calculated_columns:
        out.append("**Calculated columns**\n")
        out.append(_table(["Table", "Column", "Expression"],
                          [[c.get("table", ""), c.get("column", ""), "`" + str(c.get("expression", "")).replace("\n", " ") + "`"]
                           for c in doc.calculated_columns]))

    # 8. Report Pages & Visuals
    out.append("\n## 8. Report Pages & Visuals\n")
    if es.complex_visual_explainers:
        out.append("**How to read the key visuals**\n")
        for ex in es.complex_visual_explainers:
            out.append(f"- **{ex.visual}** ({ex.page}): {ex.how_to_read}")
        out.append("")
    page_summaries = {p.page_title: p.summary for p in es.pages}
    for p in doc.report_pages:
        flags = [f for f, on in (("hidden", p.get("hidden")), ("drill-through", p.get("drillthrough"))) if on]
        flag = f" ({', '.join(flags)})" if flags else ""
        out.append(f"### {p['name']}{flag}\n")
        if page_summaries.get(p["name"]):
            out.append(page_summaries[p["name"]] + "\n")
        out.append(_table(["Visual", "Type", "Metric(s)", "Dimension(s)"],
                          [[v.get("label") or "—", v.get("type"),
                            ", ".join(v.get("metrics", [])) or "—", ", ".join(v.get("dimensions", [])) or "—"]
                           for v in p.get("visuals", [])], "_No data visuals on this page._"))
        if p.get("decorative_count"):
            out.append(f"_Plus {p['decorative_count']} decorative element(s) — images, shapes, text boxes._\n")

    # 9. Filters, Slicers & Navigation
    out.append("\n## 9. Filters, Slicers & Navigation\n")
    for line in es.navigation_guide:
        out.append(f"- {line}")
    out.append("")
    out.append(_table(["Slicer field", "Page"], [[x["field"], x["page"]] for x in doc.slicers], "_No slicers found._"))
    drill = [p["name"] for p in doc.report_pages if p.get("drillthrough")]
    if drill:
        out.append(f"\n**Drill-through pages:** {', '.join(drill)}.\n")
    out.append(_todo("Bookmarks, button navigation logic, and the fields passed on each drill-through."))

    # 10. RLS
    sec = doc.security
    out.append("\n## 10. Row-Level Security (RLS)\n")
    if sec.roles:
        rows = [[r.get("name", ""), "; ".join(f"`{f}`" for f in r.get("filters", [])) or "—",
                 ", ".join(r.get("members", [])) or "—"] for r in sec.roles]
        out.append(_table(["Role", "Rule (DAX filter)", "Members"], rows))
    else:
        out.append("_No row-level security roles are defined in this model._\n")
        
    if md.security_notes:
        out.append(f"\n### Security Validation & Scope\n\n{md.security_notes}\n")
    else:
        out.append(_todo('Confirm each role was tested with "View as role", and note any object-level security.'))

    # 11. Refresh, Gateway & Performance
    out.append("\n## 11. Refresh, Gateway & Performance\n")
    if md.refresh_schedule:
        out.append(f"**Refresh schedule:** {md.refresh_schedule}.\n")
    if md.refresh_notes:
        out.append(md.refresh_notes + "\n")
    else:
        placeholder_rows = [
            ["Refresh Type", "✎ To complete"],
            ["Gateway Name", "✎ To complete"],
            ["Typical Duration", "✎ To complete"],
            ["Dataset Size", "✎ To complete"],
            ["Failure Alert Contact", "✎ To complete"],
        ]
        out.append(_table(["Field", "Value / Status"], placeholder_rows))
        out.append(_todo("Detail performance considerations and gateway configurations."))
        
    # 12. Deployment
    out.append("\n## 12. Deployment & Environment\n")
    if md.deployment_notes:
        out.append(md.deployment_notes + "\n")
    else:
        out.append(_todo("Dev / Test / Production workspaces, app URLs, deployment method, per-environment parameters."))
        
    # 13. Access & Permissions
    out.append("\n## 13. Access & Permissions\n")
    if md.access_notes:
        out.append(md.access_notes + "\n")
    else:
        out.append(_todo("Workspace roles and app access per group, with justification."))
        
    # 14. Glossary
    out.append("\n## 14. Data Dictionary / Glossary\n")
    out.append("_Column-level data dictionary is in section 6._\n")
    if md.glossary:
        out.append(md.glossary + "\n")
    else:
        glossary_rows = []
        for r in doc.glossary_entries:
            typo = f"⚠️ {r['typo_flag']}" if r["typo_flag"] else "—"
            glossary_rows.append([
                f"`{r['term']}`",
                r["type"],
                r["definition"],
                typo
            ])
        out.append(_table(["Term", "Type", "Plain-English Definition", "Typo / Rename Flag"], glossary_rows))

    # 15. Known Issues
    td = doc.tech_debt
    out.append("\n## 15. Known Issues, Assumptions & Limitations\n")
    for n in td.notes:
        out.append(f"- {n}")
    if md.assumptions:
        out.append(f"\n### Business Assumptions & Limitations\n\n{md.assumptions}\n")
    if td.orphaned_measures:
        out.append("\n**Orphaned measures (defined but not used on any page):**")
        for m in td.orphaned_measures:
            out.append(f"- {m}")
    out.append("")
    if not md.assumptions:
        out.append(_todo("Business assumptions and limitations with impact and workaround."))
        
    # 16. Support & Maintenance
    out.append("\n## 16. Support & Maintenance\n")
    if md.owner:
        out.append(f"**First-line contact:** {md.owner}.\n")
    if md.support_notes:
        out.append(md.support_notes + "\n")
    else:
        out.append(_todo("Escalation contact, SLA, backup location, decommission criteria."))
        
    # 17. Appendix & Sign-off
    out.append("\n## 17. Appendix & Sign-off\n")
    out.append("The model diagram is in section 6.\n")
    developer_name = md.owner if md.owner else "TBC"
    generated_date = (md.generated_at or "")[:10]
    sign_off_rows = [
        ["Business Owner", "", "", ""],
        ["Data Owner", "", "", ""],
        ["Developer", developer_name, "BI Developer", generated_date],
    ]
    out.append(_table(["Sign-off Role", "Name", "Title / Role", "Date"], sign_off_rows))
    out.append("\n**Reminder:** Obtain sign-off before sharing with stakeholders.\n")

    return "\n".join(out).rstrip() + "\n"
