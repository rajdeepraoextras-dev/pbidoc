"""Deterministic rule engine behind the Audit & Health Report.

Every function here is a pure function of a :class:`SemanticModel` (plus, in
a couple of cases, already-computed facts) — no LLM calls, no randomness, so
the same model always produces the same findings. That is the point: health
score, complexity, best-practice checks, and unused-asset detection must be
reproducible and testable, never a guess.

Two constraints shape what's implemented here:

* **No row-level data exists in model.json by design** (metadata only). So
  "high cardinality", "heavy DAX", and "slow slicers" can never be measured —
  every :class:`PerformanceRisk` below is a metadata-only heuristic, and its
  ``detail`` text says so explicitly rather than pretending to have measured
  anything.
* **Hierarchies and calculation groups are never populated** by today's
  parsers (``Table.kind`` has an unused ``"calculation-group"`` literal, and
  no hierarchy dataclass exists at all) — :func:`find_unused_assets`
  deliberately excludes them rather than reporting permanently-empty lists.
"""

from __future__ import annotations

import re
from collections import Counter

from ..schemas.audit_document import (
    BestPracticeCheck,
    ComplexityAssessment,
    DaxFinding,
    GovernanceFinding,
    HealthScore,
    PerformanceRisk,
    Recommendation,
    UnusedAssets,
)
from ..schemas.model import Measure, SemanticModel
from .deterministic import schema_shape
from .report_facts import calc_columns, report_pages
from .usage import used_column_names, used_measure_names

_SEVERITY_COST = {"Critical": 15, "High": 8, "Medium": 4, "Low": 1}

# A handful of common misspellings worth flagging in measure/table names.
# Deliberately small and self-contained (rather than importing the glossary's
# TYPOS dict) — this is a DAX-naming check, a different concern from the
# business-glossary typo flag.
_NAME_TYPOS = {
    "gaint": "giant / gained", "calender": "calendar", "revnue": "revenue",
    "catagory": "category", "requirment": "requirement", "recieve": "receive",
    "seperate": "separate", "occured": "occurred",
}
_GENERIC_NAME = re.compile(r"^(measure|calc|calculation|table|query|sheet)\s*\d*$", re.IGNORECASE)
_SENSITIVE_NAME = re.compile(r"(ssn|social.?security|passport|password|salary|dob|date.?of.?birth|"
                             r"credit.?card|phone|email|address)", re.IGNORECASE)
_ID_LIKE_NAME = re.compile(r"(id|key|guid|email|phone|address)$", re.IGNORECASE)
_TEXT_BLOB_NAME = re.compile(r"(description|comment|note|remark|detail|text|body)", re.IGNORECASE)


# -- Health score & complexity -------------------------------------------------
def _band(overall: int) -> str:
    if overall >= 90:
        return "Excellent"
    if overall >= 75:
        return "Good"
    if overall >= 50:
        return "Fair"
    return "Poor"


def compute_health_score(
    dax_findings: list[DaxFinding],
    best_practices: list[BestPracticeCheck],
    performance_risks: list[PerformanceRisk],
    governance: list[GovernanceFinding],
    unused_assets: UnusedAssets,
) -> HealthScore:
    """Weighted rubric: start each component at 100, subtract a fixed cost per
    finding by severity (Critical -15, High -8, Medium -4, Low -1), floor at
    0. Overall is the average of the five components, banded at 90/75/50."""
    dax_cost = sum(_SEVERITY_COST.get(f.severity, 4) for f in dax_findings)
    failed_practices = [c for c in best_practices if not c.passed]
    modeling_cost = len(failed_practices) * 5
    perf_cost = sum(_SEVERITY_COST.get(f.severity, 4) for f in performance_risks)
    governance_cost = sum(_SEVERITY_COST.get(f.severity, 4) for f in governance)
    unused_count = (
        len(unused_assets.measures) + len(unused_assets.columns) + len(unused_assets.tables)
        + len(unused_assets.calculated_columns) + len(unused_assets.report_pages)
    )
    unused_cost = min(30, unused_count * 2)

    component_scores = {
        "modeling": max(0, 100 - modeling_cost),
        "dax": max(0, 100 - dax_cost),
        "governance": max(0, 100 - governance_cost),
        "performance": max(0, 100 - perf_cost),
        "unused_assets": max(0, 100 - unused_cost),
    }
    overall = round(sum(component_scores.values()) / len(component_scores))
    return HealthScore(overall=overall, band=_band(overall), component_scores=component_scores)


