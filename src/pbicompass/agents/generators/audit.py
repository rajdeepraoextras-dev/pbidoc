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
from ..context import JobAIContext
from ..critic import apply_critic_pass, apply_results
from ..grounding import apply_grounding_pass
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


def _narrative_triples(doc: AuditDocument) -> list[tuple[str, str, "callable"]]:
    """The audit doc's narrative fields as ``(location, text, setter)``
    triples — shared by the critic (5.3) and grounding (Phase 3) passes so
    neither re-derives the other's field list. Findings' ``detail`` text is
    deterministic-template fact, not free LLM prose — only
    ``narrative_overview`` and the recommendation write-ups go through
    either pass."""
    triples: list[tuple[str, str, "callable"]] = []

    def _set_narrative(v: str) -> None:
        doc.narrative_overview = v
    triples.append(("narrative_overview", doc.narrative_overview, _set_narrative))

    for i, rec in enumerate(doc.recommendations):
        def _set_why(v: str, _r=rec) -> None:
            _r.why_it_matters = v
        def _set_fix(v: str, _r=rec) -> None:
            _r.suggested_fix = v
        def _set_benefit(v: str, _r=rec) -> None:
            _r.expected_benefit = v
        triples.append((f"recommendations[{i}].why_it_matters", rec.why_it_matters, _set_why))
        triples.append((f"recommendations[{i}].suggested_fix", rec.suggested_fix, _set_fix))
        triples.append((f"recommendations[{i}].expected_benefit", rec.expected_benefit, _set_benefit))
    return triples


def _run_critic(doc: AuditDocument, model, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
    """5.3: one critic pass over the audit doc's narrative fields."""
    known_names = {t.name for t in model.tables}
    known_names |= {m.name for m in model.all_measures()}

    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_critic_pass(fields, client, known_names=known_names, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


def _run_grounding(doc: AuditDocument, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
    """Phase 3: one fact-verification call over the same narrative fields,
    run *after* the critic pass so it judges the already style-corrected
    text — re-collecting the triples here (rather than reusing the critic's
    own list) picks up ``doc``'s post-critic values for free, since
    ``apply_results`` already mutated it in place. Skipped when no shared
    ``ai_context``/digest is available (e.g. this generator called directly
    without one) — mirrors the DAX Translator/insights fallback elsewhere."""
    if ai_context is None or not ai_context.model_digest:
        return
    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_grounding_pass(fields, client, model_digest=ai_context.model_digest,
                                    warn=warn, ai_context=ai_context)
    apply_results(triples, results)


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
        ai_context: Optional[JobAIContext] = None,
    ) -> AuditDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()

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
        complexity = audit_rules.compute_complexity(model)
        recommendations = audit_rules.build_recommendations(
            dax_findings, best_practices, performance_risks, governance, unused_assets, model=model,
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
                io.AUDIT_NARRATOR_SCHEMA, warn, "Audit Narrator", ai_context=ai_context,
            )
            if data and data.get("narrative_overview"):
                narrative = data["narrative_overview"]

        suppressed = audit_rules.get_suppressed_rules()
        meta = build_core_metadata(
            model, "audit", default_audience="BI architects, technical leads, and governance teams",
            owner=owner, audience=audience, refresh=refresh, version=version, status=status,
        )
        meta.score_trend = audit_rules.get_and_update_score_history(
            model.report_name or "UnknownReport",
            health.overall
        )
        ledger = audit_rules.compute_checks_ledger(
            dax_findings, best_practices, performance_risks, governance, suppressed,
        )
        doc = AuditDocument(
            metadata=meta,
            health=health,
            complexity=complexity,
            dax_findings=dax_findings,
            best_practices=best_practices,
            performance_risks=performance_risks,
            governance=governance,
            unused_assets=unused_assets,
            recommendations=recommendations,
            narrative_overview=narrative,
            suppressed_rules=suppressed,
            checks_run=ledger["run"],
            checks_passed=ledger["passed"],
            checks_failed=ledger["failed"],
            checks_suppressed=ledger["suppressed"],
            checks_by_category=ledger["by_category"],
        )

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)
            _run_grounding(doc, client, warn, ai_context)

        return doc
