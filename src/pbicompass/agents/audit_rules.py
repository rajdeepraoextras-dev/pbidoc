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
from .report_facts import calc_columns, detect_hardcoded_years, local_path_sources, report_pages
from .usage import used_column_names, used_measure_names

_SEVERITY_COST = {"Critical": 15, "High": 8, "Medium": 4, "Low": 1}

# -- Rules Engine & Configuration (Phase 4) ---------------------------------------

FINDING_RULES = {
    # DaxFinding kind
    "duplicate_logic": "PBIC-DAX-001",
    "very_long_expression": "PBIC-DAX-002",
    "missing_description": "PBIC-DAX-003",
    "naming_issue": "PBIC-DAX-004",
    "repeated_pattern": "PBIC-DAX-005",
    "hardcoded_year": "PBIC-DAX-006",
    "deep_measure_chain": "PBIC-DAX-010",
    "iferror_use": "PBIC-DAX-008",
    "division_slash": "PBIC-DAX-009",
    "calculate_no_filters": "PBIC-DAX-011",
    "calculate_in_calculate": "PBIC-DAX-013",
    "blank_comparisons": "PBIC-DAX-014",
    
    # BestPracticeCheck id
    "star_schema": "PBIC-MOD-007",
    "fact_dimension_separation": "PBIC-MOD-008",
    "naming_conventions": "PBIC-NAM-001",
    "display_folder_usage": "PBIC-DOC-001",
    "description_coverage": "PBIC-DOC-002",
    "date_table_present": "PBIC-MOD-005",
    "hidden_technical_columns": "PBIC-MOD-006",
    "unused_calculated_columns": "PBIC-MOD-010",
    "inactive_relationships": "PBIC-MOD-009",
    "bidirectional_filters": "PBIC-MOD-001",
    "many_to_many_relationships": "PBIC-MOD-002",
    "circular_dependency_risk": "PBIC-MOD-003",
    "disconnected_tables": "PBIC-MOD-004",
    "dev_leftover_naming": "PBIC-NAM-002",
    "summarize_by_keys": "PBIC-MOD-012",
    "snowflake_depth": "PBIC-MOD-013",
    "column_naming_conventions": "PBIC-NAM-004",
    "m2m_no_bridge": "PBIC-MOD-014",
    "bidirectional_fact": "PBIC-MOD-015",
    
    # PerformanceRisk kind
    "large_calc_column": "PBIC-PERF-001",
    "high_cardinality_signal": "PBIC-PERF-002",
    "large_text_column": "PBIC-PERF-003",
    "heavy_dax": "PBIC-DAX-007",
    "visual_density": "PBIC-PERF-004",
    "slow_slicer_signal": "PBIC-PERF-005",
    "cross_filter_complexity": "PBIC-PERF-006",
    "auto_datetime": "PBIC-PERF-007",
    "high_card_rel": "PBIC-PERF-008",
    
    # GovernanceFinding area
    "rls": "PBIC-GOV-001",
    "descriptions": "PBIC-GOV-002",
    "ownership": "PBIC-GOV-003",
    "sensitive_columns": "PBIC-GOV-004",
    "data_source_consistency": "PBIC-GOV-005",
    "unused_pages": "PBIC-GOV-006",
    "empty_tables": "PBIC-GOV-007",
    "broken_relationships": "PBIC-GOV-008",
    "broken_rel_hidden_col": "PBIC-GOV-009",
    "hardcoded_paths": "PBIC-GOV-010",
}

# The number of checks a finding can actually be tagged with — i.e. distinct
# rule IDs reachable through FINDING_RULES. RULE_METADATA may document a few
# extra IDs that no finding kind maps to yet; those aren't "checks run" and
# must not inflate this count (renderers use it for the "Checks Run/Passed"
# summary and must stay honest about what was actually executed).
TOTAL_RULE_COUNT = len(set(FINDING_RULES.values()))