def _relationship_graph_depth(model: SemanticModel) -> int:
    """Graph diameter (longest shortest path) over the undirected relationship
    graph — a proxy for how deep filter propagation can travel."""
    adjacency: dict[str, set[str]] = {}
    for r in model.relationships:
        adjacency.setdefault(r.from_table, set()).add(r.to_table)
        adjacency.setdefault(r.to_table, set()).add(r.from_table)
    if not adjacency:
        return 0

    def bfs_eccentricity(start: str) -> int:
        visited = {start: 0}
        frontier = [start]
        while frontier:
            nxt = []
            for node in frontier:
                for neighbor in adjacency.get(node, ()):
                    if neighbor not in visited:
                        visited[neighbor] = visited[node] + 1
                        nxt.append(neighbor)
            frontier = nxt
        return max(visited.values())

    return max(bfs_eccentricity(node) for node in adjacency)


def compute_complexity(model: SemanticModel) -> ComplexityAssessment:
    table_count = len(model.tables)
    measure_count = len(model.all_measures())
    relationship_count = len(model.relationships)
    calculated_column_count = sum(1 for t in model.tables for c in t.columns if c.is_calculated)
    max_depth = _relationship_graph_depth(model)

    reasons = []
    if table_count > 30:
        reasons.append(f"{table_count} tables")
    if measure_count > 90:
        reasons.append(f"{measure_count} measures")
    if relationship_count > 45:
        reasons.append(f"{relationship_count} relationships")

    if reasons:
        level = "High"
        rationale = "High complexity driven by " + ", ".join(reasons) + "."
    elif table_count <= 10 and measure_count <= 30 and relationship_count <= 15:
        level = "Low"
        rationale = (
            f"Low complexity: {table_count} tables, {measure_count} measures, "
            f"{relationship_count} relationships — a compact model."
        )
    else:
        level = "Medium"
        rationale = (
            f"Medium complexity: {table_count} tables, {measure_count} measures, "
            f"{relationship_count} relationships — larger than a starter model but "
            f"not yet at a scale that needs active complexity management."
        )
    return ComplexityAssessment(
        level=level, table_count=table_count, measure_count=measure_count,
        relationship_count=relationship_count, calculated_column_count=calculated_column_count,
        max_relationship_depth=max_depth, rationale=rationale,
    )


# -- DAX review -----------------------------------------------------------------
def _paren_depth(expr: str) -> int:
    depth = max_depth = 0
    for ch in expr:
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth = max(0, depth - 1)
    return max_depth


def _naming_issue(name: str) -> str:
    if _GENERIC_NAME.match(name.strip()):
        return f"'{name}' is a generic, non-descriptive name."
    name_lower = name.lower()
    for typo, correction in _NAME_TYPOS.items():
        if typo in name_lower:
            return f"'{name}' may contain a typo — possibly '{correction}'."
    return ""


def _repeated_pattern(expr: str) -> str:
    """Flag a sub-expression (a non-nested function call) that appears
    verbatim 3+ times — a signal the measure would benefit from a ``VAR``."""
    calls = re.findall(r"\b[A-Z][A-Z0-9_.]*\([^()]*\)", expr.upper())
    counts = Counter(calls)
    repeated = [c for c, n in counts.items() if n >= 3 and len(c) > 8]
    if repeated:
        return f"The pattern `{repeated[0]}` appears {counts[repeated[0]]} times — consider a VAR."
    return ""


