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

from ..consistency import AuditVerdicts, check_consistency, find_human_claim_discrepancies
from ..context import JobAIContext, build_job_context
from ..traceability import build_requirements_matrix
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
from ...schemas.audit_document import FindingCluster
from ...schemas.model import SemanticModel
from .. import audit_rules, io
from ..critic import apply_critic_pass, apply_results
from ..deterministic import (
    business_analyst_deterministic,
    data_modeler_deterministic,
    relationship_lines,
    translate_dax,
)
from ..grounding import apply_grounding_pass
from ..llm import LLMClient
from ..report_facts import (
    calc_columns,
    data_source_summaries,
    detect_hardcoded_years,
    field_parameter_table_names,
    find_referenced_tables,
    first_sentence,
    has_keyword_token,
    is_field_selector,
    local_path_sources,
    named_field_parameter_table_names,
    parse_human_glossary,
    report_pages,
    slicers,
    table_priority_key,
)
from ..sanitize import is_meta_commentary, is_punt_phrase, sanitize_narratives


def _pl(word: str, count: int, plural: str | None = None) -> str:
    """``"{count} {word or plural}"`` (P2) — kills the "asset(s)" pattern.
    Lazily imports ``render._shared.pluralize_count`` (not a top-level
    import) to avoid the agents<->render import cycle — see
    ``audit_rules.py``'s identical helper for the full reasoning."""
    from ...render._shared import pluralize_count
    return pluralize_count(word, count, plural)
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
def _executive_summary(model: SemanticModel, client, warn, ai_context: Optional[JobAIContext],
                        business_decision: Optional[str] = None, audience: Optional[str] = None,
                        assumptions: Optional[str] = None, security_notes: Optional[str] = None,
                        refresh_notes: Optional[str] = None, deployment_notes: Optional[str] = None,
                        access_notes: Optional[str] = None, support_notes: Optional[str] = None,
                        ) -> ExecutiveSummary:
    """Batches the Business Analyst prompt by page (``io.business_analyst_
    batches``) so one failed/invalid batch degrades only the pages it
    covers, not the whole Executive Summary. Each batch gets one retry
    (jittered) before falling back; a batch that still fails keeps its pages
    on the deterministic engine and names them in a warning, rather than
    silently degrading the whole document with no signal (1.4).

    Day 3: ``business_decision`` anchors ``core_purpose`` even in the
    deterministic/offline fallback and steers the Business Analyst prompt
    (``io.HUMAN_CONTEXT_NOTE``) when a client is available."""
    deterministic = business_analyst_deterministic(model)
    core_purpose = deterministic.core_purpose
    if business_decision:
        core_purpose = f"{core_purpose} This report exists to support: {business_decision}"
    if client is None:
        deterministic.core_purpose = core_purpose
        return deterministic

    pages = list(deterministic.pages)
    navigation_guide = list(deterministic.navigation_guide)
    complex_visual_explainers = list(deterministic.complex_visual_explainers)
    nav_seen = set(navigation_guide)
    explainer_seen = {(e.visual, e.page) for e in complex_visual_explainers}
    core_purpose_set = False
    offset = 0

    report_context = ai_context.insights if ai_context is not None else None
    # Phase 2: when every batch fails, seed the deterministic fallback with
    # the whole-model synthesis's own purpose statement rather than the
    # generic template one — a strictly-better fallback purchased by a call
    # already paid for this job, not an extra one.
    if report_context:
        rp = report_context.get("report_purpose") or {}
        if rp.get("statement") and rp.get("confidence") in ("High", "Medium"):
            core_purpose = rp["statement"]
            if business_decision:
                core_purpose = f"{core_purpose} This report exists to support: {business_decision}"

    for batch in io.business_analyst_batches(
            model, report_context=report_context,
            business_decision=business_decision, target_audience=audience,
            assumptions=assumptions, security_notes=security_notes,
            refresh_notes=refresh_notes, deployment_notes=deployment_notes,
            access_notes=access_notes, support_notes=support_notes):
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
def _related_tables(model: SemanticModel, table_name: str, column_name: str) -> list[str]:
    """Tables reachable from ``table_name[column_name]`` via a relationship
    — used to broaden the deterministic "join key" derivation (D6) to any
    column that structurally participates in a relationship, not just ones
    named ``*Id``/``*Key``."""
    related: list[str] = []
    for r in model.relationships:
        if r.from_table == table_name and r.from_column == column_name and r.to_table not in related:
            related.append(r.to_table)
        elif r.to_table == table_name and r.to_column == column_name and r.from_table not in related:
            related.append(r.from_table)
    return related


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
                    related = _related_tables(model, t.name, c.name)
                    if related:
                        # D6: broaden deterministic derivation — a column
                        # participating in a relationship has a known
                        # structural role even without an Id/Key name.
                        desc = f"Join key linking {t.name} to {', '.join(related)}."
                    else:
                        # Honest, calm default for a genuinely roleless
                        # column — never fabricate business intent, and
                        # never the alarming "requires business
                        # confirmation" wording for a column that isn't
                        # actually ambiguous, just undescribed (D6).
                        desc = "No description set."
            descriptions[(t.name, c.name)] = desc

    if client is not None:
        data = _call(client, io.COLUMN_DESCRIBER_SYSTEM, io.column_describer_input(model),
                     io.COLUMN_DESCRIBER_SCHEMA, warn, "Column Describer", ai_context=ai_context)
        if data:
            for item in data.get("columns", []):
                t_name = item.get("table")
                c_name = item.get("column")
                desc = (item.get("description") or "").strip()
                key = (t_name, c_name)
                if not desc or key not in descriptions:
                    continue
                if is_meta_commentary(desc):
                    continue  # D2: reject a leaked editing directive
                if is_punt_phrase(desc):
                    # D6: the LLM may only improve a description, never
                    # downgrade one — a deterministic description always
                    # exists by this point, so a punt is discarded outright.
                    continue
                descriptions[key] = desc
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
                parts.append(_pl("relationship", len(rel_list)))
            if meas_list:
                parts.append(f"{_pl('measure', len(meas_list))} ({', '.join(meas_list[:2])}{'...' if len(meas_list) > 2 else ''})")
            if vis_list:
                parts.append(f"{_pl('page', len(vis_list))} ({', '.join(vis_list[:2])}{'...' if len(vis_list) > 2 else ''})")
            if rls_list:
                parts.append(f"{_pl('RLS role', len(rls_list))}: {', '.join(rls_list)}")
                
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
        report_context = ai_context.insights if ai_context is not None else None
        data = _call(client, io.DATA_MODELER_SYSTEM, io.data_modeler_input(model, report_context=report_context),
                     io.DATA_MODELER_SCHEMA, warn, "Data Modeler", ai_context=ai_context)
        if data and "summary" in data:
            summary = data["summary"]
            risks = list(data.get("risks", risks))

    for target in local_path_sources(model):
        risks.append(f"Hardcoded local path detected — replace with a parameterized path or gateway source before production deployment. (Path: {target})")
    # V2: Auto Date/Time's own hidden calendar tables and disconnected
    # field-parameter/helper tables (e.g. "Range") are never real
    # dimensions — drawing them as model-diagram nodes misrepresents the
    # actual star/galaxy shape a reader is trying to verify. Excluded here
    # (diagram nodes + their edges) only; the "Key tables" list below still
    # lists every table for completeness, matching what count-based checks
    # elsewhere already treat as a separate concern from the diagram.
    diagram_excluded = {t.name for t in model.tables if audit_rules.is_auto_date_table(t.name)}
    diagram_excluded |= named_field_parameter_table_names(model)
    tables = [
        {"name": t.name, "kind": t.kind, "columns": len(t.columns), "measures": len(t.measures)}
        for t in model.tables if t.name not in diagram_excluded
    ]
    edges = [
        {"from": r.from_table, "to": r.to_table, "from_card": r.from_cardinality,
         "to_card": r.to_cardinality, "cross_filter": r.cross_filter, "is_active": r.is_active,
         "from_column": r.from_column, "to_column": r.to_column}
        for r in model.relationships
        if r.from_table not in diagram_excluded and r.to_table not in diagram_excluded
    ]
    return SemanticModelDoc(summary=summary, data_dictionary=data_dictionary, relationships=rels,
                            risks=risks, tables=tables, relationship_edges=edges)