RULE_METADATA = {
    "PBIC-MOD-001": ("modeling", "Medium", "Bidirectional cross-filtering",
                     "Relationships that filter in both directions can create ambiguous filter paths and performance drag.",
                     "Set relationship crossFilteringBehavior to single direction. C# script:\nModel.Relationships[\"FromTable_ToTable\"].CrossFilteringBehavior = CrossFilteringBehavior.OneDirection;"),
    "PBIC-MOD-002": ("modeling", "Medium", "Many-to-many relationships",
                     "Many-to-many relationships introduce ambiguity and require careful design.",
                     "Consolidate tables or introduce a bridge table with 1:M relationships."),
    "PBIC-MOD-003": ("modeling", "Critical", "Circular dependency paths",
                     "A cycle was found in the relationship graph — more than one filter path connects some tables, risking ambiguous results.",
                     "Remove redundant active relationships to break the cycle."),
    "PBIC-MOD-004": ("modeling", "Medium", "Disconnected fact/dimension tables",
                     "Fact/dimension tables have no relationships and won't filter or summarize with the rest of the model.",
                     "Create relationships between these tables and other model tables."),
    "PBIC-MOD-005": ("modeling", "High", "Dedicated date table present",
                     "No dedicated date/calendar table with a date-typed column was found.",
                     "Add a dedicated date table using CALENDARAUTO()."),
    "PBIC-MOD-006": ("modeling", "Low", "Hidden technical key columns",
                     "ID/key-like columns are visible to users, confusing the model interface.",
                     "Set the isHidden property to true for key/ID columns."),
    "PBIC-MOD-007": ("modeling", "Medium", "Star schema compliance",
                     "The model is not structured as a star schema, which is the recommended shape for Power BI models.",
                     "Refactor the model into a star schema layout."),
    "PBIC-MOD-008": ("modeling", "Low", "Fact/dimension separation compliance",
                     "No clear separation between fact and dimension tables.",
                     "Organize tables clearly into facts (data tables) and dimensions (lookup tables)."),
    "PBIC-MOD-009": ("modeling", "Low", "Inactive relationships",
                     "Inactive relationships are only reachable via USERELATIONSHIP().",
                     "Enable the relationship if it is the primary filter path, or document its usage."),
    "PBIC-MOD-010": ("modeling", "Low", "Unused calculated columns",
                     "Calculated columns are defined but not referenced by any page visuals.",
                     "Delete the calculated column to save RAM/disk space."),
    "PBIC-MOD-011": ("modeling", "Low", "Floating-point double columns",
                     "Columns with double data type can consume unnecessary dictionary space.",
                     "Change data type to fixed decimal or integer if appropriate."),
    "PBIC-MOD-012": ("modeling", "Low", "Summarize-by setting on key columns",
                     "Key or ID columns have default summarization (Sum, Average, etc.), which is rarely meaningful.",
                     "Set the SummarizeBy property to None on key/ID columns."),
    "PBIC-MOD-013": ("modeling", "Low", "Snowflake depth limit",
                     "Dimension tables are chained too deeply (> 2 levels), which slows down filter propagation.",
                     "Flatten dimension tables into a single wide table where possible."),
    "PBIC-MOD-014": ("modeling", "Medium", "Many-to-many relationship without bridge",
                     "Direct many-to-many relationships can result in slow performance.",
                     "Introduce an intermediate bridge table."),
    "PBIC-MOD-015": ("modeling", "High", "Bi-directional relationship filtering a fact table",
                     "Relationships that filter fact tables bidirectional cause major performance overhead.",
                     "Set relationship direction to single filtering dimension table."),

    "PBIC-DAX-001": ("dax", "Medium", "Duplicate DAX logic",
                     "Identical DAX logic found in multiple measures, making updates error-prone.",
                     "Consolidate identical logic into a single shared measure."),
    "PBIC-DAX-002": ("dax", "Medium", "Very long DAX expression",
                     "Expression is very long, making it hard to read and maintain.",
                     "Consolidate nested logic into VAR variables or split into multiple helper measures."),
    "PBIC-DAX-003": ("dax", "Low", "Missing measure description",
                     "No description is set for the measure.",
                     "Add a description to document the business meaning of this measure."),
    "PBIC-DAX-004": ("dax", "Low", "Naming typos",
                     "Common misspellings or typos detected in measure names.",
                     "Rename the measure with the corrected spelling."),
    "PBIC-DAX-005": ("dax", "Medium", "Repeated patterns in DAX",
                     "DAX expression calculates the same sub-expression multiple times.",
                     "Assign the sub-expression to a VAR variable and reuse it."),
    "PBIC-DAX-006": ("dax", "Critical", "Hardcoded year in DAX",
                     "Measure contains a hardcoded year value, which will cause incorrect calculations next year.",
                     "Replace the hardcoded year with relative date logic like YEAR(TODAY())."),
    "PBIC-DAX-007": ("dax", "Medium", "Nested iterator/filter calls (heavy DAX)",
                     "Measure uses nested or complex iterators (SUMX, FILTER, etc.) which can cause slow performance.",
                     "Optimize the DAX filters or push aggregation calculations back to the source/Power Query."),
    "PBIC-DAX-008": ("dax", "Medium", "Use of IFERROR / ISERROR",
                     "IFERROR/ISERROR functions force row-by-row error checking which degrades performance.",
                     "Replace with specific checks like DIVIDE or check for blank values."),
    "PBIC-DAX-009": ("dax", "Medium", "Division using / instead of DIVIDE",
                     "Division using / does not automatically handle division by zero.",
                     "Use the DIVIDE function for safe division."),
    "PBIC-DAX-010": ("dax", "Low", "Measure chain depth limit",
                     "Dependency chain of measures is too deep (> 3 levels), which is fragile and hard to debug.",
                     "Simplify the dependency chain or consolidate intermediate calculations."),
    "PBIC-DAX-011": ("dax", "Low", "Use of CALCULATE inside a measure without filter arguments",
                     "Using CALCULATE redudantly without arguments degrades readability and performance.",
                     "Remove CALCULATE wrapper from around the measure/expression."),
    "PBIC-DAX-013": ("dax", "Low", "CALCULATE inside CALCULATE check",
                     "Nested CALCULATE statements can indicate confusing/over-complicated filter logic.",
                     "Refactor nested CALCULATE expressions to a single CALCULATE with multiple filter arguments."),
    "PBIC-DAX-014": ("dax", "Low", "Blank values comparisons",
                     "Explicitly comparing to BLANK() in DAX can lead to subtle evaluation performance degradation.",
                     "Use ISBLANK() function instead of equal/not-equal comparison to BLANK()."),

    "PBIC-NAM-001": ("naming", "Low", "Generic/default table names",
                     "Table names should be descriptive rather than generic defaults (e.g., Table1).",
                     "Rename the table to reflect the business entity it represents."),
    "PBIC-NAM-002": ("naming", "Medium", "No development leftovers in production",
                     "Objects containing naming patterns like 'test', 'temp', or 'copy' are likely leftovers.",
                     "Remove or rename development leftovers before production deployment."),
    "PBIC-NAM-003": ("naming", "Low", "Visual default titles",
                     "Visuals should have customized, descriptive titles rather than default ones.",
                     "Set descriptive custom titles for report visuals."),
    "PBIC-NAM-004": ("naming", "Low", "Column naming conventions (typos)",
                     "Common misspellings or typos detected in column names.",
                     "Rename the column with the corrected spelling."),
    "PBIC-NAM-005": ("naming", "Low", "Measure default naming check",
                     "Measures should not start with default prefixes like 'Measure'.",
                     "Provide a meaningful name for the measure."),

    "PBIC-PERF-001": ("performance", "Medium", "Large calculated columns",
                     "Calculated columns are calculated at refresh time and consume database memory.",
                     "Move calculated columns upstream to Power Query or the data source."),
    "PBIC-PERF-002": ("performance", "Low", "Visible high-cardinality keys",
                     "Visible key columns that have high cardinality can cause slow performance.",
                     "Hide key columns to prevent them from being used directly in report visuals."),
    "PBIC-PERF-003": ("performance", "Low", "Visible large text columns",
                     "Visible text columns containing large amounts of text consume database memory.",
                     "Set the display folder or hide these columns if they are not needed in visuals."),
    "PBIC-PERF-004": ("performance", "Medium", "High visual density on page",
                     "Pages with more than 12 visible data visuals load slower and overwhelm users.",
                     "Distribute visuals across multiple pages or use drill-throughs."),
    "PBIC-PERF-005": ("performance", "Medium", "Slicers filtering high cardinality fields",
                     "Slicers bound to high cardinality fields (e.g. IDs) slow down page load times.",
                     "Use search-enabled filters or replace the slicer with a search bar."),
    "PBIC-PERF-006": ("performance", "Medium", "Cross filter complexity",
                     "Complex bidirectional filter propagation can cause visual loading lag.",
                     "Set crossFilteringBehavior to single direction."),
    "PBIC-PERF-007": ("performance", "High", "Auto date/time enabled",
                     "Auto date/time creates hidden calendar tables for each date column, increasing file size.",
                     "Disable Auto date/time in Options -> Data Load and use a dedicated date table."),
    "PBIC-PERF-008": ("performance", "Medium", "High cardinality relationships",
                     "Relationships built on high cardinality columns can degrade cross-filtering performance.",
                     "Verify if the key column can be simplified or integer-coded."),

    "PBIC-GOV-001": ("governance", "Medium", "RLS roles defined but have no members",
                     "RLS roles are defined in the model but have no members assigned in the model file.",
                     "Assign members to the roles or manage membership in the cloud service."),
    "PBIC-GOV-002": ("governance", "Medium", "Missing descriptions coverage",
                     "Fewer than 50% of measures and visible columns have descriptions.",
                     "Add descriptions to columns and measures to document their business purpose."),
    "PBIC-GOV-003": ("governance", "Medium", "Missing ownership/classification",
                     "No owner or data classification was specified when this documentation was generated.",
                     "Specify an owner and data classification in the enrichment file."),
    "PBIC-GOV-004": ("governance", "High", "Sensitive column name visible",
                     "Visible column names suggest they contain sensitive data (e.g., DOB, salary, credit card).",
                     "Verify security/governance compliance and hide these columns if they are not needed."),
    "PBIC-GOV-005": ("governance", "Low", "Mixed storage modes across tables",
                     "Mixed storage modes (e.g., directQuery and import) can complicate refresh behavior.",
                     "Consolidate storage modes or configure dual storage mode for dimension tables."),
    "PBIC-GOV-006": ("governance", "Low", "Unused report pages",
                     "Hidden pages that are not drillthrough targets are likely leftovers.",
                     "Delete the unused report pages."),
    "PBIC-GOV-007": ("governance", "Low", "Empty table check",
                     "Tables with zero columns or measures detected in the schema.",
                     "Remove empty tables from the model."),
    "PBIC-GOV-008": ("governance", "Medium", "Broken relationships",
                     "Relationships referencing tables or columns that do not exist in the model schema.",
                     "Repair or delete broken relationships."),
    "PBIC-GOV-009": ("governance", "Low", "Broken relationships referencing hidden columns",
                     "Relationships referencing hidden columns can lead to unexpected model filtering behavior.",
                     "Verify column visibility in relations."),
    "PBIC-GOV-010": ("governance", "High", "Hardcoded local file path in data source",
                     "A data source references a local file path (e.g. a personal drive or workstation folder) rather than a shared, refreshable location.",
                     "Move the source file to a shared location (SharePoint, a network share, or a database) and repoint the query."),
    "PBIC-DOC-001": ("governance", "Low", "Display folder usage",
                     "Fewer than 50% of measures are organized in display folders.",
                     "Create display folders to organize measures into logical groups."),
    "PBIC-DOC-002": ("governance", "Low", "Description coverage",
                     "Fewer than 50% of measures and visible columns have a description.",
                     "Provide descriptions to columns and measures."),
    "PBIC-DOC-003": ("governance", "Low", "Changelog and history section absent",
                     "No changelog or version history section is present in the model or metadata.",
                     "Create a changelog/version history to record changes to the model."),
    "PBIC-PERF-009": ("performance", "Medium", "Calculated table performance impact",
                     "Calculated tables are computed in memory during refresh and can cause memory pressure.",
                     "Push table calculations back to Power Query or data source where possible."),
}

