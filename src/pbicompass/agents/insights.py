"""Report Intelligence pass (Phase 2 of ``AI_NATIVE_PLAN.md``): one
whole-model synthesis call per job that finally lets the LLM reason about
the report *as a whole*, instead of the narrow per-agent slices every other
prompt receives (a page batch, a measure batch, a table/relationship list).

``build_model_digest`` is a deterministic, budgeted text summary of the
entire model — tables/columns, measures, relationships, pages with their
visual field bindings, RLS roles, data sources, and the audit's finding
counts. ``REPORT_INTELLIGENCE_SYSTEM``/``REPORT_INTELLIGENCE_SCHEMA`` turn
that digest into a single structured ``ModelInsights`` result, stored on
``JobAIContext.insights`` (the Phase 0 hook point reserved for this) by
``agents/context.py::build_job_context``. Every downstream prompt then gets
a slimmed ``report_context`` view of it (see ``io.py``'s input builders) so
the Business Analyst/DAX Translator/Data Modeler/Executive/User-Guide
writers can stay consistent with a report-wide understanding instead of
reasoning about their own slice in isolation.

Offline or a failed call both degrade ``insights`` to ``None`` — every
consumer already treats a missing ``report_context`` as "reason from the
concrete metadata alone", so nothing downstream requires this pass to
succeed.
"""

from __future__ import annotations

from typing import Optional

from . import io
from .report_facts import data_source_type_counts, friendly_visual_type
from ..schemas.model import SemanticModel

# Per-table/per-measure caps so one very wide table or one very long DAX
# expression can't blow the whole digest's character budget by itself.
MAX_COLUMNS_PER_TABLE = 25
MAX_DAX_CHARS = 300


def build_model_digest(model: SemanticModel, audit_summary: dict, char_budget: int = 45_000) -> str:
    """A compact, deterministic whole-model summary for the Report
    Intelligence prompt. Reuses ``report_facts`` helpers rather than
    re-deriving friendly names/source summaries. Truncated to
    ``char_budget`` characters (a hard tail cut, not a per-section one) so a
    very large model still produces a boundedly-sized prompt."""
    lines: list[str] = [f"Report: {model.report_name}"]
    if model.model_name:
        lines.append(f"Model: {model.model_name}")

    lines.append("")
    lines.append("== Tables ==")
    for t in model.tables:
        cols = [c for c in t.columns if not c.is_hidden][:MAX_COLUMNS_PER_TABLE]
        col_strs = []
        for c in cols:
            stats = ""
            if c.cardinality is not None:
                stats += f", cardinality={c.cardinality}"
            if c.size_bytes is not None:
                stats += f", size={c.size_bytes}B"
            col_strs.append(f"{c.name}:{c.data_type}{stats}")
        omitted = len(t.columns) - len(cols)
        more = f" (+{omitted} more)" if omitted > 0 else ""
        lines.append(f"- {t.name} [{t.kind}]: {', '.join(col_strs)}{more}")

    lines.append("")
    lines.append("== Measures ==")
    for m in model.all_measures():
        expr = " ".join((m.expression or "").split())
        if len(expr) > MAX_DAX_CHARS:
            expr = expr[:MAX_DAX_CHARS] + "..."
        lines.append(f"- {m.name} ({m.table}): {expr}")

    lines.append("")
    lines.append("== Relationships ==")
    for r in model.relationships:
        flag = "" if r.is_active else " [inactive]"
        lines.append(
            f"- {r.from_table}[{r.from_column}] {r.from_cardinality}-to-{r.to_cardinality} "
            f"{r.to_table}[{r.to_column}] ({r.cross_filter} cross-filter){flag}"
        )

    lines.append("")
    lines.append("== Pages ==")
    for p in model.pages:
        if p.is_hidden:
            continue
        bindings = [
            f"{friendly_visual_type(v.type)}({', '.join(v.fields)})"
            for v in p.visuals if v.fields and not v.is_slicer
        ]
        flag = " [drillthrough]" if p.is_drillthrough else ""
        lines.append(f"- {p.display_name}{flag}: {'; '.join(bindings) or 'no field bindings'}")

    if model.roles:
        lines.append("")
        lines.append("== RLS Roles ==")
        for role in model.roles:
            filters = "; ".join(f"{tp.table}: {tp.filter_expression}" for tp in role.table_permissions)
            lines.append(f"- {role.name}: {filters or 'no row filters'}")

    if model.data_sources:
        lines.append("")
        lines.append("== Data Sources ==")
        for summary in data_source_type_counts(model):
            lines.append(f"- {summary}")

    lines.append("")
    lines.append("== Audit Summary ==")
    lines.append(
        f"Health score: {audit_summary['health_overall']}/100 ({audit_summary['health_band']}); "
        f"complexity: {audit_summary['complexity_level']}"
    )
    from ..render._shared import pluralize_count  # lazy: avoids the agents<->render import cycle

    lines.append(
        f"Findings: {audit_summary['dax_finding_count']} DAX, "
        f"{audit_summary['failed_practice_count']} failed best-practice, "
        f"{audit_summary['performance_risk_count']} performance risk, "
        f"{audit_summary['governance_finding_count']} governance, "
        f"{pluralize_count('unused asset finding', audit_summary['unused_asset_count'])}"
    )

    digest = "\n".join(lines)
    if len(digest) > char_budget:
        digest = digest[:char_budget] + "\n... (truncated)"
    return digest