def _join_caveat(existing: str, note: str) -> str:
    """Append ``note`` onto ``existing`` as a new sentence, adding exactly
    one separating period (P2) — a plain ``f"{existing}. {note}"`` doubles
    up when ``existing`` already ends with terminal punctuation ("...date
    filters.. Housed in...")."""
    existing = (existing or "").rstrip()
    if not existing:
        return note
    if existing[-1] not in ".!?":
        existing += "."
    return f"{existing} {note}"


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
        det_plain_english, det_caveats, det_category = translate_dax(
            measure.name, measure.expression, measure.format_string
        )
        t = translations.get(entry.name) if translations else None
        if t:
            plain_english = (t.get("plain_english") or "").strip()
            if is_meta_commentary(plain_english) or is_punt_phrase(plain_english):
                # D6: the LLM may only improve the deterministic gloss,
                # never downgrade it to a punt or leak an editing directive.
                entry.plain_english = det_plain_english
                entry.confidence = "Low"
            else:
                entry.plain_english = plain_english
                entry.confidence = t.get("confidence", "") or "Low"
            calculation_logic = (t.get("calculation_logic") or "").strip()
            entry.calculation_logic = (
                calculation_logic if calculation_logic and not is_meta_commentary(calculation_logic)
                else entry.plain_english
            )
            caveats = (t.get("caveats") or "").strip()
            entry.caveats = caveats if not is_meta_commentary(caveats) else det_caveats
            entry.category = t.get("category", "") or det_category
        else:
            # The deterministic line is derived from the DAX, so it doubles as
            # the calculation logic; the business meaning behind it is
            # unverified, hence the explicit Low confidence.
            entry.plain_english = det_plain_english
            entry.calculation_logic = det_plain_english
            entry.caveats = det_caveats
            entry.category = det_category
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

        # Cross-check measure table attribution. ``entry.table`` (the home
        # table, an objective fact from the model definition) is never
        # overwritten here — a card's title has to stay trustworthy even
        # when this name-pattern heuristic guesses the "true" table wrong.
        # The referenced tables instead populate ``operates_on``, a
        # clearly-separate secondary line, with a caveat note only when
        # they genuinely diverge from the home table.
        referenced_tables = find_referenced_tables(measure.expression)
        if referenced_tables:
            referenced_tables.sort(key=table_priority_key)
            entry.operates_on = referenced_tables
            true_table = referenced_tables[0]
            # Mismatch if:
            # - original table is not referenced at all, OR
            # - original table is a low-priority container (>=50) and a higher-priority table is available
            is_mismatch = (entry.table not in referenced_tables) or (
                table_priority_key(entry.table) >= 50 and table_priority_key(true_table) < table_priority_key(entry.table)
            )
            if is_mismatch and entry.table != true_table:
                note = f"Housed in '{entry.table}' table but operates on '{true_table}' table."
                entry.caveats = _join_caveat(entry.caveats, note)

    return MeasureCatalog(measures=entries, dependency_svg=render_measure_dependency_graph_svg(model))