def find_dax_findings(measures: list[Measure]) -> list[DaxFinding]:
    findings: list[DaxFinding] = []
    normalized_groups: dict[str, list[Measure]] = {}
    for m in measures:
        normalized = re.sub(r"\s+", "", (m.expression or "")).upper()
        if normalized:
            normalized_groups.setdefault(normalized, []).append(m)

    flagged_duplicates: set[str] = set()
    for group in normalized_groups.values():
        if len(group) > 1:
            names = [g.name for g in group]
            for m in group:
                if m.name in flagged_duplicates:
                    continue
                flagged_duplicates.add(m.name)
                others = ", ".join(n for n in names if n != m.name)
                findings.append(DaxFinding(
                    measure=m.name, table=m.table, kind="duplicate_logic",
                    detail=f"Identical DAX logic to: {others}.", severity="Medium",
                ))

    for m in measures:
        expr = m.expression or ""
        if len(expr) > 500 or _paren_depth(expr) > 8:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="very_long_expression",
                detail=f"Expression is {len(expr)} characters with nesting depth {_paren_depth(expr)} — "
                       f"hard to read and maintain.",
                severity="High",
            ))
        if not m.description:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="missing_description",
                detail="No description set for this measure.", severity="Low",
            ))
        naming = _naming_issue(m.name)
        if naming:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="naming_issue",
                detail=naming, severity="Low",
            ))
        repeated = _repeated_pattern(expr)
        if repeated:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="repeated_pattern",
                detail=repeated, severity="Medium",
            ))
    return findings


# -- Best practices --------------------------------------------------------------
def check_best_practices(model: SemanticModel) -> list[BestPracticeCheck]:
    checks: list[BestPracticeCheck] = []
    shape, facts, dims = schema_shape(model)

    is_star = shape.startswith("a star schema")
    checks.append(BestPracticeCheck(
        id="star_schema", name="Star schema", passed=is_star, category="schema",
        detail=f"Model shape detected as {shape}." if not is_star else
               f"Model follows a star schema — {shape}.",
    ))

    fact_dim_ok = bool(facts) and bool(dims)
    checks.append(BestPracticeCheck(
        id="fact_dimension_separation", name="Fact/dimension separation", passed=fact_dim_ok,
        category="schema",
        detail=(f"{len(facts)} fact table(s), {len(dims)} dimension table(s)." if fact_dim_ok
                else "No clear fact/dimension separation was detected."),
    ))

    generic_names = [t.name for t in model.tables if _GENERIC_NAME.match(t.name.strip())]
    checks.append(BestPracticeCheck(
        id="naming_conventions", name="Table naming conventions", passed=not generic_names,
        category="naming",
        detail=(f"Generic/default table name(s) found: {', '.join(generic_names)}." if generic_names
                else "No generic/default table names detected."),
    ))

    measures = model.all_measures()
    with_folder = sum(1 for m in measures if m.display_folder)
    folder_ok = not measures or (with_folder / len(measures)) >= 0.5
    checks.append(BestPracticeCheck(
        id="display_folder_usage", name="Display folder usage", passed=folder_ok,
        category="documentation",
        detail=(f"{with_folder}/{len(measures)} measures use a display folder." if measures
                else "No measures to organize."),
    ))

    visible_columns = [c for t in model.tables for c in t.columns if not c.is_hidden]
    described = sum(1 for m in measures if m.description) + sum(1 for c in visible_columns if c.description)
    total_describable = len(measures) + len(visible_columns)
    desc_ok = total_describable == 0 or (described / total_describable) >= 0.5
    checks.append(BestPracticeCheck(
        id="description_coverage", name="Description coverage", passed=desc_ok,
        category="documentation",
        detail=(f"{described}/{total_describable} measures and visible columns have a description."
                if total_describable else "Nothing to describe."),
    ))

    date_table = None
    for t in model.tables:
        if re.search(r"date|calendar", t.name, re.IGNORECASE):
            if any(c.data_type in ("dateTime", "date") for c in t.columns):
                date_table = t.name
                break
    checks.append(BestPracticeCheck(
        id="date_table_present", name="Dedicated date table", passed=date_table is not None,
        category="modeling",
        detail=(f"'{date_table}' looks like the model's date table." if date_table
                else "No dedicated date/calendar table with a date-typed column was found."),
    ))

    id_like = [c for t in model.tables for c in t.columns if _ID_LIKE_NAME.search(c.name)]
    hidden_id_like = [c for c in id_like if c.is_hidden]
    hidden_ok = not id_like or (len(hidden_id_like) / len(id_like)) >= 0.7
    checks.append(BestPracticeCheck(
        id="hidden_technical_columns", name="Hidden technical/key columns", passed=hidden_ok,
        category="modeling",
        detail=(f"{len(hidden_id_like)}/{len(id_like)} ID/key-like columns are hidden." if id_like
                else "No ID/key-like columns found."),
    ))

    used_cols = used_column_names(model)
    unused_calc_cols = [c for c in calc_columns(model) if c["column"] not in used_cols]
    checks.append(BestPracticeCheck(
        id="unused_calculated_columns", name="Unused calculated columns", passed=not unused_calc_cols,
        category="modeling",
        detail=(f"{len(unused_calc_cols)} calculated column(s) are not referenced by any visual: "
                + ", ".join(f"{c['table']}[{c['column']}]" for c in unused_calc_cols) + "."
                if unused_calc_cols else "Every calculated column is referenced by a visual."),
    ))

    inactive = [r for r in model.relationships if not r.is_active]
    checks.append(BestPracticeCheck(
        id="inactive_relationships", name="Inactive relationships", passed=not inactive,
        category="modeling",
        detail=(f"{len(inactive)} inactive relationship(s) — only reachable via USERELATIONSHIP()."
                if inactive else "No inactive relationships."),
    ))

    bidirectional = [r for r in model.relationships if r.cross_filter == "both"]
    checks.append(BestPracticeCheck(
        id="bidirectional_filters", name="Bidirectional cross-filtering", passed=not bidirectional,
        category="modeling",
        detail=(f"{len(bidirectional)} relationship(s) use bidirectional cross-filtering, which can "
                f"create ambiguous filter paths." if bidirectional
                else "No bidirectional relationships."),
    ))

    many_to_many = [r for r in model.relationships
                     if r.from_cardinality == "many" and r.to_cardinality == "many"]
    checks.append(BestPracticeCheck(
        id="many_to_many_relationships", name="Many-to-many relationships", passed=not many_to_many,
        category="modeling",
        detail=(f"{len(many_to_many)} many-to-many relationship(s) detected." if many_to_many
                else "No many-to-many relationships."),
    ))

    # Circular-dependency risk: union-find over the graph of *active*
    # relationships only — an edge joining two tables already in the same
    # connected component means more than one active filter path connects
    # them. Inactive relationships (e.g. a second OrderDate/ShipDate link to
    # the same date table) are excluded: they don't propagate filters unless
    # a specific DAX measure activates them via USERELATIONSHIP(), so a
    # second inactive edge between two already-related tables is normal
    # modeling, not a circular-dependency risk.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            x = parent[x]
        return x

    has_cycle = False
    for r in model.relationships:
        if not r.is_active:
            continue
        ra, rb = find(r.from_table), find(r.to_table)
        if ra == rb:
            has_cycle = True
        else:
            parent[ra] = rb
    checks.append(BestPracticeCheck(
        id="circular_dependency_risk", name="No circular relationship paths", passed=not has_cycle,
        category="modeling",
        detail=("A cycle was found in the relationship graph — more than one filter path connects "
                "some tables, which risks ambiguous or double-counted results." if has_cycle
                else "No circular relationship paths detected."),
    ))

    return checks