_suppressed_rules_run = []
_rules_override_config = {}

def set_rules_override_config(config: dict):
    global _rules_override_config
    _rules_override_config = config

def get_suppressed_rules() -> list[str]:
    return sorted(list(set(_suppressed_rules_run)))

def reset_suppressed_rules():
    global _suppressed_rules_run
    _suppressed_rules_run = []

def load_rules_config() -> dict:
    import tomllib
    from pathlib import Path
    config = {}
    for name in ("pbicompass.rules.toml", "rules.toml"):
        p = Path(name)
        if p.exists():
            try:
                config = tomllib.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    if _rules_override_config:
        rules = config.setdefault("rules", {})
        for rid in _rules_override_config.get("suppressed_rules", []):
            rules.setdefault(rid, {})["enabled"] = False
        for rid, sev in _rules_override_config.get("severity_overrides", {}).items():
            rules.setdefault(rid, {})["severity"] = sev
    return config

def process_finding(finding_obj, key: str) -> bool:
    rule_id = FINDING_RULES.get(key, "")
    if not rule_id:
        return True
    
    finding_obj.rule_id = rule_id
    config = load_rules_config()
    rcfg = config.get("rules", {}).get(rule_id, {})
    
    if rcfg.get("enabled", True) is False:
        global _suppressed_rules_run
        if rule_id not in _suppressed_rules_run:
            _suppressed_rules_run.append(rule_id)
        return False
        
    if hasattr(finding_obj, "severity") and "severity" in rcfg:
        finding_obj.severity = rcfg["severity"]
        
    return True

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
# Dev-only scaffolding that occasionally ships to production untouched.
_DEV_LEFTOVER_NAME = re.compile(r"^(test|tmp|temp|copy of|backup|sheet\d+|table\d+)\b", re.IGNORECASE)
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

    def _count_note(findings: list, label: str) -> str:
        if not findings:
            return f"No {label} identified."
        worst = min(findings, key=lambda f: _PRIORITY_ORDER.get(f.severity, 4))
        return f"{len(findings)} {label} (worst severity: {worst.severity})."

    component_notes = {
        "modeling": (f"{len(failed_practices)} of {len(best_practices)} best-practice check(s) failed."
                     if failed_practices else "All best-practice checks passed."),
        "dax": _count_note(dax_findings, "DAX finding(s)"),
        "governance": _count_note(governance, "governance finding(s)"),
        "performance": _count_note(performance_risks, "performance risk signal(s)"),
        "unused_assets": (f"{unused_count} unused asset(s) add size and maintenance load without analytical value."
                          if unused_count else "Every measure, column, table, and page is in use."),
    }
    overall = round(sum(component_scores.values()) / len(component_scores))
    return HealthScore(overall=overall, band=_band(overall),
                       component_scores=component_scores, component_notes=component_notes)


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

    # Calculate measure dependency depths
    measure_names = {m.name for m in measures}
    from .deterministic import _measure_refs
    measure_deps = {}
    for m in measures:
        measure_deps[m.name] = [d for d in _measure_refs(m.expression or "") if d in measure_names and d != m.name]

    memo = {}
    def get_depth(name, path):
        if name in path:
            return 0
        if name in memo:
            return memo[name]
        deps = measure_deps.get(name, [])
        if not deps:
            return 1
        path.add(name)
        max_d = max(get_depth(d, path) for d in deps) if deps else 0
        path.remove(name)
        memo[name] = 1 + max_d
        return memo[name]

    for m in measures:
        expr = m.expression or ""
        depth = get_depth(m.name, set())
        if depth > 3:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="deep_measure_chain",
                detail=f"Dependency chain is {depth} levels deep (maximum recommended is 3) — "
                       f"deep chains are fragile and hard to debug.",
                severity="Low",
            ))
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
        years = detect_hardcoded_years(expr)
        if years:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="hardcoded_year",
                detail=f"Contains hardcoded year value(s) ({', '.join(years)}) — this measure stops "
                       f"reflecting current data at the next year boundary.",
                severity="Critical",
            ))
        
        # PBIC-DAX-008: IFERROR / ISERROR
        if "IFERROR(" in expr.upper() or "ISERROR(" in expr.upper():
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="iferror_use",
                detail="Uses IFERROR or ISERROR which can cause performance issues by forcing row-by-row error checking.",
                severity="Medium",
            ))
            
        # PBIC-DAX-009: Division using / without safety checks
        if "/" in expr and "DIVIDE(" not in expr.upper():
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="division_slash",
                detail="Uses '/' operator for division instead of DIVIDE() function which safely handles division by zero.",
                severity="Medium",
            ))
            
        # PBIC-DAX-011: CALCULATE without filter arguments
        if "CALCULATE(" in expr.upper():
            if re.search(r"CALCULATE\s*\(\s*\[?[A-Za-z0-9_ ]+\]?\s*\)", expr, re.IGNORECASE):
                findings.append(DaxFinding(
                    measure=m.name, table=m.table, kind="calculate_no_filters",
                    detail="Uses CALCULATE() without filter arguments, which is redundant.",
                    severity="Low",
                ))
        
        # PBIC-DAX-013: CALCULATE inside CALCULATE
        if len(re.findall(r"\bCALCULATE\b", expr.upper())) > 1:
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="calculate_in_calculate",
                detail="Uses nested CALCULATE() statements, which can be hard to read and debug.",
                severity="Low",
            ))
            
        # PBIC-DAX-014: Blank values comparisons
        if "= BLANK(" in expr.upper() or "== BLANK(" in expr.upper() or "<> BLANK(" in expr.upper():
            findings.append(DaxFinding(
                measure=m.name, table=m.table, kind="blank_comparisons",
                detail="Explicitly compares to BLANK() in DAX instead of using ISBLANK() function.",
                severity="Low",
            ))

    filtered = []
    for f in findings:
        if process_finding(f, f.kind):
            filtered.append(f)
    return filtered


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

    related = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    disconnected = [t.name for t in model.tables if t.kind in ("fact", "dimension") and t.name not in related]
    checks.append(BestPracticeCheck(
        id="disconnected_tables", name="No disconnected fact/dimension tables", passed=not disconnected,
        category="modeling",
        detail=(f"{len(disconnected)} fact/dimension table(s) have no relationships and won't filter "
                f"or summarize with the rest of the model: {', '.join(disconnected)}." if disconnected
                else "Every fact/dimension table participates in at least one relationship."),
    ))

    dev_leftover = [t.name for t in model.tables if _DEV_LEFTOVER_NAME.match(t.name.strip())]
    for t in model.tables:
        dev_leftover += [f"{t.name}[{c.name}]" for c in t.columns if _DEV_LEFTOVER_NAME.match(c.name.strip())]
    checks.append(BestPracticeCheck(
        id="dev_leftover_naming", name="No development leftovers in production model", passed=not dev_leftover,
        category="naming",
        detail=(f"Object name(s) look like development leftovers, not production content: "
                f"{', '.join(dev_leftover)}." if dev_leftover
                else "No table/column names match common development-leftover patterns."),
    ))

    # PBIC-MOD-012: Summarize-by setting on key columns
    bad_summarize_keys = []
    for t in model.tables:
        for c in t.columns:
            if not c.is_hidden and (_ID_LIKE_NAME.search(c.name) or c.is_key):
                if c.summarize_by and c.summarize_by.lower() != "none":
                    bad_summarize_keys.append(f"{t.name}[{c.name}] ({c.summarize_by})")
                    
    checks.append(BestPracticeCheck(
        id="summarize_by_keys", name="Summarize-by setting on key columns", passed=not bad_summarize_keys,
        category="modeling",
        detail=(f"Visible key/ID columns have default summarization: {', '.join(bad_summarize_keys)}." if bad_summarize_keys
                else "Key/ID columns have SummarizeBy set to None."),
    ))

    # PBIC-MOD-013: Snowflake dimension depth limit
    dim_tables = {t.name for t in model.tables if t.kind == "dimension"}
    dim_deps = {}
    for r in model.relationships:
        if r.from_table in dim_tables and r.to_table in dim_tables:
            dim_deps.setdefault(r.to_table, []).append(r.from_table)
            
    memo_depth = {}
    def get_dim_depth(t_name, visited):
        if t_name in visited:
            return 0
        if t_name in memo_depth:
            return memo_depth[t_name]
        visited.add(t_name)
        parents = dim_deps.get(t_name, [])
        if not parents:
            d = 0
        else:
            d = 1 + max(get_dim_depth(p, visited) for p in parents)
        visited.remove(t_name)
        memo_depth[t_name] = d
        return d
        
    deep_snowflakes = []
    for t_name in dim_tables:
        depth = get_dim_depth(t_name, set())
        if depth > 2:
            deep_snowflakes.append(f"{t_name} (depth {depth})")
            
    checks.append(BestPracticeCheck(
        id="snowflake_depth", name="Snowflake dimension depth limit", passed=not deep_snowflakes,
        category="modeling",
        detail=(f"Dimension tables chained too deeply (> 2 levels): {', '.join(deep_snowflakes)}." if deep_snowflakes
                else "All dimension tables are within recommended chain depth of 2."),
    ))

    # PBIC-NAM-004: Column naming conventions (typos)
    col_typos = []
    for t in model.tables:
        for c in t.columns:
            name_lower = c.name.lower()
            for typo, correction in _NAME_TYPOS.items():
                if typo in name_lower:
                    col_typos.append(f"{t.name}[{c.name}] (typo: {typo} -> {correction})")
                    break
    checks.append(BestPracticeCheck(
        id="column_naming_conventions", name="Column naming conventions (typos)", passed=not col_typos,
        category="naming",
        detail=(f"Column name(s) contain typos: {', '.join(col_typos)}." if col_typos
                else "No typos detected in column names."),
    ))

    filtered = []
    for c in checks:
        if process_finding(c, c.id):
            filtered.append(c)
    return filtered


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
            if c.cardinality is not None:
                if c.cardinality > 20000 and not c.is_hidden:
                    risks.append(PerformanceRisk(
                        kind="high_cardinality_signal", object_name=c.name, table=t.name,
                        detail=f"'{c.name}' has measured cardinality of {c.cardinality} distinct values (measured via pbixray).",
                        severity="Medium",
                    ))
            elif _ID_LIKE_NAME.search(c.name) and not c.is_hidden:
                risks.append(PerformanceRisk(
                    kind="high_cardinality_signal", object_name=c.name, table=t.name,
                    detail=f"'{c.name}' is named like an identifier and is visible — heuristic signal "
                           f"of high cardinality from naming/type only, not measured against actual data.",
                    severity="Low",
                ))
            
            if c.size_bytes is not None and c.size_bytes > 5000000 and not c.is_hidden:
                risks.append(PerformanceRisk(
                    kind="large_text_column", object_name=c.name, table=t.name,
                    detail=f"'{c.name}' has measured size of {c.size_bytes} bytes (measured via pbixray).",
                    severity="Medium",
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

    # PBIC-PERF-007: Auto date/time enabled
    has_auto_date = any("LocalDateTable" in t.name or "TemplateId" in t.name for t in model.tables)
    if has_auto_date:
        risks.append(PerformanceRisk(
            kind="auto_datetime", object_name="Auto Date/Time", table=None,
            detail="Auto date/time is enabled in this model — hidden local tables are created for date columns, increasing file size.",
            severity="Medium",
        ))

    # PBIC-PERF-008: High cardinality relationships
    for r in model.relationships:
        if _ID_LIKE_NAME.search(r.from_column) or _ID_LIKE_NAME.search(r.to_column):
            if r.from_cardinality == "many" and r.to_cardinality == "many":
                risks.append(PerformanceRisk(
                    kind="high_card_rel", object_name=f"{r.from_table} -> {r.to_table}", table=None,
                    detail=f"Relationship between '{r.from_table}' and '{r.to_table}' is built on ID/key-like columns with many-to-many cardinality.",
                    severity="Medium",
                ))

    filtered = []
    for r in risks:
        if process_finding(r, r.kind):
            filtered.append(r)
    return filtered


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

    local_paths = local_path_sources(model)
    if local_paths:
        findings.append(GovernanceFinding(
            area="hardcoded_paths",
            detail=f"Hardcoded local file path(s) in data sources: {'; '.join(local_paths)}.",
            severity="High",
        ))

    # PBIC-GOV-006: Unused report pages
    unused_pages = [p["name"] for p in report_pages(model) if p["hidden"] and not p["drillthrough"]]
    for up in unused_pages:
        findings.append(GovernanceFinding(
            area="unused_pages",
            detail=f"Report page '{up}' is hidden and has no drill-through targets configured (potentially unused leftover).",
            severity="Low",
        ))

    # PBIC-GOV-007: Empty table check
    empty_tables = [t.name for t in model.tables if not t.columns and not t.measures]
    for et in empty_tables:
        findings.append(GovernanceFinding(
            area="empty_tables",
            detail=f"Table '{et}' is empty (has no columns or measures defined).",
            severity="Low",
        ))

    # PBIC-GOV-008: Broken relationships
    table_names = {t.name for t in model.tables}
    for r in model.relationships:
        if r.from_table not in table_names or r.to_table not in table_names:
            rel_name = r.name or f"{r.from_table} -> {r.to_table}"
            findings.append(GovernanceFinding(
                area="broken_relationships",
                detail=f"Relationship '{rel_name}' references a table that does not exist in the model schema.",
                severity="Medium",
            ))

    # PBIC-GOV-009: Broken relationships referencing hidden columns
    for r in model.relationships:
        from_tbl = next((t for t in model.tables if t.name == r.from_table), None)
        to_tbl = next((t for t in model.tables if t.name == r.to_table), None)
        if from_tbl and to_tbl:
            from_col = next((c for c in from_tbl.columns if c.name == r.from_column), None)
            to_col = next((c for c in to_tbl.columns if c.name == r.to_column), None)
            if (from_col and from_col.is_hidden) or (to_col and to_col.is_hidden):
                findings.append(GovernanceFinding(
                    area="broken_rel_hidden_col",
                    detail=f"Relationship between '{r.from_table}' and '{r.to_table}' references a hidden column.",
                    severity="Low",
                ))

    filtered = []
    for f in findings:
        if process_finding(f, f.area):
            filtered.append(f)
    return filtered


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
    "hardcoded_year": ("Critical",
        "Some measures contain a hardcoded year value in their DAX.",
        "The affected measures stop reflecting current data at the next year boundary — the report silently shows stale results.",
        "Replace the literal year with dynamic logic such as YEAR(TODAY()) or a relative-period filter on the date table.",
        "Calculations stay correct every year without manual edits."),
    "deep_measure_chain": ("Low",
        "Deep measure dependency chains were found.",
        "Chains of measures referencing other measures deeper than 3 levels are fragile and hard to refactor/debug.",
        "Simplify the dependency chain or consolidate intermediate calculations.",
        "More maintainable measures that are easier to debug."),
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
    "disconnected_tables": ("High",
        "Some fact or dimension tables have no relationships to the rest of the model.",
        "A disconnected table won't filter or summarize with the rest of the report — any visual built from it shows unrelated or misleading totals.",
        "Add a relationship connecting the table to the model, or remove it if it's not needed.",
        "Every table in the model filters and aggregates correctly with the rest of the report."),
    "dev_leftover_naming": ("Medium",
        "Some table or column names look like development leftovers (e.g. 'test', 'tmp', 'Copy of ...').",
        "Development scaffolding that ships to production confuses report authors and signals the model wasn't cleaned up before handover.",
        "Rename to reflect the object's real content, or remove it if it was only ever a working copy.",
        "A production model that only contains intentional, clearly-named objects."),
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
    "hardcoded_paths": ("High",
        "Some data sources use a hardcoded local file path.",
        "Refresh fails as soon as the report runs on any machine or service other than the author's.",
        "Replace the literal path with a Power Query parameter, or move the source behind a gateway/shared location.",
        "Refresh works after deployment to the Power BI Service."),
}


# Estimated implementation effort per finding kind (defaults to "Medium").
# Kept separate from the prose templates so effort can be tuned without
# touching every tuple.
_EFFORT_BY_KIND = {
    # DAX findings
    "missing_description": "Low", "naming_issue": "Low", "repeated_pattern": "Low",
    "hardcoded_year": "Low", "deep_measure_chain": "Low",
    # best-practice checks
    "star_schema": "High", "fact_dimension_separation": "High",
    "naming_conventions": "Low", "display_folder_usage": "Low",
    "description_coverage": "Low", "hidden_technical_columns": "Low",
    "unused_calculated_columns": "Low", "inactive_relationships": "Low",
    "dev_leftover_naming": "Low",
    # performance risks
    "high_cardinality_signal": "Low", "large_text_column": "Low",
    "slow_slicer_signal": "Low",
    # governance
    "descriptions": "Low", "ownership": "Low", "hardcoded_paths": "Low",
}


def _recommendation(priority: str, issue: str, why: str, fix: str, benefit: str,
                    effort: str = "Medium", category: str = "modeling") -> Recommendation:
    return Recommendation(priority=priority, issue=issue, why_it_matters=why,
                          suggested_fix=fix, expected_benefit=benefit, effort=effort,
                          category=category)


def build_recommendations(
    dax_findings: list[DaxFinding],
    best_practices: list[BestPracticeCheck],
    performance_risks: list[PerformanceRisk],
    governance: list[GovernanceFinding],
    unused_assets: UnusedAssets,
    model: Optional[SemanticModel] = None,
) -> list[Recommendation]:
    """One templated recommendation per distinct finding kind actually present,
    sorted by priority. Templated prose, not LLM output — reproducible and
    testable."""
    recs: list[Recommendation] = []

    # dict.fromkeys() dedupes while preserving first-seen order
    seen_dax = dict.fromkeys(f.kind for f in dax_findings)
    for kind in seen_dax:
        if kind in _DAX_TEMPLATES:
            recs.append(_recommendation(*_DAX_TEMPLATES[kind],
                                        effort=_EFFORT_BY_KIND.get(kind, "Medium"), category="dax"))

    for check in best_practices:
        if not check.passed and check.id in _PRACTICE_TEMPLATES:
            recs.append(_recommendation(*_PRACTICE_TEMPLATES[check.id],
                                        effort=_EFFORT_BY_KIND.get(check.id, "Medium"), category="modeling"))

    seen_perf = dict.fromkeys(f.kind for f in performance_risks)
    for kind in seen_perf:
        if kind in _PERF_TEMPLATES:
            recs.append(_recommendation(*_PERF_TEMPLATES[kind],
                                        effort=_EFFORT_BY_KIND.get(kind, "Medium"), category="performance"))

    seen_gov = dict.fromkeys(f.area for f in governance)
    for area in seen_gov:
        if area in _GOVERNANCE_TEMPLATES:
            recs.append(_recommendation(*_GOVERNANCE_TEMPLATES[area],
                                        effort=_EFFORT_BY_KIND.get(area, "Medium"), category="governance"))

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
            effort="Low", category="unused_assets",
        ))

    # Add custom dynamic fix snippets if model is provided
    if model:
        for r in recs:
            # PBIC-DAX-006: Hardcoded year
            if "hardcoded year" in r.issue.lower():
                hardcoded_finds = [f for f in dax_findings if f.kind == "hardcoded_year"]
                if hardcoded_finds:
                    fix_lines = []
                    for f in hardcoded_finds:
                        m_obj = next((x for x in model.all_measures() if x.name == f.measure), None)
                        if m_obj and m_obj.expression:
                            fixed_expr = re.sub(r"\b20[12]\d\b", "YEAR(TODAY())", m_obj.expression)
                            fix_lines.append(f"// Fix for measure: {f.measure}\nmeasure {f.measure} =\n{fixed_expr}\n")
                    if fix_lines:
                        r.suggested_fix = r.suggested_fix + "\n\n```dax\n" + "\n".join(fix_lines) + "```"

            # PBIC-MOD-001: Bidirectional cross-filtering
            elif "bidirectional" in r.issue.lower():
                bidirectional_rels = [rel for rel in model.relationships if rel.cross_filter == "both"]
                if bidirectional_rels:
                    fix_lines = []
                    for rel in bidirectional_rels:
                        fix_lines.append(f"Model.Relationships.First(rel => rel.FromTable.Name == \"{rel.from_table}\" && rel.ToTable.Name == \"{rel.to_table}\").CrossFilteringBehavior = CrossFilteringBehavior.OneDirection;")
                    r.suggested_fix = r.suggested_fix + "\n\n```csharp\n// Tabular Editor C# Script to resolve bidirectional cross-filtering\n" + "\n".join(fix_lines) + "\n```"

            # PBIC-MOD-006: Hidden technical columns
            elif "id/key-style columns" in r.issue.lower():
                visible_keys = []
                for t in model.tables:
                    for col in t.columns:
                        if not col.is_hidden and (_ID_LIKE_NAME.search(col.name) or col.is_key):
                            visible_keys.append(f"{t.name}[{col.name}]")
                if visible_keys:
                    te_lines = []
                    for vk in visible_keys:
                        if "[" in vk and vk.endswith("]"):
                            t_name, c_name = vk.split("[", 1)
                            c_name = c_name[:-1]
                            te_lines.append(f"Model.Tables[\"{t_name}\"].Columns[\"{c_name}\"].IsHidden = true;")
                    r.suggested_fix = r.suggested_fix + "\n\n```csharp\n// Tabular Editor C# Script to hide key/ID columns\n" + "\n".join(te_lines) + "\n```"

            # Unused assets
            elif "unused asset" in r.issue.lower():
                m_to_tbl = {m.name: m.table for m in model.all_measures() if m.table}
                unused_lines = []
                for c in unused_assets.columns:
                    unused_lines.append(f"Model.Tables[\"{c['table']}\"].Columns[\"{c['column']}\"].Delete();")
                for c in unused_assets.calculated_columns:
                    unused_lines.append(f"Model.Tables[\"{c['table']}\"].Columns[\"{c['column']}\"].Delete();")
                for m in unused_assets.measures:
                    m_tbl = m_to_tbl.get(m, "Unassigned Measures")
                    unused_lines.append(f"Model.Tables[\"{m_tbl}\"].Measures[\"{m}\"].Delete();")
                if unused_lines:
                    r.suggested_fix = r.suggested_fix + "\n\n```csharp\n// Tabular Editor C# Script to delete unused assets\n" + "\n".join(unused_lines[:15]) + ("\n// ... and more" if len(unused_lines) > 15 else "") + "\n```"

    recs.sort(key=lambda r: _PRIORITY_ORDER.get(r.priority, 4))
    return recs


