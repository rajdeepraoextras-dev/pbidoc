"""Render a :class:`Document` to a Word ``.docx`` — with no third-party deps.

A ``.docx`` is just a ZIP of XML parts (OOXML). We hand-write the minimal valid
set (content types, relationships, styles, and the document body) so a real,
editable Word document is produced without ``python-docx``/``lxml``/Pandoc. The
named heading styles make it navigable and TOC-able in Word.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..agents.audit_rules import TOTAL_RULE_COUNT
from ..schemas.document import Document
from ._docx_writer import _Docx
from ._shared import HEALTH_COMPONENT_LABELS
from ._shared import MODEL_DIAGRAM_RENDERED
from ._shared import format_timestamp as _fmt_ts
from ._shared import is_local_path as _is_local_path
from ._shared import non_data_note as _non_data_note
from ._shared import section_provenance
from ._shared import slicer_field_label as _slicer_label


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
    subtitle_str = f"{md.target_audience or ''} · generated {_fmt_ts(md.generated_at)}"
    if getattr(md, "score_trend", None):
        subtitle_str += f" · Score Trend: {md.score_trend}"
    d.para([d._run(subtitle_str, italic=True)])
    
    from ._shared import compute_completeness
    pct, missing_count, _ = compute_completeness(md)
    d.para([d._run(f"Completeness: {pct}% ({missing_count} fields awaiting input)", italic=True)])

    def todo(t):
        d.para([d._run("✎ To complete: " + t, italic=True)])

    def _t(rows):
        return [[str(c) for c in r] for r in rows]

    def _badge(section_num: int) -> str:
        return f" [{section_provenance(section_num, md)}]"

    # 1. Document Control
    d.heading(1, f"1. Document Control{_badge(1)}")
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
    d.label("Generated", _fmt_ts(md.generated_at))

    if getattr(doc, "changelog", None):
        d.heading(2, "Changes since last documentation")
        _add_para_with_md(d, doc.changelog)

    missing_doc_control = [f for f, v in [("Version", md.version), ("Status", md.status), ("Author", md.author),
                                          ("Reviewer", md.reviewer), ("Classification", md.classification)] if not v]
    if missing_doc_control:
        todo(f"Complete missing document control fields: {', '.join(missing_doc_control)}")

    # 2. Executive Summary
    es = doc.executive_summary
    d.heading(1, f"2. Executive Summary{_badge(2)}")
    _add_para_with_md(d, es.core_purpose)
    if md.business_decision:
        d.heading(2, "Primary Business Decision / Impact")
        d.para(md.business_decision)
    headline = [m.name for m in doc.measure_catalog.measures][:6]
    if headline:
        d.para([d._run("Headline metrics: ", bold=True), d._run(", ".join(headline) + ".")])
    if not md.business_decision:
        todo("The primary business decision this dashboard drives (e.g. weekly sales planning).")

    # 3. Business Requirements. requirements_matrix (Day 4) supersedes the
    # plain text dump whenever it's non-empty.
    d.heading(1, f"3. Business Requirements{_badge(3)}")
    if doc.requirements_matrix:
        covered = sum(1 for r in doc.requirements_matrix if r["status"] in ("Covered", "Partial"))
        d.para([d._run(
            f"Requirements Traceability Matrix — {covered}/{len(doc.requirements_matrix)} at least partially "
            f"covered by the report's own measures, columns, and pages.", italic=True)])
        rag_rows = []
        for r in doc.requirements_matrix:
            evidence = ", ".join(e["name"] for e in r.get("evidence", [])) or "—"
            rag_rows.append([r.get("priority") or "—", r["text"], r["status"], evidence])
        d.table(["Priority", "Requirement", "Status", "Evidence"], _t(rag_rows))
    elif md.requirements:
        for req in md.requirements.split('\n'):
            if req.strip():
                d.para(req)
    else:
        todo("Business requirements have not yet been captured; confirm scope with the business owner.")

    # 4. Audience & Stakeholders
    d.heading(1, f"4. Audience & Stakeholders{_badge(4)}")
    d.table(["Role", "Name / Group", "Access"],
            [["Business Owner", md.owner or "—", "Edit / sign-off"],
             ["Primary Users", md.target_audience or "—", "View"],
             ["Author / Creator", md.author or "—", "Modify / Publish"]])
    todo("Confirm other stakeholders (Data Owner, Developer/Maintainer, and per-group access levels).")

    # 5. Data Sources
    ln = doc.lineage
    d.heading(1, f"5. Data Sources{_badge(5)}")
    if ln.data_sources_inventory:
        d.table(["Source Type", "Location / Host", "Tables Fed", "Storage Mode", "Authentication", "Flag / Risk"],
                _t([[item["type"], item["display_location"], ", ".join(item["tables_fed"]) or "—",
                     item["storage_mode"], item["auth"], item["flag"] or "None"]
                    for item in ln.data_sources_inventory]))
    else:
        for s in ln.source_systems or ["No external data sources detected."]:
            if _is_local_path(s):
                d.bullet(s + " ⚠️ [Hardcoded local path]")
            else:
                d.bullet(s)
    d.heading(2, "Power Query / ETL transformations")
    if not ln.transformations:
        d.para("No Power Query transformations found.")
    else:
        for t in ln.transformations:
            d.heading(3, t.get("name", ""))
            if t.get("description"):
                d.para(t["description"])
            steps = t.get("steps")
            if steps:
                for i, s in enumerate(steps, 1):
                    d.para([d._run(f"{i}. {s.get('step')} ", bold=True), d._run(f"({s.get('type')}): {s.get('expr')}")])
    if ln.lineage_edges:
        d.heading(2, "Data lineage")
        d.table(["From", "To", "Link Type"],
                _t([[ed["from"], ed["to"], ed["type"]] for ed in ln.lineage_edges]))
    todo("Per source: authentication, owning team, and known data latency.")

    # 6. Data Model
    sm = doc.semantic_model
    d.heading(1, f"6. Data Model{_badge(6)}")
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
    d.table(["Table", "Column", "Data Type", "Description", "Used by"],
            _t([[r.get("table", ""), r.get("column", ""), r.get("data_type", ""), r.get("description", ""), r.get("used_by", "")]
                for r in sm.data_dictionary]))

    # 7. Measures
    d.heading(1, f"7. Measures & Calculations (DAX Dictionary){_badge(7)}")
    for m in doc.measure_catalog.measures:
        suffix = f" · {m.category}" if m.category else ""
        d.heading(3, m.name + suffix)
        operates_on = [t for t in (m.operates_on or []) if t and t != m.table]
        if m.table:
            line = f"Home table: {m.table}"
            if operates_on:
                line += f" · Operates on: {', '.join(operates_on)}"
            d.para([d._run(line, italic=True)])
        d.para(m.plain_english)
        if m.calculation_logic and m.calculation_logic != m.plain_english:
            d.para([d._run("Calculation: ", bold=True), d._run(m.calculation_logic)])
        if m.caveats:
            d.para([d._run("Known caveats: " + m.caveats, italic=True)])
        if m.dependency_tree:
            d.para([d._run("Dependency tree:", bold=True)])
            d.code(m.dependency_tree)
        elif m.dependencies:
            d.para([d._run("Depends on: " + ", ".join(m.dependencies), italic=True)])
        used = ", ".join(m.used_on) if m.used_on else "not placed on a page"
        d.para([d._run("Used on: " + used, italic=True)])
        if m.confidence and m.confidence != "High":
            suffix_note = " — review with the business owner" if m.confidence == "Low" else ""
            d.para([d._run(f"Confidence in inferred business meaning: {m.confidence}{suffix_note}.", italic=True)])
        d.code(m.dax)
    if doc.calculated_columns:
        d.heading(2, "Calculated columns")
        d.table(["Table", "Column", "Expression"],
                _t([[c.get("table", ""), c.get("column", ""), (c.get("expression", "") or "").replace("\n", " ")]
                    for c in doc.calculated_columns]))

    # 8. Report Pages & Visuals
    d.heading(1, f"8. Report Pages & Visuals{_badge(8)}")
    page_docs = {p.page_title: p for p in es.pages}
    for p in doc.report_pages:
        flags = [f for f, on in (("hidden", p.get("hidden")), ("drill-through", p.get("drillthrough"))) if on]
        d.heading(3, p["name"] + (f" ({', '.join(flags)})" if flags else ""))
        pd = page_docs.get(p["name"])
        if pd:
            if pd.summary:
                d.para(pd.summary)
            if pd.users:
                d.para([d._run("Who uses it: ", bold=True), d._run(pd.users)])
            if pd.business_questions:
                d.para([d._run("Business questions answered:", bold=True)])
                for q in pd.business_questions:
                    d.bullet(q)
            if pd.decisions:
                d.para([d._run("Decision supported: ", bold=True), d._run(pd.decisions)])
            if pd.confidence == "Low":
                d.para([d._run("Purpose inferred with low confidence — requires business review.", italic=True)])
        d.table(["Visual", "Type", "Metrics", "Dimensions"],
                _t([[v.get("label") or "—", v.get("type"), ", ".join(v.get("metrics", [])) or "—",
                     ", ".join(v.get("dimensions", [])) or "—"] for v in p.get("visuals", [])]) or [["—", "—", "—", "—"]])
        if p.get("decorative_count"):
            d.para([d._run(_non_data_note(p["decorative_count"]), italic=True)])

    # 9. Filters, Slicers & Navigation
    d.heading(1, f"9. Filters, Slicers & Navigation{_badge(9)}")
    if doc.navigation_edges:
        d.heading(2, "Page navigation connection list")
        d.table(["From Page", "To Page", "Trigger Label", "Link Type"],
                _t([[ed["from"], ed["to"], ed["label"], ed["type"]] for ed in doc.navigation_edges]))
    d.table(["Slicer field", "Page"], _t([[_slicer_label(x), x["page"]] for x in doc.slicers]) or [["—", "—"]])
    drill = [p["name"] for p in doc.report_pages if p.get("drillthrough")]
    if drill:
        d.para([d._run("Drill-through pages: ", bold=True), d._run(", ".join(drill) + ".")])
    todo("Bookmarks, button navigation logic, and drill-through fields.")

    # 10. RLS
    sec = doc.security
    d.heading(1, f"10. Row-Level Security (RLS){_badge(10)}")
    for disc in (getattr(sec, "discrepancies", None) or []):
        d.para([d._run("⚠ Discrepancy — human input vs. model", bold=True)])
        d.para([d._run("You stated: ", bold=True), d._run(disc.get("human_claim", ""))])
        d.para([d._run("The model shows: ", bold=True), d._run(disc.get("model_finding", ""))])
        d.para([d._run(disc.get("explanation", ""), italic=True)])
    if sec.roles:
        d.heading(2, "Roles definition")
        d.table(["Role Name", "Permission", "Members"],
                _t([[r.get("name", ""), r.get("model_permission", "read"),
                     ", ".join(r.get("members", [])) or "no members assigned (managed in cloud service)"]
                    for r in sec.roles]))

        d.heading(2, "Role × Table security matrix")
        filtered_tables = sorted({
            filt.split(":")[0].strip()
            for r in sec.roles
            for filt in r.get("filters", [])
            if ":" in filt
        })
        if not filtered_tables:
            d.para("No table-level filters are defined for these roles.")
        else:
            grid_rows = []
            for t_name in filtered_tables:
                row = [t_name]
                for r in sec.roles:
                    filt_val = "—"
                    for filt in r.get("filters", []):
                        if filt.startswith(f"{t_name}:"):
                            filt_val = filt.split(":", 1)[1].strip()
                            break
                    row.append(filt_val)
                grid_rows.append(row)
            d.table(["Table"] + [r.get("name", "") for r in sec.roles], _t(grid_rows))

        d.heading(2, "RLS Validation Checklist")
        for r in sec.roles:
            d.bullet(f"Test {r.get('name', '')}: Select 'View as' → check '{r.get('name', '')}' in Power BI Desktop to verify filter propagation.")
    else:
        d.para("No row-level security roles are defined in this model.")
        
    if md.security_notes:
        d.heading(2, "Security Validation & Scope")
        d.para(md.security_notes)
    else:
        todo("Confirm each role was tested with 'View as role', and note OLS rules.")

    # 11. Refresh, Gateway & Performance
    d.heading(1, f"11. Refresh, Gateway & Performance{_badge(11)}")
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
    d.heading(1, f"12. Deployment & Environment{_badge(12)}")
    if md.deployment_notes:
        for line in md.deployment_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Dev / Test / Production workspaces, app URLs, deployment method, parameters.")
        
    # 13. Access & Permissions
    d.heading(1, f"13. Access & Permissions{_badge(13)}")
    if md.access_notes:
        for line in md.access_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Workspace roles and app access per group, with justification.")
        
    # 14. Glossary. doc.glossary_entries already carries the merged result
    # (Day 3) — human terms override/append, never replace the whole table.
    d.heading(1, f"14. Data Dictionary / Glossary{_badge(14)}")
    d.para("Column-level data dictionary is in section 6.")
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
    d.heading(1, f"15. Known Issues, Assumptions & Limitations{_badge(15)}")
    for n in td.notes:
        d.bullet(n)
    if md.assumptions:
        d.heading(2, "Business Assumptions & Limitations")
        d.para(md.assumptions)
    if td.orphaned_measures:
        d.heading(2, "Orphaned measures (defined but not used on any page)")
        for m in td.orphaned_measures:
            d.bullet(m)

    unused = td.unused_assets or {}
    unused_cols_raw = unused.get("columns", [])
    unused_calc_cols_raw = unused.get("calculated_columns", [])
    unused_meas_raw = unused.get("measures", [])
    if unused_cols_raw or unused_calc_cols_raw or unused_meas_raw:
        d.heading(2, "Unused Assets Grouped by Table")

        table_unused: dict = {}
        for c in unused_cols_raw:
            table_unused.setdefault(c["table"], {"columns": [], "calculated_columns": [], "measures": []})["columns"].append(c["column"])
        for c in unused_calc_cols_raw:
            table_unused.setdefault(c["table"], {"columns": [], "calculated_columns": [], "measures": []})["calculated_columns"].append(c["column"])
        m_to_tbl = {m.name: m.table for m in doc.measure_catalog.measures if m.table}
        for m_name in unused_meas_raw:
            tbl = m_to_tbl.get(m_name, "Unassigned Measures")
            table_unused.setdefault(tbl, {"columns": [], "calculated_columns": [], "measures": []})["measures"].append(m_name)

        for t_name, assets in sorted(table_unused.items()):
            col_list, calc_col_list, meas_list = assets["columns"], assets["calculated_columns"], assets["measures"]
            total_count = len(col_list) + len(calc_col_list) + len(meas_list)
            if not total_count:
                continue
            d.heading(3, f"Table: {t_name} ({total_count} unused assets)")
            for col in sorted(col_list):
                d.bullet(f"{col} — Evidence: no visuals, no measures, no relationships, no RLS filters reference this column.")
            for col in sorted(calc_col_list):
                d.bullet(f"{col} — Evidence: no visuals, no measures, no relationships, no RLS filters reference this calculated column.")
            for m in sorted(meas_list):
                d.bullet(f"{m} — Evidence: no visuals or other measures reference this measure.")
            script_lines = [f'Model.Tables["{t_name}"].Columns["{col}"].Delete();' for col in sorted(col_list + calc_col_list)]
            script_lines += [f'Model.Tables["{t_name}"].Measures["{m}"].Delete();' for m in sorted(meas_list)]
            if script_lines:
                d.para([d._run("Tabular Editor C# Script:", bold=True)])
                d.code(f'// Tabular Editor C# script to remove unused assets in Table {t_name}\n' + "\n".join(script_lines))

    if not md.assumptions:
        todo("Business assumptions and limitations with impact and workaround.")
        
    # 16. Model Health & AI Recommendations
    d.heading(1, f"16. Model Health & AI Recommendations{_badge(16)}")
    hs = doc.health_score or {}
    if hs:
        d.heading(2, f"Health Score: {hs.get('overall', 0)} / 100 ({hs.get('band', '')})")
        notes = hs.get("component_notes", {})
        d.table(["Component", "Score", "Why"],
                _t([[HEALTH_COMPONENT_LABELS.get(k, k), v, notes.get(k, "")]
                    for k, v in hs.get("component_scores", {}).items()]))
        d.para([d._run("Scored by deterministic rules over the model metadata — reproducible, not an AI guess.", italic=True)])
    top_cluster = getattr(doc, "top_cluster", None)
    if top_cluster:
        d.heading(2, f"Root cause: {top_cluster.get('root_cause', '')}")
        d.para(top_cluster.get("narrative", ""))
        if top_cluster.get("rule_ids"):
            d.para([d._run("Related findings: ", bold=True), d._run(", ".join(top_cluster["rule_ids"]))])
    recs = doc.ai_recommendations or []
    suppressed = doc.tech_debt.suppressed_rules if hasattr(doc, "tech_debt") and doc.tech_debt else []
    suppressed_count = len(suppressed)
    total_checks = doc.checks_run or TOTAL_RULE_COUNT
    passed_count = doc.checks_passed
    failed_count = doc.checks_failed

    d.para([
        d._run("Best Practice Rules Summary: ", bold=True),
        d._run(f"Checks Run: {total_checks - suppressed_count}  |  Passed: {passed_count}  |  Failed: {failed_count}  |  Suppressed: {suppressed_count}")
    ])
    if suppressed:
        d.para([
            d._run("Suppressed by configuration: ", bold=True),
            d._run(", ".join(suppressed))
        ])

    if recs:
        d.heading(2, "Prioritized recommendations")
        for i, r in enumerate(recs, 1):
            d.heading(3, f"{i}. [{r.get('priority', 'Medium')}] {r.get('issue', '')}")
            d.para([d._run("Impact: ", bold=True), d._run(r.get("why_it_matters", ""))])
            fix_text = r.get("suggested_fix", "")
            cleaned_fix = re.sub(r"```[a-z]*\n", "", fix_text).replace("```", "")
            d.para([d._run("Recommendation: ", bold=True), d._run(cleaned_fix)])
            if r.get("expected_benefit"):
                d.para([d._run("Expected benefit: ", bold=True), d._run(r.get("expected_benefit"))])
            d.para([d._run("Estimated effort: ", bold=True), d._run(r.get("effort", "Medium"))])
    elif hs:
        d.para("No recommendations — no findings were raised against this model.")
    if not hs and not recs:
        d.para("Not computed for this document.")

    # 17. Support & Maintenance
    d.heading(1, f"17. Support & Maintenance{_badge(17)}")
    if md.owner:
        d.label("First-line contact", md.owner)
    if md.support_notes:
        for line in md.support_notes.split('\n'):
            if line.strip():
                d.para(line)
    else:
        todo("Escalation contact, SLA, backup location, decommission criteria.")
        
    # 18. Appendix & Sign-off
    d.heading(1, f"18. Appendix & Sign-off{_badge(18)}")
    if MODEL_DIAGRAM_RENDERED:
        d.para("The model diagram is in the HTML / section 6.")
    generated_date = (md.generated_at or "")[:10]
    # owner -> Business Owner, author -> Developer, reviewer -> Approver
    # (Day 3) — each row filled from the metadata field it corresponds to.
    sign_off_rows = [
        ["Business Owner", md.owner or "", "Business Owner", generated_date if md.owner else ""],
        ["Developer", md.author or "TBC", "BI Developer", generated_date],
        ["Approver", md.reviewer or "", "Reviewer", generated_date if md.reviewer else ""],
    ]
    d.table(["Sign-off Role", "Name", "Title / Role", "Date"], _t(sign_off_rows))
    d.para([d._run("Reminder: ", bold=True), d._run("Obtain sign-off before sharing with stakeholders.")])

    # 19. Methodology & Guarantees
    d.heading(1, "19. Methodology & Guarantees [Extracted]")
    d.bullet([d._run("Parsed Artifacts: ", bold=True), d._run("Power BI metadata (tables, columns, measures, relationships, visuals, and page layout tables). No customer database row-level data is ever parsed, read, or transmitted.")])
    d.bullet([d._run("AI Agents Used: ", bold=True), d._run("PBICompass Engine v0.1.0 and prompt version 2026-07. Models called: Anthropic Claude, Google Gemini, Cohere. All operations run under zero-retention policies.")])
    d.bullet([d._run("Guarantees: ", bold=True), d._run("100% offline-ready deliverables, zero CDNs, zero telemetry, and fully reproducible scoring metrics backed by deterministic compliance checking rules.")])
    d.bullet([d._run("Limitations: ", bold=True), d._run("This tool cannot verify runtime query performance, network latency, database authentication credentials, or confirm the actual semantic business meaning without human verification.")])

    d.save(out_path)
    return out_path