# -- Performance risks (heuristic — no row data exists to measure this) ----------
def find_performance_risks(model: SemanticModel) -> list[PerformanceRisk]:
    risks: list[PerformanceRisk] = []

    for t in model.tables:
        for c in t.columns:
            if c.is_calculated and c.expression and len(c.expression) > 200:
                risks.append(PerformanceRisk(
                    kind="large_calc_column", object_name=c.name, table=t.name,
                    detail=f"Calculated column expression is {len(c.expression)} characters — "
                           f"heuristic signal from expression size, not measured against actual "
                           f"row counts (no row-level data is extracted).",
                    severity="Medium",
                ))
            if _ID_LIKE_NAME.search(c.name) and not c.is_hidden:
                risks.append(PerformanceRisk(
                    kind="high_cardinality_signal", object_name=c.name, table=t.name,
                    detail=f"'{c.name}' is named like an identifier and is visible — heuristic signal "
                           f"of high cardinality from naming/type only, not measured against actual data.",
                    severity="Low",
                ))
            if c.data_type in ("string", "text") and _TEXT_BLOB_NAME.search(c.name) and not c.display_folder:
                risks.append(PerformanceRisk(
                    kind="large_text_column", object_name=c.name, table=t.name,
                    detail=f"'{c.name}' looks like a free-text column (name pattern + string type) — "
                           f"heuristic signal only, not measured against actual text length.",
                    severity="Low",
                ))

    for m in model.all_measures():
        expr = m.expression or ""
        iterator_count = len(re.findall(r"\b(SUMX|FILTER|CALCULATE|AVERAGEX|MINX|MAXX)\s*\(", expr.upper()))
        if iterator_count >= 3:
            risks.append(PerformanceRisk(
                kind="heavy_dax", object_name=m.name, table=m.table,
                detail=f"{iterator_count} nested iterator/filter calls (SUMX/FILTER/CALCULATE/…) — "
                       f"a static-analysis proxy for evaluation cost, not a measured runtime.",
                severity="Medium",
            ))

    high_card_slicer_targets = {
        r.object_name for r in risks if r.kind == "high_cardinality_signal"
    }
    for p in model.pages:
        visible_visuals = [v for v in p.visuals if not v.is_slicer]
        if len(visible_visuals) > 12:
            risks.append(PerformanceRisk(
                kind="visual_density", object_name=p.display_name, table=None,
                detail=f"{len(visible_visuals)} visuals on one page — more visuals means more "
                       f"concurrent queries on every filter change.",
                severity="Low",
            ))
        for v in p.visuals:
            if v.is_slicer:
                field_leaf = (v.fields[0].split(".")[-1] if v.fields else None)
                if field_leaf and field_leaf in high_card_slicer_targets:
                    risks.append(PerformanceRisk(
                        kind="slow_slicer_signal", object_name=field_leaf, table=None,
                        detail=f"Slicer on '{field_leaf}' (page '{p.display_name}') is bound to a "
                               f"column flagged as a high-cardinality signal — heuristic only.",
                        severity="Low",
                    ))

    table_risk_count: dict[str, int] = {}
    for r in model.relationships:
        if r.cross_filter == "both" or not r.is_active:
            table_risk_count[r.from_table] = table_risk_count.get(r.from_table, 0) + 1
            table_risk_count[r.to_table] = table_risk_count.get(r.to_table, 0) + 1
    for table, count in table_risk_count.items():
        if count > 2:
            risks.append(PerformanceRisk(
                kind="cross_filter_complexity", object_name=table, table=table,
                detail=f"'{table}' participates in {count} bidirectional/inactive relationships — "
                       f"filter propagation through this table is hard to reason about.",
                severity="Medium",
            ))

    return risks