# -- VI. Security & Governance ------------------------------------------------
def _security(model: SemanticModel, security_notes: Optional[str] = None) -> SecurityGovernance:
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
            f"{_pl('row-level security role', len(model.roles))} "
            f"{'is' if len(model.roles) == 1 else 'are'} defined. Role membership "
            "and workspace access are managed in the Power BI Service and should be "
            "reviewed there against this catalog."
        ]
    else:
        constraints = ["No row-level security roles are defined in this model."]
    discrepancies = find_human_claim_discrepancies(security_notes, len(model.roles))
    return SecurityGovernance(roles=roles, workspace_constraints=constraints,
                              discrepancies=[dataclasses.asdict(d) for d in discrepancies])


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
        notes.append(f"{_pl('hidden measure', len(hidden_used))} "
                     f"{'is' if len(hidden_used) == 1 else 'are'} still referenced by report visuals.")
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


def _infer_glossary(model: SemanticModel, measure_catalog: MeasureCatalog, col_descs: dict[tuple[str, str], str],
                     field_param_tables: set[str] = frozenset(),
                     human_glossary: Optional[str] = None) -> list[dict[str, str]]:
    """Day 3: ``human_glossary`` (the intake form's free-text glossary
    field) is merged in last, term-by-term, overriding any matching entry's
    definition and appending any new business term with no counterpart
    below — never a full override of this deterministic/AI-inferred table
    the way rendering it as an either/or with the raw text field used to."""
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

    # 2. Add key dimension fields used in report visuals. A field-parameter
    # reference (I4) is excluded — it's a UI selector, not a real
    # dimension, and (being disconnected from the model) can never resolve
    # to a real column description, so it would otherwise always render the
    # alarming "requires business confirmation" punt (D6/D4).
    measure_names = {m.name for m in model.all_measures()}
    dimensions = set()
    for p in model.pages:
        for v in p.visuals:
            for f in v.fields:
                if is_field_selector(f, field_param_tables):
                    continue
                name = f.split(".")[-1]
                if name and name not in measure_names:
                    dimensions.add(name)

    for dim in sorted(dimensions):
        typo_flag = _check_typo(dim)

        # Look up definition in data dictionary descriptions (a placeholder
        # "Unknown"/"No description set" entry doesn't count — the keyword
        # heuristics below may still identify the field)
        definition = None
        for (t_name, c_name), desc in col_descs.items():
            if c_name == dim and desc and not desc.startswith(("Unknown", "No description set")):
                definition = desc
                break

        if not definition:
            definition = "No description set."
            if has_keyword_token(dim, ("date", "calendar")):
                definition = "Time-dimension field used to filter, segment, and perform time-intelligence trends."
            elif has_keyword_token(dim, ("customer",)):
                definition = "Represents unique customer attributes, identifiers, or segments."
            elif has_keyword_token(dim, ("product",)):
                definition = "Represents product categories, items, or inventory attributes."
            elif has_keyword_token(dim, ("region", "country", "city")):
                definition = "Geographical dimension used to analyze regional breakdown and location-based performance."

        entries.append({
            "term": dim,
            "type": "Dimension",
            "definition": definition,
            "typo_flag": typo_flag
        })

    human_terms = parse_human_glossary(human_glossary)
    if human_terms:
        by_term_lower = {e["term"].lower(): e for e in entries}
        for term, definition in human_terms.items():
            existing = by_term_lower.get(term.lower())
            if existing:
                existing["definition"] = definition
                existing["typo_flag"] = ""
            else:
                new_entry = {"term": term, "type": "Business Term", "definition": definition, "typo_flag": ""}
                entries.append(new_entry)
                by_term_lower[term.lower()] = new_entry

    return entries


