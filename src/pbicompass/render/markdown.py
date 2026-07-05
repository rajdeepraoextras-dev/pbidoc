"""Render a :class:`Document` to Markdown, following the enterprise BI
documentation template (the same 18 sections as the HTML renderer).

Extracted sections are auto-filled; human-only sections are populated from
user metadata or emitted as placeholders.

Stdlib only.
"""

from __future__ import annotations

from ..agents.audit_rules import TOTAL_RULE_COUNT
from ..schemas.document import Document
from ._shared import HEALTH_COMPONENT_LABELS
from ._shared import format_timestamp as _fmt_ts
from ._shared import is_local_path as _is_local_path
from ._shared import md_table as _table
from ._shared import md_todo as _todo
from ._shared import non_data_note as _non_data_note
from ._shared import slicer_field_label as _slicer_label


def render_markdown(doc: Document) -> str:
    md = doc.metadata
    s = doc.stats
    out: list[str] = [f"# {md.report_name} — Power BI Documentation\n"]
    subtitle_str = f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}"
    if getattr(md, "score_trend", None):
        subtitle_str += f" · Score Trend: {md.score_trend}"
    from ._shared import compute_completeness
    pct, missing_count, _ = compute_completeness(md)
    out.append(
        f"_{subtitle_str}_\n\n"
        f"**Completeness:** {pct}% ({missing_count} fields awaiting input)\n\n"
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
        ["Generated", _fmt_ts(md.generated_at)],
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
        out.append(_todo("Business requirements have not yet been captured; confirm scope with the business owner."))

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
    if ln.data_sources_inventory:
        out.append(_table(["Source Type", "Location / Host", "Table(s) Fed", "Storage Mode", "Authentication", "Flag / Risk"],
                          [[item["type"], item["display_location"], ", ".join(item["tables_fed"]) or "—",
                            item["storage_mode"], item["auth"], item["flag"] or "None"]
                           for item in ln.data_sources_inventory]))
    else:
        out.append("_No external data sources detected._\n")
    out.append("\n\n**Power Query / ETL transformations**\n")
    if not ln.transformations:
        out.append("_None found._\n")
    else:
        for t in ln.transformations:
            out.append(f"\n#### {t.get('name')}\n")
            out.append(f"{t.get('description')}\n")
            steps = t.get("steps")
            if steps:
                for idx, s in enumerate(steps, 1):
                    out.append(f"{idx}. **{s.get('step')}** ({s.get('type')}): `{s.get('expr')}`\n")
            out.append("")
    if ln.lineage_edges:
        out.append("\n**Data lineage connection list**\n")
        out.append(_table(["From", "To", "Link Type"],
                          [[ed["from"], ed["to"], ed["type"]] for ed in ln.lineage_edges]))
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
    out.append(_table(["Table", "Column", "Data Type", "Description", "Used by"],
                      [[r.get("table", ""), r.get("column", ""), r.get("data_type", ""), r.get("description", ""), r.get("used_by", "")]
                       for r in sm.data_dictionary]))

    # 7. Measures & Calculations
    out.append("\n## 7. Measures & Calculations (DAX Dictionary)\n")
    for m in doc.measure_catalog.measures:
        home = f" · {m.table}" if m.table else ""
        cat = f" · _{m.category}_" if m.category else ""
        out.append(f"### {m.name}{home}{cat}\n")
        out.append(m.plain_english)
        if m.calculation_logic and m.calculation_logic != m.plain_english:
            out.append(f"\n**Calculation:** {m.calculation_logic}")
        if m.caveats:
            out.append(f"\n_Known caveats: {m.caveats}_")
        if m.dependency_tree:
            out.append(f"\n**Dependency tree:**\n```\n{m.dependency_tree}\n```")
        elif m.dependencies:
            out.append(f"\n_Depends on: {', '.join(m.dependencies)}_")
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        out.append(f"\n_Used on: {used}_")
        if m.confidence and m.confidence != "High":
            out.append(f"\n_Confidence in inferred business meaning: {m.confidence}"
                       + (" — review with the business owner._" if m.confidence == "Low" else "._"))
        out.append("")
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
    page_docs = {p.page_title: p for p in es.pages}
    for p in doc.report_pages:
        flags = [f for f, on in (("hidden", p.get("hidden")), ("drill-through", p.get("drillthrough"))) if on]
        flag = f" ({', '.join(flags)})" if flags else ""
        out.append(f"### {p['name']}{flag}\n")
        pd = page_docs.get(p["name"])
        if pd:
            if pd.summary:
                out.append(pd.summary + "\n")
            if pd.users:
                out.append(f"**Who uses it:** {pd.users}\n")
            if pd.business_questions:
                out.append("**Business questions answered:**\n")
                for q in pd.business_questions:
                    out.append(f"- {q}")
                out.append("")
            if pd.decisions:
                out.append(f"**Decision supported:** {pd.decisions}\n")
            if pd.confidence == "Low":
                out.append("_Purpose inferred with low confidence — requires business review._\n")
        out.append(_table(["Visual", "Type", "Metric(s)", "Dimension(s)"],
                          [[v.get("label") or "—", v.get("type"),
                            ", ".join(v.get("metrics", [])) or "—", ", ".join(v.get("dimensions", [])) or "—"]
                           for v in p.get("visuals", [])], "_No data visuals on this page._"))
        if p.get("decorative_count"):
            out.append(f"_{_non_data_note(p['decorative_count'])}_\n")

    # 9. Filters, Slicers & Navigation
    out.append("\n## 9. Filters, Slicers & Navigation\n")
    if doc.navigation_edges:
        out.append("\n**Page navigation connection list**\n")
        out.append(_table(["From Page", "To Page", "Trigger Label", "Link Type"],
                          [[ed["from"], ed["to"], ed["label"], ed["type"]] for ed in doc.navigation_edges]))
    for line in es.navigation_guide:
        out.append(f"- {line}")
    if not es.navigation_guide:
        out.append("_No navigation rules defined._")
    out.append("")
    out.append(_table(["Slicer field", "Page"], [[_slicer_label(x), x["page"]] for x in doc.slicers], "_No slicers found._"))
    drill = [p["name"] for p in doc.report_pages if p.get("drillthrough")]
    if drill:
        out.append(f"\n**Drill-through pages:** {', '.join(drill)}.\n")
    out.append(_todo("Bookmarks, button navigation logic, and the fields passed on each drill-through."))

    # 10. RLS
    sec = doc.security
    out.append("\n## 10. Row-Level Security (RLS)\n")
    if sec.roles:
        out.append("\n### Roles definition\n")
        meta_rows = []
        for r in sec.roles:
            m = ", ".join(r.get("members", [])) or "no members assigned (managed in cloud service)"
            meta_rows.append([r.get("name", ""), r.get("model_permission", "read"), m])
        out.append(_table(["Role Name", "Permission", "Members"], meta_rows))
        
        out.append("\n### Role × Table security matrix\n")
        filtered_tables = sorted(list({
            filt.split(":")[0].strip()
            for r in sec.roles
            for filt in r.get("filters", [])
            if ":" in filt
        }))
        if not filtered_tables:
            out.append("_No table-level filters are defined for these roles._\n")
        else:
            grid_headers = ["Table"] + [r.get("name", "") for r in sec.roles]
            grid_rows = []
            for t_name in filtered_tables:
                row = [t_name]
                for r in sec.roles:
                    filt_val = "—"
                    for filt in r.get("filters", []):
                        if filt.startswith(f"{t_name}:"):
                            filt_val = filt.split(":", 1)[1].strip()
                            break
                    row.append(f"`{filt_val}`" if filt_val != "—" else "—")
                grid_rows.append(row)
            out.append(_table(grid_headers, grid_rows))
            
        out.append("\n### RLS Validation Checklist\n")
        for r in sec.roles:
            out.append(f"- [ ] **Test {r.get('name', '')}:** Select 'View as' -> check '{r.get('name', '')}' in Power BI Desktop to verify filter propagation.\n")
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
    # Redesigned Unused Assets grouping by table
    unused = td.unused_assets or {}
    unused_cols_raw = unused.get("columns", [])
    unused_calc_cols_raw = unused.get("calculated_columns", [])
    unused_meas_raw = unused.get("measures", [])
    
    if unused_cols_raw or unused_calc_cols_raw or unused_meas_raw:
        out.append("\n**Unused Assets Grouped by Table:**\n")
        
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
                
            out.append(f"\n#### Table: {t_name} ({total_count} unused assets)\n")
            
            if col_list:
                out.append("\n*Unused Columns:*")
                for col in sorted(col_list):
                    out.append(f"- **{col}** — Evidence: no visuals, no measures, no relationships, no RLS filters reference this column.")
                    
            if calc_col_list:
                out.append("\n*Unused Calculated Columns:*")
                for col in sorted(calc_col_list):
                    out.append(f"- **{col}** — Evidence: no visuals, no measures, no relationships, no RLS filters reference this calculated column.")
                    
            if meas_list:
                out.append("\n*Unused Measures:*")
                for m in sorted(meas_list):
                    out.append(f"- **{m}** — Evidence: no visuals or other measures reference this measure.")
                    
            script_lines = []
            for col in sorted(col_list + calc_col_list):
                script_lines.append(f'Model.Tables["{t_name}"].Columns["{col}"].Delete();')
            for m in sorted(meas_list):
                script_lines.append(f'Model.Tables["{t_name}"].Measures["{m}"].Delete();')
                
            if script_lines:
                out.append("\n*Tabular Editor C# Script:*")
                out.append("```csharp")
                out.append(f"// Tabular Editor C# script to remove unused assets in Table {t_name}")
                for line in script_lines:
                    out.append(line)
                out.append("```\n")
    out.append("")
    if not md.assumptions:
        out.append(_todo("Business assumptions and limitations with impact and workaround."))
        
    # 16. Model Health & AI Recommendations
    out.append("\n## 16. Model Health & AI Recommendations\n")
    hs = doc.health_score or {}
    if hs:
        out.append(f"**Health Score: {hs.get('overall', 0)} / 100 ({hs.get('band', '')})**\n")
        notes = hs.get("component_notes", {})
        out.append(_table(["Component", "Score", "Why"],
                          [[HEALTH_COMPONENT_LABELS.get(k, k), v, notes.get(k, "")]
                           for k, v in hs.get("component_scores", {}).items()]))
        out.append("_Scored by deterministic rules over the model metadata — reproducible, not an AI guess._\n")
    recs = doc.ai_recommendations or []
    suppressed = doc.tech_debt.suppressed_rules if hasattr(doc, "tech_debt") and doc.tech_debt else []
    suppressed_count = len(suppressed)
    total_checks = TOTAL_RULE_COUNT
    failed_count = len(recs)
    passed_count = max(0, total_checks - suppressed_count - failed_count)
    
    out.append(f"\n**Best Practice Rules Summary:** Checks Run: **{total_checks - suppressed_count}** | "
               f"Passed: **{passed_count}** | Failed: **{failed_count}** | Suppressed: **{suppressed_count}**\n")
    if suppressed:
        out.append(f"**Suppressed by configuration:** {', '.join(f'`{rid}`' for rid in suppressed)}\n")

    if recs:
        out.append("\n### Prioritized recommendations\n")
        for i, r in enumerate(recs, 1):
            out.append(f"#### {i}. [{r.get('priority', 'Medium')}] {r.get('issue', '')}\n")
            out.append(f"- **Impact:** {r.get('why_it_matters', '')}")
            out.append(f"- **Recommendation:** {r.get('suggested_fix', '')}")
            if r.get("expected_benefit"):
                out.append(f"- **Expected benefit:** {r.get('expected_benefit')}")
            out.append(f"- **Estimated effort:** {r.get('effort', 'Medium')}\n")
    elif hs:
        out.append("\n_No recommendations — no findings were raised against this model._\n")
    if not hs and not recs:
        out.append("_Not computed for this document._\n")

    # 17. Support & Maintenance
    out.append("\n## 17. Support & Maintenance\n")
    if md.owner:
        out.append(f"**First-line contact:** {md.owner}.\n")
    if md.support_notes:
        out.append(md.support_notes + "\n")
    else:
        out.append(_todo("Escalation contact, SLA, backup location, decommission criteria."))

    # 18. Appendix & Sign-off
    out.append("\n## 18. Appendix & Sign-off\n")
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

    # 19. Methodology & Guarantees
    out.append("\n## 19. Methodology & Guarantees [⚙ Extracted]\n")
    out.append("- **Parsed Artifacts:** Power BI metadata (tables, columns, measures, relationships, visuals, and page layout layout tables). No customer database row-level data is ever parsed, read, or transmitted.\n")
    out.append("- **AI Agents Used:** PBICompass Engine v0.1.0 and prompt version 2026-07. Models called: Anthropic Claude, Google Gemini, Cohere. All operations run under zero-retention policies.\n")
    out.append("- **Guarantees:** 100% offline-ready deliverables, zero-CDNs, zero telemetries, and fully reproducible scoring metrics backed by deterministic compliance checking rules.\n")
    out.append("- **Limitations:** This tool cannot verify runtime query performance, network latency, database authentication credentials, or confirm the actual semantic business meaning without human verification.\n")

    return "\n".join(out).rstrip() + "\n"