# -- Governance --------------------------------------------------------------------
def check_governance(
    model: SemanticModel, *, owner: str | None = None, classification: str | None = None,
) -> list[GovernanceFinding]:
    findings: list[GovernanceFinding] = []

    if not model.roles:
        findings.append(GovernanceFinding(
            area="rls", detail="No row-level security roles are defined in this model.", severity="Medium",
        ))
    else:
        for r in model.roles:
            if not r.members:
                findings.append(GovernanceFinding(
                    area="rls", detail=f"Role '{r.name}' has no members assigned in the model file "
                                        f"(membership may be managed in the Power BI Service).",
                    severity="Low",
                ))

    measures = model.all_measures()
    visible_columns = [c for t in model.tables for c in t.columns if not c.is_hidden]
    described = sum(1 for m in measures if m.description) + sum(1 for c in visible_columns if c.description)
    total = len(measures) + len(visible_columns)
    if total and (described / total) < 0.5:
        pct = round(100 * described / total)
        findings.append(GovernanceFinding(
            area="descriptions",
            detail=f"Only {pct}% of measures/visible columns have a description.",
            severity="Medium",
        ))

    if not owner and not classification:
        findings.append(GovernanceFinding(
            area="ownership",
            detail="No owner or data classification was specified when this documentation was generated.",
            severity="Medium",
        ))

    sensitive = [c.name for t in model.tables for c in t.columns
                 if _SENSITIVE_NAME.search(c.name) and not c.is_hidden]
    if sensitive:
        findings.append(GovernanceFinding(
            area="sensitive_columns",
            detail=f"Visible column name(s) suggest sensitive data: {', '.join(sensitive)}.",
            severity="High",
        ))

    modes = {p.mode for t in model.tables for p in t.partitions if p.mode}
    if len(modes) > 1:
        findings.append(GovernanceFinding(
            area="data_source_consistency",
            detail=f"Mixed storage modes detected across tables ({', '.join(sorted(modes))}), "
                   f"which can complicate refresh behavior.",
            severity="Low",
        ))

    return findings


