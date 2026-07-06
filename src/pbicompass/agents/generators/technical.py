"""Technical Documentation generator — ``SemanticModel`` -> ``Document``.

This is the original (Phase-0-and-earlier) orchestrator body, moved here
verbatim so it can sit alongside the other document generators. Fans out to
the agents and assembles the seven-section document. The three prose agents
(Business Analyst, DAX Translator, Data Modeler) use the LLM when a client is
provided and fall back to the deterministic engine on any failure. Metadata,
lineage, security, and the tech-debt audit are always deterministic — the
orphaned-measure audit in particular is a set difference, never a guess.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional

from ..context import JobAIContext, build_job_context
from ...schemas.document import (
    Document,
    DocumentMetadata,
    ExecutiveSummary,
    LineageArchitecture,
    MeasureCatalog,
    MeasureEntry,
    PageSummary,
    SecurityGovernance,
    SemanticModelDoc,
    TechDebtAudit,
    VisualExplainer,
)
from ...schemas.model import SemanticModel
from .. import audit_rules, io
from ..critic import apply_critic_pass, apply_results
from ..deterministic import (
    business_analyst_deterministic,
    data_modeler_deterministic,
    relationship_lines,
    translate_dax,
)
from ..llm import LLMClient
from ..report_facts import (
    calc_columns,
    data_source_summaries,
    detect_hardcoded_years,
    find_referenced_tables,
    first_sentence,
    local_path_sources,
    report_pages,
    slicers,
    table_priority_key,
)
from ..usage import measure_dependencies, measure_usage, used_measure_names
from .base import Warn, call_llm, call_llm_with_retry

_call = call_llm  # local alias — keeps the body below byte-for-byte identical


# -- I. Document Metadata -----------------------------------------------------
def _metadata(model: SemanticModel, owner, audience, refresh,
              version=None, status=None, author=None, reviewer=None,
              classification=None, business_decision=None, requirements=None,
              security_notes=None, refresh_notes=None, deployment_notes=None,
              access_notes=None, glossary=None, assumptions=None, support_notes=None) -> DocumentMetadata:
    overridden = getattr(model.meta, "overridden_fields", [])
    return DocumentMetadata(
        report_name=model.report_name,
        owner=owner,
        refresh_schedule=refresh,
        target_audience=audience or "BI developers and business stakeholders",
        source_format=model.meta.source_format,
        generated_at=model.meta.generated_at,
        version=version,
        status=status,
        author=author,
        reviewer=reviewer,
        classification=classification,
        business_decision=business_decision,
        requirements=requirements,
        security_notes=security_notes,
        refresh_notes=refresh_notes,
        deployment_notes=deployment_notes,
        access_notes=access_notes,
        glossary=glossary,
        assumptions=assumptions,
        support_notes=support_notes,
        overridden_fields=list(overridden),
    )


# -- II. Executive Summary ----------------------------------------------------
def _executive_summary(model: SemanticModel, client, warn, ai_context: Optional[JobAIContext]) -> ExecutiveSummary:
    """Batches the Business Analyst prompt by page (``io.business_analyst_
    batches``) so one failed/invalid batch degrades only the pages it
    covers, not the whole Executive Summary. Each batch gets one retry
    (jittered) before falling back; a batch that still fails keeps its pages
    on the deterministic engine and names them in a warning, rather than
    silently degrading the whole document with no signal (1.4)."""
    deterministic = business_analyst_deterministic(model)
    if client is None:
        return deterministic

    pages = list(deterministic.pages)
    core_purpose = deterministic.core_purpose
    navigation_guide = list(deterministic.navigation_guide)
    complex_visual_explainers = list(deterministic.complex_visual_explainers)
    nav_seen = set(navigation_guide)
    explainer_seen = {(e.visual, e.page) for e in complex_visual_explainers}
    core_purpose_set = False
    offset = 0

    for batch in io.business_analyst_batches(model):
        batch_size = len(batch["pages"])
        slice_titles = [p.page_title for p in pages[offset:offset + batch_size]]
        data = call_llm_with_retry(client, io.BUSINESS_ANALYST_SYSTEM, batch, io.BUSINESS_ANALYST_SCHEMA,
                                    ai_context=ai_context, name="Business Analyst")
        batch_pages = None
        if data:
            try:
                batch_pages = [PageSummary(**p) for p in data["pages"]]
            except (KeyError, TypeError) as exc:
                warn(f"Business Analyst: malformed response for pages: {', '.join(slice_titles)} "
                     f"({exc}) — deterministic summary used")

        if batch_pages is not None:
            for i, page in enumerate(batch_pages):
                if offset + i < len(pages):
                    pages[offset + i] = page
            if not core_purpose_set and data.get("core_purpose"):
                core_purpose, core_purpose_set = data["core_purpose"], True
            for nav in data.get("navigation_guide", []):
                if nav not in nav_seen:
                    nav_seen.add(nav)
                    navigation_guide.append(nav)
            for e in data.get("complex_visual_explainers", []):
                try:
                    explainer = VisualExplainer(**e)
                except TypeError:
                    continue
                key = (explainer.visual, explainer.page)
                if key not in explainer_seen:
                    explainer_seen.add(key)
                    complex_visual_explainers.append(explainer)
        elif data is None and slice_titles:
            warn(f"Business Analyst: AI narrative unavailable for pages: {', '.join(slice_titles)} "
                 f"— deterministic summary used")

        offset += batch_size

    return ExecutiveSummary(
        core_purpose=core_purpose, pages=pages,
        navigation_guide=navigation_guide, complex_visual_explainers=complex_visual_explainers,
    )


# -- III. Lineage & Architecture ----------------------------------------------
def _summarize_m(m: str) -> str:
    conn = re.search(r"(\w+\.\w+)\s*\(", m)
    item = re.search(r'Item\s*=\s*"([^"]+)"', m)
    schema = re.search(r'Schema\s*=\s*"([^"]+)"', m)
    parts = []
    if item:
        target = f"{schema.group(1)}.{item.group(1)}" if schema else item.group(1)
        parts.append(f"loads {target}")
    if conn:
        parts.append(f"via {conn.group(1)}")
    desc = " ".join(parts) or "custom Power Query transformation"
    return desc[0].upper() + desc[1:] + "."


def _lineage(model: SemanticModel) -> LineageArchitecture:
    from ...render._lineage import build_lineage_data
    from ...parsers.m_steps import split_m_steps, classify_step_function
    sources = data_source_summaries(model)

    transforms: list[dict[str, str]] = []
    for e in model.expressions:
        if e.expression and e.kind == "expression":
            steps_raw = split_m_steps(e.expression)
            steps = [{"step": name, "expr": expr, "type": classify_step_function(expr)} for name, expr in steps_raw]
            transforms.append({
                "name": e.name,
                "description": _summarize_m(e.expression),
                "raw_m": e.expression,
                "steps": steps
            })
    for t in model.tables:
        for p in t.partitions:
            if p.source_kind == "m" and p.expression:
                steps_raw = split_m_steps(p.expression)
                steps = [{"step": name, "expr": expr, "type": classify_step_function(expr)} for name, expr in steps_raw]
                transforms.append({
                    "name": t.name,
                    "description": _summarize_m(p.expression),
                    "raw_m": p.expression,
                    "steps": steps
                })
            elif p.source_kind == "calculated" and p.expression:
                transforms.append({"name": t.name, "description": "Calculated table defined in DAX."})

    edges, svg = build_lineage_data(model)
    from ...render._lineage import build_data_sources_inventory
    inventory = build_data_sources_inventory(model)
    return LineageArchitecture(
        source_systems=sources,
        transformations=transforms,
        lineage_svg=svg,
        lineage_edges=edges,
        data_sources_inventory=inventory
    )


# -- IV. Semantic Model -------------------------------------------------------
def _column_descriptions(model: SemanticModel, client, warn, ai_context: Optional[JobAIContext]) -> dict[tuple[str, str], str]:
    descriptions = {}
    for t in model.tables:
        for c in t.columns:
            if c.is_hidden:
                continue
            if c.description:
                desc = c.description
            elif c.is_calculated:
                desc = "Calculated column."
            else:
                c_lower = c.name.lower()
                if c_lower.endswith("id") or c_lower.endswith("key"):
                    desc = f"Key identifier; used to join {t.name} to related tables."
                else:
                    # Honest default — never fabricate business intent (the LLM
                    # pass below overwrites this when it can actually infer one).
                    desc = "Unknown — requires business confirmation."
            descriptions[(t.name, c.name)] = desc

    if client is not None:
        data = _call(client, io.COLUMN_DESCRIBER_SYSTEM, io.column_describer_input(model),
                     io.COLUMN_DESCRIBER_SCHEMA, warn, "Column Describer", ai_context=ai_context)
        if data:
            for item in data.get("columns", []):
                t_name = item.get("table")
                c_name = item.get("column")
                desc = item.get("description")
                if t_name and c_name and desc:
                    descriptions[(t_name, c_name)] = desc
    return descriptions


def _semantic_model(model: SemanticModel, client, warn, col_descs: dict,
                     ai_context: Optional[JobAIContext]) -> SemanticModelDoc:
    data_dictionary = []
    for t in model.tables:
        for c in t.columns:
            if c.is_hidden:
                continue
            
            # Relationships
            rel_list = []
            for r in model.relationships:
                if (r.from_table == t.name and r.from_column == c.name) or (r.to_table == t.name and r.to_column == c.name):
                    other_table = r.to_table if r.from_table == t.name else r.from_table
                    other_col = r.to_column if r.from_table == t.name else r.from_column
                    rel_list.append(f"'{other_table}[{other_col}]'")
            
            # Measures
            meas_list = []
            for m in model.all_measures():
                ref_pattern1 = f"{t.name}[{c.name}]".lower()
                ref_pattern2 = f"'{t.name}'[{c.name}]".lower()
                expr_lower = (m.expression or "").lower()
                if ref_pattern1 in expr_lower or ref_pattern2 in expr_lower:
                    meas_list.append(m.name)
            
            # Visuals / Pages
            vis_list = []
            for p in model.pages:
                for v in p.visuals:
                    field_ref = f"{t.name}.{c.name}".lower()
                    if any(f.lower() == field_ref for f in v.fields):
                        if p.display_name not in vis_list:
                            vis_list.append(p.display_name)
            
            # RLS
            rls_list = []
            for role in model.roles:
                for tp in role.table_permissions:
                    if tp.table == t.name and c.name.lower() in tp.filter_expression.lower():
                        rls_list.append(role.name)
            
            parts = []
            if rel_list:
                parts.append(f"{len(rel_list)} relationship(s)")
            if meas_list:
                parts.append(f"{len(meas_list)} measure(s) ({', '.join(meas_list[:2])}{'...' if len(meas_list) > 2 else ''})")
            if vis_list:
                parts.append(f"{len(vis_list)} page(s) ({', '.join(vis_list[:2])}{'...' if len(vis_list) > 2 else ''})")
            if rls_list:
                parts.append(f"RLS role(s): {', '.join(rls_list)}")
                
            used_by_text = "; ".join(parts) if parts else "not referenced — see unused assets"
            
            prov = "Extracted"
            if getattr(c, "provenance", None) == "Human-provided":
                prov = "Human-provided"
            elif (t.name, c.name) in col_descs:
                prov = "AI-inferred"
            data_dictionary.append({
                "table": t.name,
                "column": c.name,
                "data_type": c.data_type,
                "description": col_descs.get((t.name, c.name), c.description or ("Calculated column" if c.is_calculated else "")),
                "used_by": used_by_text,
                "provenance": prov,
            })
    rels = relationship_lines(model)
    summary, risks = data_modeler_deterministic(model)
    if client is not None:
        data = _call(client, io.DATA_MODELER_SYSTEM, io.data_modeler_input(model),
                     io.DATA_MODELER_SCHEMA, warn, "Data Modeler", ai_context=ai_context)
        if data and "summary" in data:
            summary = data["summary"]
            risks = list(data.get("risks", risks))

    for target in local_path_sources(model):
        risks.append(f"Hardcoded local path detected — replace with a parameterized path or gateway source before production deployment. (Path: {target})")
    tables = [
        {"name": t.name, "kind": t.kind, "columns": len(t.columns), "measures": len(t.measures)}
        for t in model.tables
    ]
    edges = [
        {"from": r.from_table, "to": r.to_table, "from_card": r.from_cardinality,
         "to_card": r.to_cardinality, "cross_filter": r.cross_filter, "is_active": r.is_active,
         "from_column": r.from_column, "to_column": r.to_column}
        for r in model.relationships
    ]
    return SemanticModelDoc(summary=summary, data_dictionary=data_dictionary, relationships=rels,
                            risks=risks, tables=tables, relationship_edges=edges)


# -- V. Measure Catalog -------------------------------------------------------
def _measure_catalog(model: SemanticModel, client, warn, ai_context: Optional[JobAIContext]) -> MeasureCatalog:
    measures = model.all_measures()
    entries = [
        MeasureEntry(name=m.name, table=m.table, dax=m.expression, format_string=m.format_string)
        for m in measures
    ]
    usage = measure_usage(model)

    # Phase 0: the DAX Translator runs once per job (``build_job_context``),
    # not once per document type — consume the shared result instead of
    # re-calling the same agent over the same measures.
    translations = ai_context.translations if ai_context is not None else None

    from ...render._measure_deps import build_measure_dependency_tree, render_measure_dependency_graph_svg
    from ..deterministic import _measure_refs
    measure_names = {m.name for m in measures}
    measure_deps_map = {}
    for m in measures:
        measure_deps_map[m.name] = [d for d in _measure_refs(m.expression or "") if d in measure_names and d != m.name]

    for entry, measure in zip(entries, measures):
        t = translations.get(entry.name) if translations else None
        if t:
            entry.plain_english = t.get("plain_english", "")
            entry.calculation_logic = t.get("calculation_logic", "")
            entry.caveats = t.get("caveats", "")
            entry.category = t.get("category", "")
            entry.confidence = t.get("confidence", "")
        else:
            entry.plain_english, entry.caveats, entry.category = translate_dax(
                measure.name, measure.expression, measure.format_string
            )
            # The deterministic line is derived from the DAX, so it doubles as
            # the calculation logic; the business meaning behind it is
            # unverified, hence the explicit Low confidence.
            entry.calculation_logic = entry.plain_english
            entry.confidence = "Low"
        if getattr(measure, "provenance", None) == "Human-provided":
            # A human-supplied description (enrichment file, 5.1) is the
            # business definition — it overrides the LLM/DAX-derived guess,
            # which stays available as calculation_logic ("how it computes",
            # distinct from "what it means").
            entry.plain_english = measure.description
            entry.confidence = ""
            entry.provenance = "Human-provided"
        elif t:
            entry.provenance = "AI-inferred"
        else:
            entry.provenance = "Extracted"
        entry.dependencies = measure_dependencies(measure.expression, measure_names)
        entry.used_on = usage.get(entry.name, [])
        tree_lines = build_measure_dependency_tree(entry.name, measure_deps_map, {entry.name})
        entry.dependency_tree = "\n".join(tree_lines)

        # Cross-check measure table attribution
        referenced_tables = find_referenced_tables(measure.expression)
        if referenced_tables:
            referenced_tables.sort(key=table_priority_key)
            true_table = referenced_tables[0]
            # Mismatch if:
            # - original table is not referenced at all, OR
            # - original table is a low-priority container (>=50) and a higher-priority table is available
            is_mismatch = (entry.table not in referenced_tables) or (
                table_priority_key(entry.table) >= 50 and table_priority_key(true_table) < table_priority_key(entry.table)
            )
            if is_mismatch and entry.table != true_table:
                orig_table = entry.table
                entry.table = true_table
                note = f"Housed in '{orig_table}' table but operates on '{true_table}' table."
                if entry.caveats:
                    entry.caveats = f"{entry.caveats}. {note}"
                else:
                    entry.caveats = note

    return MeasureCatalog(measures=entries, dependency_svg=render_measure_dependency_graph_svg(model))


# -- VI. Security & Governance ------------------------------------------------
def _security(model: SemanticModel) -> SecurityGovernance:
    roles = [
        {
            "name": r.name,
            "model_permission": r.model_permission or "read",
            "filters": [f"{p.table}: {p.filter_expression}" for p in r.table_permissions],
            "members": r.members,
        }
        for r in model.roles
    ]
    if model.roles:
        constraints = [
            f"{len(model.roles)} row-level security role(s) are defined. Role membership "
            "and workspace access are managed in the Power BI Service and should be "
            "reviewed there against this catalog."
        ]
    else:
        constraints = ["No row-level security roles are defined in this model."]
    return SecurityGovernance(roles=roles, workspace_constraints=constraints)


# -- VII. Tech Debt / Audit (always deterministic) ----------------------------
def _audit(model: SemanticModel) -> TechDebtAudit:
    measures = model.all_measures()
    used = used_measure_names(model)
    orphaned = [m.name for m in measures if m.name not in used]
    hidden_used = [m.name for m in measures if m.is_hidden and m.name in used]

    notes: list[str] = []
    if measures:
        pct = round(100 * len(orphaned) / len(measures))
        notes.append(
            f"{len(orphaned)} of {len(measures)} measures ({pct}%) are defined but not used "
            f"on any report page (directly or via another measure)."
        )
    if hidden_used:
        notes.append(f"{len(hidden_used)} hidden measure(s) are still referenced by report visuals.")
    total_visuals = sum(len(p.visuals) for p in model.pages)
    if measures and not used and total_visuals:
        notes.append(
            "Note: no measure references could be read from this report's visuals, so usage "
            "could not be confirmed — measures below may in fact be in use."
        )

    # Check for hardcoded years in DAX
    for m in measures:
        years = detect_hardcoded_years(m.expression)
        if years:
            year_list = ", ".join(years)
            notes.append(f"Measure '{m.name}' contains a hardcoded year value ({year_list}) in its DAX calculation.")

    unused = audit_rules.find_unused_assets(model)
    return TechDebtAudit(
        orphaned_measures=orphaned,
        hidden_but_used=hidden_used,
        notes=notes,
        unused_assets=dataclasses.asdict(unused)
    )


TYPOS = {
    "gaint": "giant / gained",
    "calender": "calendar",
    "customer": "customer",
    "revnue": "revenue",
    "catagory": "category",
    "requirment": "requirement",
    "develeper": "developer",
}


def _check_typo(name: str) -> str:
    name_lower = name.lower()
    for typo, correction in TYPOS.items():
        if typo in name_lower:
            return f"Likely '{correction}'"
    return ""


def _infer_glossary(model: SemanticModel, measure_catalog: MeasureCatalog, col_descs: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    entries = []

    # 1. Add all measures (first sentence only — full definition lives in the
    # Measure Catalog; the glossary should not repeat it)
    for m in measure_catalog.measures:
        typo_flag = _check_typo(m.name)
        entries.append({
            "term": m.name,
            "type": "Measure",
            "definition": first_sentence(m.plain_english) or "See the Measure Catalog for the definition.",
            "typo_flag": typo_flag
        })

    # 2. Add key dimension fields used in report visuals
    measure_names = {m.name for m in model.all_measures()}
    dimensions = set()
    for p in model.pages:
        for v in p.visuals:
            for f in v.fields:
                name = f.split(".")[-1]
                if name and name not in measure_names:
                    dimensions.add(name)

    for dim in sorted(dimensions):
        typo_flag = _check_typo(dim)

        # Look up definition in data dictionary descriptions ("Unknown" entries
        # don't count — the keyword heuristics below may still identify the field)
        definition = None
        for (t_name, c_name), desc in col_descs.items():
            if c_name == dim and desc and not desc.startswith("Unknown"):
                definition = desc
                break

        if not definition:
            definition = "Unknown — requires business confirmation."
            dim_lower = dim.lower()
            if "date" in dim_lower or "calendar" in dim_lower:
                definition = "Time-dimension field used to filter, segment, and perform time-intelligence trends."
            elif "customer" in dim_lower:
                definition = "Represents unique customer attributes, identifiers, or segments."
            elif "product" in dim_lower:
                definition = "Represents product categories, items, or inventory attributes."
            elif "region" in dim_lower or "country" in dim_lower or "city" in dim_lower:
                definition = "Geographical dimension used to analyze regional breakdown and location-based performance."

        entries.append({
            "term": dim,
            "type": "Dimension",
            "definition": definition,
            "typo_flag": typo_flag
        })

    return entries


# -- Health Score & AI Recommendations ----------------------------------------
def _health_and_recommendations(
    model: SemanticModel,
    owner: Optional[str],
    classification: Optional[str],
) -> tuple[dict, list[dict], list[str]]:
    """Run the deterministic audit rules over the model and return
    ``(health_score, ai_recommendations, suppressed_rules)`` as plain dicts/lists for the technical
    document."""
    audit_rules.reset_suppressed_rules()
    measures = model.all_measures()
    dax_findings = audit_rules.find_dax_findings(measures)
    best_practices = audit_rules.check_best_practices(model)
    performance_risks = audit_rules.find_performance_risks(model)
    governance = audit_rules.check_governance(model, owner=owner, classification=classification)
    unused_assets = audit_rules.find_unused_assets(model)
    health = audit_rules.compute_health_score(
        dax_findings, best_practices, performance_risks, governance, unused_assets,
    )
    recommendations = audit_rules.build_recommendations(
        dax_findings, best_practices, performance_risks, governance, unused_assets, model=model,
    )
    suppressed = audit_rules.get_suppressed_rules()
    return dataclasses.asdict(health), [dataclasses.asdict(r) for r in recommendations], suppressed


def _run_critic(doc: Document, model: SemanticModel, client, warn, ai_context: Optional[JobAIContext]) -> None:
    """5.3: one critic pass over every narrative prose field, applied
    in place onto ``doc``. Only called when a client is available (offline
    runs are unaffected — see the caller)."""
    known_names = {t.name for t in model.tables}
    known_names |= {c.name for t in model.tables for c in t.columns}
    known_names |= {m.name for m in model.all_measures()}
    known_names |= {p.display_name for p in model.pages}

    triples: list[tuple[str, str, "callable"]] = []
    es = doc.executive_summary

    def _set_core_purpose(v: str) -> None:
        es.core_purpose = v
    triples.append(("executive_summary.core_purpose", es.core_purpose, _set_core_purpose))

    for i, p in enumerate(es.pages):
        def _set_page_summary(v: str, _p=p) -> None:
            _p.summary = v
        triples.append((f"executive_summary.pages[{i}].summary", p.summary, _set_page_summary))

    for i, ve in enumerate(es.complex_visual_explainers):
        def _set_how_to_read(v: str, _ve=ve) -> None:
            _ve.how_to_read = v
        triples.append((f"executive_summary.complex_visual_explainers[{i}].how_to_read",
                        ve.how_to_read, _set_how_to_read))

    def _set_sm_summary(v: str) -> None:
        doc.semantic_model.summary = v
    triples.append(("semantic_model.summary", doc.semantic_model.summary, _set_sm_summary))

    for i, entry in enumerate(doc.measure_catalog.measures):
        def _set_plain_english(v: str, _e=entry) -> None:
            _e.plain_english = v
        def _set_caveats(v: str, _e=entry) -> None:
            _e.caveats = v
        def _set_calc_logic(v: str, _e=entry) -> None:
            _e.calculation_logic = v
        triples.append((f"measure_catalog.measures[{i}].plain_english", entry.plain_english, _set_plain_english))
        triples.append((f"measure_catalog.measures[{i}].caveats", entry.caveats, _set_caveats))
        triples.append((f"measure_catalog.measures[{i}].calculation_logic",
                        entry.calculation_logic, _set_calc_logic))

    for i, note in enumerate(doc.tech_debt.notes):
        def _set_note(v: str, _i=i) -> None:
            doc.tech_debt.notes[_i] = v
        triples.append((f"tech_debt.notes[{i}]", note, _set_note))

    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_critic_pass(fields, client, known_names=known_names, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


class TechnicalDocumentationGenerator:
    """Assembles the 17-section :class:`Document` — the original pbicompass
    documentation output, unchanged, for BI developers/data engineers/
    consultants/support engineers."""

    @staticmethod
    def generate(
        model: SemanticModel,
        client: Optional[LLMClient] = None,
        *,
        owner: Optional[str] = None,
        audience: Optional[str] = None,
        refresh: Optional[str] = None,
        on_warning: Optional[Warn] = None,
        # Custom metadata fields
        version: Optional[str] = None,
        status: Optional[str] = None,
        author: Optional[str] = None,
        reviewer: Optional[str] = None,
        classification: Optional[str] = None,
        business_decision: Optional[str] = None,
        requirements: Optional[str] = None,
        security_notes: Optional[str] = None,
        refresh_notes: Optional[str] = None,
        deployment_notes: Optional[str] = None,
        access_notes: Optional[str] = None,
        glossary: Optional[str] = None,
        assumptions: Optional[str] = None,
        support_notes: Optional[str] = None,
        ai_context: Optional[JobAIContext] = None,
    ) -> Document:
        """Assemble the seven-section :class:`Document` from a parsed model.

        Pass an ``LLMClient`` to use Claude for the prose agents; omit it (or
        pass ``None``) to run the fully deterministic offline pipeline.

        ``ai_context`` (Phase 0) is the job-shared :class:`JobAIContext`
        built once by the caller (``cli.py``/``service/worker.py``) across
        every requested document type; omit it (or pass ``None``) and this
        generator builds its own on demand — direct-import callers and tests
        that call this generator alone keep working unchanged.
        """
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()
        if ai_context is None and client is not None:
            ai_context = build_job_context(model, client, warn)
        col_descs = _column_descriptions(model, client, warn, ai_context)
        from ...render._nav_map import render_navigation_map
        nav_edges, nav_map_svg = render_navigation_map(model)
        doc = Document(
            metadata=_metadata(
                model, owner, audience, refresh,
                version=version, status=status, author=author, reviewer=reviewer,
                classification=classification, business_decision=business_decision,
                requirements=requirements, security_notes=security_notes,
                refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                access_notes=access_notes, glossary=glossary,
                assumptions=assumptions, support_notes=support_notes,
            ),
            executive_summary=_executive_summary(model, client, warn, ai_context),
            lineage=_lineage(model),
            semantic_model=_semantic_model(model, client, warn, col_descs, ai_context),
            measure_catalog=_measure_catalog(model, client, warn, ai_context),
            security=_security(model),
            tech_debt=_audit(model),
            stats=dict(model.meta.counts),
            report_pages=report_pages(model),
            slicers=slicers(model),
            calculated_columns=calc_columns(model),
            navigation_map_svg=nav_map_svg or None,
            navigation_edges=nav_edges,
        )

        # Check for hardcoded years for Executive Summary callout
        hardcoded_measures = []
        for m in model.all_measures():
            years = detect_hardcoded_years(m.expression)
            if years:
                hardcoded_measures.append((m.name, years))

        if hardcoded_measures:
            callout_parts = []
            for name, years in hardcoded_measures:
                callout_parts.append(f"'{name}' (referencing {', '.join(years)})")
            warning_msg = (
                "\n\n**Warning:** This report contains metrics with hardcoded year values, including: "
                f"{'; '.join(callout_parts)}. These calculations are not dynamic and will require "
                "manual update each year."
            )
            doc.executive_summary.core_purpose += warning_msg

        doc.glossary_entries = _infer_glossary(model, doc.measure_catalog, col_descs)
        health_score, ai_recs, suppressed = _health_and_recommendations(
            model, owner, classification,
        )
        doc.health_score = health_score
        doc.ai_recommendations = ai_recs
        doc.tech_debt.suppressed_rules = suppressed
        
        trend = audit_rules.get_and_update_score_history(
            model.report_name or "UnknownReport",
            health_score.get("overall", 0)
        )
        doc.metadata.score_trend = trend

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)

        return doc