# -- Health Score & AI Recommendations ----------------------------------------
def _health_and_recommendations(
    model: SemanticModel,
    owner: Optional[str],
    classification: Optional[str],
    ai_context: Optional[JobAIContext] = None,
    security_notes: Optional[str] = None,
) -> tuple[dict, list[dict], list[str], dict]:
    """Run the deterministic audit rules over the model and return
    ``(health_score, ai_recommendations, suppressed_rules, checks_ledger)``
    as plain dicts/lists for the technical document.

    ``checks_ledger`` reuses ``ai_context.checks_ledger`` when the sibling
    Audit & Health Report already computed it in this job (Day 8's
    pre-generated-audit path) instead of re-deriving pass/fail counts a
    different way — the two documents must always show identical numbers
    for the same model."""
    audit_rules.reset_suppressed_rules()
    measures = model.all_measures()
    dax_findings = audit_rules.find_dax_findings(measures)
    best_practices = audit_rules.check_best_practices(model)
    performance_risks = audit_rules.find_performance_risks(model)
    governance = audit_rules.check_governance(model, owner=owner, classification=classification,
                                              security_notes=security_notes)
    unused_assets = audit_rules.find_unused_assets(model)
    health = audit_rules.compute_health_score(
        dax_findings, best_practices, performance_risks, governance, unused_assets,
    )
    recommendations = audit_rules.build_recommendations(
        dax_findings, best_practices, performance_risks, governance, unused_assets, model=model,
    )
    suppressed = audit_rules.get_suppressed_rules()
    if ai_context is not None and ai_context.checks_ledger is not None:
        ledger = ai_context.checks_ledger
    else:
        ledger = audit_rules.compute_checks_ledger(
            dax_findings, best_practices, performance_risks, governance, suppressed,
        )
    return (dataclasses.asdict(health), [dataclasses.asdict(r) for r in recommendations],
            suppressed, ledger)