# -- Unused assets -----------------------------------------------------------------
def find_unused_assets(model: SemanticModel) -> UnusedAssets:
    used_measures = used_measure_names(model)
    used_cols = used_column_names(model)

    unused_measures = [m.name for m in model.all_measures() if m.name not in used_measures]
    unused_columns = [
        {"table": t.name, "column": c.name}
        for t in model.tables for c in t.columns
        if not c.is_hidden and not c.is_calculated and c.name not in used_cols
    ]
    unused_calc_cols = [c for c in calc_columns(model) if c["column"] not in used_cols]

    related_tables = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    unused_tables = [
        t.name for t in model.tables
        if t.name not in related_tables
        and not any(m.name in used_measures for m in t.measures)
        and not any(c.name in used_cols for c in t.columns)
    ]

    unused_pages = [p["name"] for p in report_pages(model)
                    if p["hidden"] and not p["drillthrough"]]

    return UnusedAssets(
        measures=unused_measures,
        columns=unused_columns,
        tables=unused_tables,
        calculated_columns=[{"table": c["table"], "column": c["column"]} for c in unused_calc_cols],
        report_pages=unused_pages,
    )


# -- Recommendations ----------------------------------------------------------------
_PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

_DAX_TEMPLATES = {
    "duplicate_logic": ("Medium",
        "Multiple measures share identical DAX logic.",
        "A future rule change has to be applied in every duplicate, risking drift between measures that should stay in sync.",
        "Consolidate into one base measure and have the others reference it.",
        "One source of truth for the calculation and lower maintenance risk."),
    "very_long_expression": ("Medium",
        "Some measures have very long or deeply nested DAX expressions.",
        "Long, deeply nested expressions are hard to read, debug, and hand off to another developer.",
        "Break the expression into intermediate measures or use VAR to name sub-steps.",
        "Easier debugging and faster onboarding for anyone maintaining the model."),
    "missing_description": ("Low",
        "Some measures have no description.",
        "Without a description, business meaning has to be reverse-engineered from the DAX every time.",
        "Add a one-sentence description to each measure explaining what it calculates and why.",
        "Faster onboarding and fewer misinterpretations of what a measure means."),
    "naming_issue": ("Low",
        "Some measures have generic or possibly misspelled names.",
        "Unclear names slow down report authors trying to find the right measure.",
        "Rename to a clear, business-meaningful name.",
        "Faster measure discovery in the field list."),
    "repeated_pattern": ("Medium",
        "Some measures repeat the same sub-expression multiple times.",
        "Repeated sub-expressions are recalculated redundantly and are harder to maintain consistently.",
        "Extract the repeated pattern into a VAR.",
        "Clearer, and potentially faster, DAX."),
}

