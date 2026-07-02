"""Audit & Health Report generator — ``SemanticModel`` -> ``AuditDocument``.

Everything except ``narrative_overview`` is fully deterministic
(:mod:`pbicompass.agents.audit_rules`) — no LLM call is on the critical path for
the score, complexity, findings, or recommendations. ``narrative_overview``
optionally goes through an LLM for polished prose, with a deterministic
templated-paragraph fallback so the document is always complete offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.audit_document import AuditDocument
from .. import audit_rules
from .. import io
from ..llm import LLMClient
from .base import Warn, build_core_metadata, call_llm


def _deterministic_overview(
    health, complexity, dax_findings, best_practices, performance_risks, governance, recommendations,
) -> str:
    failed = [c for c in best_practices if not c.passed]
    top = recommendations[0] if recommendations else None
    parts = [
        f"This model scores {health.overall}/100 overall ({health.band}), with "
        f"{complexity.level.lower()} structural complexity across "
        f"{complexity.table_count} tables, {complexity.measure_count} measures, and "
        f"{complexity.relationship_count} relationships."
    ]
    weakest = min(health.component_scores, key=lambda k: health.component_scores[k])
    strongest = max(health.component_scores, key=lambda k: health.component_scores[k])
    if health.component_scores[weakest] < health.component_scores[strongest]:
        parts.append(
            f"{weakest.replace('_', ' ').capitalize()} is the area holding the score back "
            f"({health.component_scores[weakest]}/100), while {strongest.replace('_', ' ')} "
            f"is the strongest area ({health.component_scores[strongest]}/100)."
        )
    counts = (f"{len(dax_findings)} DAX finding(s), {len(failed)} failed best-practice check(s), "
              f"{len(performance_risks)} performance risk signal(s), and {len(governance)} "
              f"governance finding(s) were identified.")
    parts.append(counts)
    if top:
        parts.append(f"The top priority is: {top.issue} ({top.priority}) — {top.suggested_fix}")
    return " ".join(parts)


class AuditReportGenerator:
    """Evaluates the model rather than describing it: health score,
    complexity, DAX review, best-practice checks, performance-risk signals,
    governance findings, unused assets, and prioritized recommendations."""

    @staticmethod
    def generate(
        model,
        client: Optional[LLMClient] = None,
        *,
        owner: Optional[str] = None,
        audience: Optional[str] = None,
        refresh: Optional[str] = None,
        version: Optional[str] = None,
        status: Optional[str] = None,
        classification: Optional[str] = None,
        on_warning: Optional[Warn] = None,
    ) -> AuditDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()

        measures = model.all_measures()
        dax_findings = audit_rules.find_dax_findings(measures)
        best_practices = audit_rules.check_best_practices(model)
        performance_risks = audit_rules.find_performance_risks(model)
        governance = audit_rules.check_governance(model, owner=owner, classification=classification)
        unused_assets = audit_rules.find_unused_assets(model)
        health = audit_rules.compute_health_score(
            dax_findings, best_practices, performance_risks, governance, unused_assets,
        )
        complexity = audit_rules.compute_complexity(model)
        recommendations = audit_rules.build_recommendations(
            dax_findings, best_practices, performance_risks, governance, unused_assets,
        )

        narrative = _deterministic_overview(
            health, complexity, dax_findings, best_practices, performance_risks, governance, recommendations,
        )
        if client is not None:
            failed = [c for c in best_practices if not c.passed]
            data = call_llm(
                client, io.AUDIT_NARRATOR_SYSTEM,
                io.audit_narrator_input(
                    health_overall=health.overall, health_band=health.band,
                    component_scores=health.component_scores, complexity_level=complexity.level,
                    dax_finding_count=len(dax_findings), failed_practice_count=len(failed),
                    performance_risk_count=len(performance_risks),
                    governance_finding_count=len(governance),
                    unused_asset_count=(
                        len(unused_assets.measures) + len(unused_assets.columns)
                        + len(unused_assets.tables) + len(unused_assets.calculated_columns)
                        + len(unused_assets.report_pages)
                    ),
                    top_recommendations=[
                        {"priority": r.priority, "issue": r.issue, "suggested_fix": r.suggested_fix}
                        for r in recommendations[:3]
                    ],
                ),
                io.AUDIT_NARRATOR_SCHEMA, warn, "Audit Narrator",
            )
            if data and data.get("narrative_overview"):
                narrative = data["narrative_overview"]

        return AuditDocument(
            metadata=build_core_metadata(
                model, "audit", default_audience="BI architects, technical leads, and governance teams",
                owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            ),
            health=health,
            complexity=complexity,
            dax_findings=dax_findings,
            best_practices=best_practices,
            performance_risks=performance_risks,
            governance=governance,
            unused_assets=unused_assets,
            recommendations=recommendations,
            narrative_overview=narrative,
        )