def get_and_update_score_history(report_name: str, current_score: int) -> Optional[str]:
    import os
    # Off by default (mirrors agents/cache.py's LLM cache): the hosted
    # service never sets this env var, so score-trend persistence stays
    # opt-in there per the zero-retention guarantee; the CLI turns it on by
    # setting a default path (see cli.py's "generate" command).
    db_path = os.environ.get("PBICOMPASS_SCORE_HISTORY") or "off"
    if db_path == "off":
        return None

    import json
    from pathlib import Path
    from datetime import datetime, timezone

    history_file = Path(db_path)
    history = {}
    if history_file.exists():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    runs = history.get(report_name, [])
    last_run = runs[-1] if runs else None
    
    now_str = datetime.now(timezone.utc).isoformat()
    runs.append({"overall": current_score, "timestamp": now_str})
    history[report_name] = runs
    
    try:
        history_file.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception:
        pass
        
    if last_run:
        try:
            dt = datetime.fromisoformat(last_run["timestamp"])
            date_str = dt.strftime("%d %b %Y")
        except Exception:
            date_str = last_run["timestamp"][:10]
            
        diff = current_score - last_run["overall"]
        diff_str = f"+{diff}" if diff >= 0 else str(diff)
        return f"{last_run['overall']} → {current_score} ({diff_str}) since {date_str}"
        
    return None