_PRACTICE_TEMPLATES = {
    "star_schema": ("Medium",
        "The model does not follow a star schema.",
        "Non-star shapes (snowflake, galaxy, flat) typically perform worse and are harder to reason about for report authors.",
        "Where practical, re-model dimensions to relate directly to fact tables.",
        "Simpler, faster, more predictable filter propagation."),
    "fact_dimension_separation": ("Medium",
        "Fact and dimension roles are not clearly separated.",
        "Without clear fact/dimension separation, it's unclear which tables should drive filtering versus aggregation.",
        "Classify tables explicitly and separate transactional facts from descriptive dimensions.",
        "Clearer model semantics for anyone extending the report."),
    "naming_conventions": ("Low",
        "Some tables use generic/default names (e.g. 'Table1', 'Query1').",
        "Default names give report authors no indication of what a table contains.",
        "Rename tables to reflect their business content.",
        "Faster navigation for anyone building new visuals."),
    "display_folder_usage": ("Low",
        "Few measures are organized into display folders.",
        "Without display folders, the field list becomes a long, unorganized list as the model grows.",
        "Group related measures into display folders (e.g. 'Sales', 'Time Intelligence').",
        "A field list that's easier to navigate for report authors."),
    "description_coverage": ("Medium",
        "Many measures/columns have no description.",
        "Missing descriptions mean new team members must reverse-engineer intent from DAX and column names alone.",
        "Add descriptions to the most-used measures and columns first.",
        "Faster onboarding and self-documenting metadata in Power BI Desktop tooltips."),
    "date_table_present": ("Medium",
        "No dedicated date/calendar table was detected.",
        "Time intelligence functions (YTD, prior period, etc.) rely on a proper date table to behave correctly.",
        "Add a dedicated date table marked as a date table, related to all fact tables.",
        "Correct, reliable time-intelligence calculations."),
    "hidden_technical_columns": ("Low",
        "Some ID/key-style columns are visible to report authors.",
        "Visible technical join keys clutter the field list and can be mis-used in visuals by mistake.",
        "Hide columns that exist only to support relationships.",
        "A cleaner field list focused on business-relevant fields."),
    "unused_calculated_columns": ("Low",
        "Some calculated columns are never used in any report visual.",
        "Unused calculated columns still consume model size and refresh time for no analytical benefit.",
        "Remove calculated columns that are not referenced anywhere, or confirm they are needed for row-level security/relationships.",
        "Smaller model size and faster refreshes."),
    "inactive_relationships": ("Low",
        "Some relationships are inactive.",
        "Inactive relationships only take effect via USERELATIONSHIP() in DAX — easy to forget and easy to misconfigure.",
        "Document where each inactive relationship is activated, or make it the active relationship if that's the common case.",
        "Less risk of a report silently using the wrong relationship."),
    "bidirectional_filters": ("High",
        "Some relationships use bidirectional cross-filtering.",
        "Bidirectional filters can create ambiguous filter paths and materially slow down query performance.",
        "Switch to single-direction filtering and use CROSSFILTER() in specific DAX measures where bidirectional behavior is truly needed.",
        "More predictable filtering and better query performance."),
    "many_to_many_relationships": ("High",
        "Some relationships are many-to-many.",
        "Many-to-many relationships are the most common source of unexpected double-counting in Power BI.",
        "Introduce a bridge table with a unique key, or confirm the many-to-many behavior is intentional and documented.",
        "Correct aggregation results."),
    "circular_dependency_risk": ("Critical",
        "The relationship graph contains a cycle.",
        "More than one filter path between the same tables risks ambiguous or double-counted results that are hard to debug.",
        "Remove or deactivate one of the relationships forming the cycle.",
        "Deterministic, single-path filter propagation."),
}

_PERF_TEMPLATES = {
    "large_calc_column": ("Low",
        "Some calculated columns have very large expressions.",
        "Calculated columns are computed and stored for every row at refresh time — large formulas increase refresh time and model size.",
        "Consider whether the calculation can be a measure (computed on demand) instead of a calculated column.",
        "Faster refreshes and a smaller model, if converted to a measure."),
    "high_cardinality_signal": ("Low",
        "Some visible columns look like high-cardinality identifiers.",
        "High-cardinality columns compress poorly and can slow down the model if used in visuals or relationships.",
        "Hide the column if it's a technical key, or confirm its cardinality is acceptable for the intended use.",
        "Smaller model size and better query performance, if confirmed unnecessary."),
    "large_text_column": ("Low",
        "Some columns look like free-text fields.",
        "Long free-text columns are expensive to store and rarely useful for aggregation or filtering.",
        "Confirm the column is needed in the model, or exclude it from the import if it's not used in any visual.",
        "Smaller model size."),
    "heavy_dax": ("Medium",
        "Some measures nest several iterator/filter functions.",
        "Deeply nested iterators (SUMX/FILTER/CALCULATE) are more expensive to evaluate, especially over large tables.",
        "Review whether the nesting can be simplified, or pre-aggregate with a calculated table.",
        "Potentially faster report rendering."),
    "visual_density": ("Low",
        "Some report pages have a large number of visuals.",
        "Every visual on a page issues its own query on load and on every filter change — more visuals means more concurrent load.",
        "Split dense pages into multiple focused pages, or use bookmarks to progressively reveal detail.",
        "Faster page load and filter response times."),
    "slow_slicer_signal": ("Low",
        "Some slicers are bound to high-cardinality-looking columns.",
        "Slicers on high-cardinality columns can be slow to render and awkward for users to search.",
        "Consider a search-enabled slicer, a hierarchy, or a lower-cardinality grouping column instead.",
        "A more responsive, more usable filter experience."),
    "cross_filter_complexity": ("Medium",
        "Some tables sit at the center of several bidirectional/inactive relationships.",
        "Tables with many non-standard relationships are the hardest part of a model to reason about and to keep performant.",
        "Review whether every bidirectional/inactive relationship touching this table is still needed.",
        "Simpler, more maintainable filter propagation around this table."),
}