def _narrative_triples(doc: Document) -> list[tuple[str, str, "callable"]]:
    """Every narrative prose field in the technical document as
    ``(location, text, setter)`` triples — shared by the critic (5.3) and
    grounding (Phase 3) passes so neither re-derives the other's field
    list."""
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
    return triples


def _run_critic(doc: Document, model: SemanticModel, client, warn, ai_context: Optional[JobAIContext]) -> None:
    """5.3: one critic pass over every narrative prose field, applied
    in place onto ``doc``. Only called when a client is available (offline
    runs are unaffected — see the caller)."""
    known_names = {t.name for t in model.tables}
    known_names |= {c.name for t in model.tables for c in t.columns}
    known_names |= {m.name for m in model.all_measures()}
    known_names |= {p.display_name for p in model.pages}

    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_critic_pass(fields, client, known_names=known_names, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


def _run_grounding(doc: Document, client, warn, ai_context: Optional[JobAIContext]) -> None:
    """Phase 3: one fact-verification call over the same narrative fields,
    run after the critic pass so it judges the already style-corrected text.
    Skipped when no shared ``ai_context``/digest is available."""
    if ai_context is None or not ai_context.model_digest:
        return
    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_grounding_pass(fields, client, model_digest=ai_context.model_digest,
                                    warn=warn, ai_context=ai_context)
    apply_results(triples, results)


def _run_consistency(
    doc: Document, client, warn, ai_context: Optional[JobAIContext],
    audit_verdicts: Optional[AuditVerdicts],
) -> None:
    """Day 2: cross-artifact consistency check against the sibling Audit &
    Health Report's verdicts, run after grounding so it judges the already
    fact-checked text. Its deterministic fixed-vocabulary layer needs no
    LLM, so this runs even offline — a no-op only when no Audit document was
    generated alongside this one in the same job (``audit_verdicts is None``)."""
    if audit_verdicts is None:
        return
    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = check_consistency(fields, client, verdicts=audit_verdicts, warn=warn, ai_context=ai_context)
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
        top_cluster: Optional[FindingCluster] = None,
        audit_verdicts: Optional[AuditVerdicts] = None,
        requirements_matrix: Optional[list] = None,
    ) -> Document:
        """Assemble the seven-section :class:`Document` from a parsed model.

        Pass an ``LLMClient`` to use Claude for the prose agents; omit it (or
        pass ``None``) to run the fully deterministic offline pipeline.

        ``ai_context`` (Phase 0) is the job-shared :class:`JobAIContext`
        built once by the caller (``cli.py``/``service/worker.py``) across
        every requested document type; omit it (or pass ``None``) and this
        generator builds its own on demand — direct-import callers and tests
        that call this generator alone keep working unchanged.

        ``top_cluster`` (Day 8) is the broadest-impact root-cause cluster
        from the sibling Audit document's Audit Synthesizer (Day 7) — the
        caller generates the Audit document first when both types are
        requested in the same job and passes its top cluster here, so §16
        can surface it without a second, potentially-inconsistent Synthesizer
        call. Omit it (or pass ``None``) and §16 simply carries no root-cause
        callout — never a placeholder.

        ``audit_verdicts`` (Day 2) is the same sibling Audit document's
        ground-truth verdicts (schema shape, RLS role count, refresh
        configured, description coverage, fact/dimension counts) —
        ``agents.consistency.build_audit_verdicts(model, pre_audit_doc)``.
        When given, every narrative field is checked against it and any
        contradicting claim (e.g. "a well-structured star schema" when the
        Audit document's own star-schema check failed) is corrected in
        place. Omit it (or pass ``None``) and this check is skipped — never
        a false positive against a document that wasn't generated.

        ``requirements_matrix`` (Day 4) is the pre-computed Requirements
        Traceability Matrix (``agents.traceability.build_requirements_matrix``)
        — unlike ``top_cluster``/``audit_verdicts`` it has no ordering
        dependency on the Audit document, so the caller may compute it once
        up front and share it with every document type. Omit it (or pass
        ``None``) and this generator computes its own from ``requirements``.
        """
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()
        if ai_context is None and client is not None:
            ai_context = build_job_context(
                model, client, warn,
                business_decision=business_decision, target_audience=audience,
                assumptions=assumptions, security_notes=security_notes,
                refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                access_notes=access_notes, support_notes=support_notes,
            )
        if requirements_matrix is None:
            requirements_matrix = build_requirements_matrix(
                model, requirements, client, warn, ai_context=ai_context,
                business_decision=business_decision, target_audience=audience,
                assumptions=assumptions, security_notes=security_notes,
                refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                access_notes=access_notes, support_notes=support_notes,
            )
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
            executive_summary=_executive_summary(
                model, client, warn, ai_context,
                business_decision=business_decision, audience=audience,
                assumptions=assumptions, security_notes=security_notes,
                refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                access_notes=access_notes, support_notes=support_notes),
            lineage=_lineage(model),
            semantic_model=_semantic_model(model, client, warn, col_descs, ai_context),
            measure_catalog=_measure_catalog(model, client, warn, ai_context),
            security=_security(model, security_notes),
            tech_debt=_audit(model),
            stats=dict(model.meta.counts),
            report_pages=report_pages(model),
            slicers=slicers(model),
            calculated_columns=calc_columns(model),
            navigation_map_svg=nav_map_svg or None,
            navigation_edges=nav_edges,
            requirements_matrix=[dataclasses.asdict(r) for r in requirements_matrix],
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

        doc.glossary_entries = _infer_glossary(model, doc.measure_catalog, col_descs,
                                               field_parameter_table_names(model), glossary)
        health_score, ai_recs, suppressed, ledger = _health_and_recommendations(
            model, owner, classification, ai_context, security_notes=security_notes,
        )
        doc.health_score = health_score
        doc.ai_recommendations = ai_recs
        doc.tech_debt.suppressed_rules = suppressed
        doc.checks_run = ledger["run"]
        doc.checks_passed = ledger["passed"]
        doc.checks_failed = ledger["failed"]
        doc.checks_suppressed = ledger["suppressed"]
        doc.checks_by_category = ledger["by_category"]
        doc.top_cluster = dataclasses.asdict(top_cluster) if top_cluster else None
        
        trend = audit_rules.get_shared_score_trend(
            ai_context, model.report_name or "UnknownReport",
            health_score.get("overall", 0)
        )
        doc.metadata.score_trend = trend

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)
            _run_grounding(doc, client, warn, ai_context)
        _run_consistency(doc, client, warn, ai_context, audit_verdicts)

        # P0: the one gate every narrative field passes through (unconditional
        # — a field's initial LLM draft can carry the leak before critic/
        # grounding ever run) — see sanitize.sanitize_narratives's docstring.
        sanitize_narratives(_narrative_triples(doc))

        return doc