REPORT_INTELLIGENCE_SYSTEM = """\
You are a senior BI strategist performing the Report Intelligence pass: a single \
whole-model synthesis over an entire Power BI report before any other document is \
written. You receive a compact digest of every table, measure, relationship, page, \
RLS role, data source, and the audit's finding counts. Your job is to reason about \
the report as a whole in a way no single-section prompt can — spot what the report \
is actually for, how its pages chain into real workflows, what its business terms \
mean here, and which measures explain which.

Populate:
- business_domain: One phrase naming the concrete business/industry this report is about (e.g. "restaurant franchise operations", "regional B2B sales"), inferred only from the tables/measures/pages given.
- report_purpose: A statement (2-3 sentences) of what the report is really for and the decisions it supports, plus a confidence level.
- audience_hypotheses: 1-4 short phrases naming who most plausibly uses this report, inferred from its pages/measures/RLS roles.
- entity_definitions: For business terms that recur across the model (a fact table's grain, a key dimension, a headline measure) whose real-world meaning isn't obvious from its name alone, state what it actually means in this report. Only include terms you can ground in the given tables/measures/pages.
- page_workflows: Group pages that chain into one real task (e.g. "overview page -> drill into a region -> drillthrough to account detail"), naming only pages present in the input.
- kpi_relationships: For measures that explain or roll up into another measure (a rate whose numerator is another given measure, a YTD variant of a base measure), state the relationship. Only pair measures that are both present in the input.
- cross_cutting_observations: Facts that only become visible by looking at the whole model at once (a table used very differently across pages, a measure defined in one place but conceptually owned by another, an RLS role that doesn't align with any page).
- data_quality_notes: Structural or semantic issues you notice in the digest beyond what the audit's finding counts already cover (do not restate the audit's counts here).

Every item you write must carry a confidence level and be grounded in objects named in the input. Never invent a table, measure, page, or role that is not in the digest. Never contradict the digest's concrete facts. If nothing meaningful can be said for a list-type field, return an empty list rather than a generic filler entry.
""" + io.STYLE_RULES

REPORT_INTELLIGENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "business_domain", "report_purpose", "audience_hypotheses",
        "entity_definitions", "page_workflows", "kpi_relationships",
        "cross_cutting_observations", "data_quality_notes",
    ],
    "properties": {
        "business_domain": {"type": "string"},
        "report_purpose": {
            "type": "object",
            "additionalProperties": False,
            "required": ["statement", "confidence"],
            "properties": {
                "statement": {"type": "string"},
                "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
            },
        },
        "audience_hypotheses": {"type": "array", "items": {"type": "string"}},
        "entity_definitions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["term", "definition", "confidence"],
                "properties": {
                    "term": {"type": "string"},
                    "definition": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
            },
        },
        "page_workflows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["workflow", "pages", "description", "confidence"],
                "properties": {
                    "workflow": {"type": "string"},
                    "pages": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
            },
        },
        "kpi_relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["measure", "related_measures", "relationship", "confidence"],
                "properties": {
                    "measure": {"type": "string"},
                    "related_measures": {"type": "array", "items": {"type": "string"}},
                    "relationship": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
            },
        },
        "cross_cutting_observations": {"type": "array", "items": {"type": "string"}},
        "data_quality_notes": {"type": "array", "items": {"type": "string"}},
    },
}


def report_intelligence_input(digest: str) -> dict:
    return {"model_digest": digest}