_GOVERNANCE_TEMPLATES = {
    "rls": ("Medium",
        "Row-level security gaps were found.",
        "Without RLS (or with roles that have no members configured), sensitive rows may be visible to more users than intended.",
        "Define RLS roles for any data that should be restricted, and assign members either in the model or in the Power BI Service.",
        "Data access that matches the intended audience."),
    "descriptions": ("Medium",
        "Description coverage across measures and columns is low.",
        "Missing descriptions increase onboarding time and the risk of misinterpreting a field's meaning.",
        "Prioritize descriptions for the most-used measures and columns first.",
        "Faster onboarding and fewer misunderstandings."),
    "ownership": ("Medium",
        "No owner or data classification was specified for this report.",
        "Without a named owner, there's no clear point of contact for questions, incidents, or change requests.",
        "Assign a business owner and a data classification (e.g. Internal, Confidential) in the documentation metadata.",
        "Clear accountability and appropriate handling of the data."),
    "sensitive_columns": ("High",
        "Column names suggest sensitive data is present and visible.",
        "Visible sensitive fields (PII, financial, credentials) increase exposure risk if access isn't tightly controlled.",
        "Confirm whether these columns need RLS/OLS, or hide/remove them if they're not needed in the model.",
        "Reduced risk of unintended exposure of sensitive data."),
    "data_source_consistency": ("Low",
        "Tables use mixed storage modes.",
        "Mixed Import/DirectQuery modes complicate refresh behavior and can produce inconsistent performance across the report.",
        "Standardize on one storage mode where practical, or document why a mixed configuration is intentional.",
        "More predictable refresh and query performance."),
}


def _recommendation(priority: str, issue: str, why: str, fix: str, benefit: str) -> Recommendation:
    return Recommendation(priority=priority, issue=issue, why_it_matters=why,
                          suggested_fix=fix, expected_benefit=benefit)


def build_recommendations(
    dax_findings: list[DaxFinding],
    best_practices: list[BestPracticeCheck],
    performance_risks: list[PerformanceRisk],
    governance: list[GovernanceFinding],
    unused_assets: UnusedAssets,
) -> list[Recommendation]:
    """One templated recommendation per distinct finding kind actually present,
    sorted by priority. Templated prose, not LLM output — reproducible and
    testable."""
    recs: list[Recommendation] = []

    seen_dax = {f.kind for f in dax_findings}
    for kind in seen_dax:
        if kind in _DAX_TEMPLATES:
            recs.append(_recommendation(*_DAX_TEMPLATES[kind]))

    for check in best_practices:
        if not check.passed and check.id in _PRACTICE_TEMPLATES:
            recs.append(_recommendation(*_PRACTICE_TEMPLATES[check.id]))

    seen_perf = {f.kind for f in performance_risks}
    for kind in seen_perf:
        if kind in _PERF_TEMPLATES:
            recs.append(_recommendation(*_PERF_TEMPLATES[kind]))

    seen_gov = {f.area for f in governance}
    for area in seen_gov:
        if area in _GOVERNANCE_TEMPLATES:
            recs.append(_recommendation(*_GOVERNANCE_TEMPLATES[area]))

    unused_total = (
        len(unused_assets.measures) + len(unused_assets.columns)
        + len(unused_assets.tables) + len(unused_assets.calculated_columns)
        + len(unused_assets.report_pages)
    )
    if unused_total:
        recs.append(_recommendation(
            "Low",
            f"{unused_total} unused asset(s) were found (measures, columns, tables, calculated "
            f"columns, or hidden non-drillthrough pages).",
            "Unused assets add to model size, refresh time, and cognitive load for anyone maintaining the report.",
            "Remove unused assets, or confirm they're intentionally kept for a documented reason (e.g. future use, RLS support).",
            "A leaner, easier-to-maintain model.",
        ))

    recs.sort(key=lambda r: _PRIORITY_ORDER.get(r.priority, 4))
    return recs
