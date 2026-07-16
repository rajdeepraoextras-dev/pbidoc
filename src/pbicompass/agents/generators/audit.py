"""Audit & Health Report generator — ``SemanticModel`` -> ``AuditDocument``.

The score, complexity, and findings are fully deterministic
(:mod:`pbicompass.agents.audit_rules`) — no LLM call is on their critical path.
``narrative_overview``, the root-cause ``clusters``/``strategic_narrative``
(Day 7), and a bounded top-N of ``recommendations[].suggested_fix`` (Day 9,
paid — an appended AI-suggested code sketch) optionally go through an LLM,
each with a deterministic/prose-only fallback so the document is always
complete offline.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from ...schemas.audit_document import AuditDocument, FindingCluster
from .. import audit_rules
from .. import consistency
from .. import io
from .. import traceability
from ..context import JobAIContext
from ..critic import apply_critic_pass, apply_results
from ..grounding import apply_grounding_pass
from ..llm import LLMClient
from ..sanitize import enforce_score_consistency, is_meta_commentary, sanitize_narratives
from .base import Warn, build_core_metadata, call_llm

# Day 9 (paid feature, §4.3/4.6): the plan tiers allowed to receive
# AI-suggested fix snippets — matches accounts.py::PLAN_LIMITS' free/pro/
# enterprise vocabulary. A caller with no account concept (the CLI) passes
# ``plan=None``/omits it, which stays out of this set on purpose — an
# explicit ``--plan`` opts a self-hosted run in.
_AI_FIX_SNIPPET_PLANS = {"pro", "enterprise"}
# Bounded regardless of the owner's token-cost-is-not-a-concern policy
# (§4.0) — "top-N" per the roadmap, so one job never fans out one LLM call
# per recommendation.
_AI_FIX_SNIPPET_TOP_N = 3
_PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def _recommendation_example_objects(rec, dax_findings, performance_risks) -> list[str]:
    """Real object names from the underlying findings that share this
    recommendation's ``rule_id`` — given to the AI Fix Snippet Writer so it
    references an actual measure/column instead of inventing one, and kept
    empty (never guessed) when the recommendation has no per-object finding
    behind it (e.g. governance/modeling findings are model-wide)."""
    if not rec.rule_id:
        return []
    names: list[str] = []
    for f in dax_findings:
        if f.rule_id == rec.rule_id and f.measure not in names:
            names.append(f.measure)
    for r in performance_risks:
        if r.rule_id == rec.rule_id and r.object_name not in names:
            names.append(r.object_name)
    return names[:3]


def _apply_ai_fix_snippets(
    recommendations, dax_findings, performance_risks, client, warn: Warn,
    ai_context: Optional[JobAIContext], plan: Optional[str],
) -> None:
    """Day 9: append an "AI-suggested — review before applying" DAX/M/script
    sketch to the top-N prose-only recommendations (Critical/High first) —
    skipped entirely offline, and plan-gated to pro/enterprise so the free
    tier never silently gets a lesser version of this feature (it gets none,
    per the roadmap's paid-feature framing). Recommendations that already
    carry a deterministic code fence (``build_recommendations``'s own
    Tabular Editor/M scripts) are left untouched rather than doubled up."""
    if client is None or plan not in _AI_FIX_SNIPPET_PLANS:
        return
    candidates = sorted(
        (r for r in recommendations if "```" not in r.suggested_fix),
        key=lambda r: _PRIORITY_ORDER.get(r.priority, 99),
    )[:_AI_FIX_SNIPPET_TOP_N]
    if not candidates:
        return

    items = [
        {
            "rule_id": r.rule_id or f"rec-{i}",
            "issue": r.issue,
            "why_it_matters": r.why_it_matters,
            "category": r.category,
            "current_suggested_fix": r.suggested_fix,
            "example_objects": _recommendation_example_objects(r, dax_findings, performance_risks),
        }
        for i, r in enumerate(candidates)
    ]
    # A candidate lacking a real rule_id got a synthetic "rec-{i}" key above
    # purely so the schema always has something to echo back — match
    # candidates back up positionally for those, by rule_id for the rest.
    by_key = {item["rule_id"]: rec for item, rec in zip(items, candidates)}

    data = call_llm(
        client, io.AI_FIX_SNIPPET_SYSTEM, io.ai_fix_snippet_input(items),
        io.AI_FIX_SNIPPET_SCHEMA, warn, "AI Fix Snippet Writer", ai_context=ai_context,
    )
    if not data:
        return

    for snippet in data.get("snippets", []):
        rec = by_key.get((snippet.get("rule_id") or "").strip())
        code = (snippet.get("code") or "").strip()
        if rec is None or not code or is_meta_commentary(code):
            continue
        lang = snippet.get("language") or "text"
        if lang not in ("dax", "m", "csharp", "text"):
            lang = "text"
        rec.suggested_fix = (
            f"{rec.suggested_fix.rstrip()}\n\n"
            f"**AI-suggested — review before applying:**\n```{lang}\n{code}\n```"
        )


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
    from ...render._shared import pluralize_count  # lazy: avoids the agents<->render import cycle

    counts = (f"{pluralize_count('DAX finding', len(dax_findings))}, "
              f"{pluralize_count('failed best-practice check', len(failed))}, "
              f"{pluralize_count('performance risk signal', len(performance_risks))}, and "
              f"{pluralize_count('governance finding', len(governance))} were identified.")
    parts.append(counts)
    if top:
        parts.append(f"The top priority is: {top.issue} ({top.priority}) — {top.suggested_fix}")
    return " ".join(parts)


def _cluster_fallback_narrative(cluster: FindingCluster) -> str:
    """Deterministic backstop for a cluster's root-cause explanation (P0) —
    used only when :func:`sanitize.strip_punt_leak` guts the LLM-written
    ``narrative`` down to nothing (the grounding pass replacing more than
    one claim in the field with the same canned punt sentence). No prose
    subtlety, just the facts: what the root cause is and which findings
    it's tied to — always available since ``root_cause``/``rule_ids`` come
    straight from the Audit Synthesizer's own structured output, not prose."""
    ids = ", ".join(cluster.rule_ids) if cluster.rule_ids else "the related findings"
    return f"{cluster.root_cause} is the shared root cause behind {ids}. Resolving it should clear all of them together."


def _strategic_narrative_fallback(clusters: list[FindingCluster]) -> str:
    """Deterministic backstop for the overall root-cause story (P0) — same
    reasoning as :func:`_cluster_fallback_narrative`, one level up."""
    if not clusters:
        return ""
    if len(clusters) == 1:
        return f"One root-cause cluster was identified: {clusters[0].root_cause}."
    causes = "; ".join(c.root_cause for c in clusters)
    return f"{len(clusters)} root-cause clusters were identified: {causes}."


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

    def _set_strategic(v: str) -> None:
        doc.strategic_narrative = v
    triples.append(("strategic_narrative", doc.strategic_narrative, _set_strategic))

    for i, cluster in enumerate(doc.clusters):
        def _set_cluster_narrative(v: str, _c=cluster) -> None:
            _c.narrative = v
        triples.append((f"clusters[{i}].narrative", cluster.narrative, _set_cluster_narrative))

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
        author: Optional[str] = None,
        reviewer: Optional[str] = None,
        business_decision: Optional[str] = None,
        requirements: Optional[str] = None,
        security_notes: Optional[str] = None,
        refresh_notes: Optional[str] = None,
        deployment_notes: Optional[str] = None,
        access_notes: Optional[str] = None,
        glossary: Optional[str] = None,
        assumptions: Optional[str] = None,
        support_notes: Optional[str] = None,
        on_warning: Optional[Warn] = None,
        ai_context: Optional[JobAIContext] = None,
        plan: Optional[str] = None,
        requirements_matrix: Optional[list] = None,
    ) -> AuditDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()
        if requirements_matrix is None:
            requirements_matrix = traceability.build_requirements_matrix(
                model, requirements, client, warn, ai_context=ai_context,
                business_decision=business_decision, target_audience=audience,
                assumptions=assumptions, security_notes=security_notes,
                refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                access_notes=access_notes, support_notes=support_notes,
            )

        audit_rules.reset_suppressed_rules()
        measures = model.all_measures()
        dax_findings = audit_rules.find_dax_findings(measures)
        best_practices = audit_rules.check_best_practices(model)
        performance_risks = audit_rules.find_performance_risks(model)
        governance = audit_rules.check_governance(model, owner=owner, classification=classification,
                                                   security_notes=security_notes)
        unused_assets = audit_rules.find_unused_assets(model)

        # Day 3: a documented business assumption ("returns lag source by
        # 1 day") that happens to explain a finding is noted on that finding
        # directly — a reviewer sees it's already accounted for, rather than
        # reading it as an unexplained gap. Applied before scoring/ledger,
        # though neither reads .detail, only .severity/.passed/.rule_id.
        consistency.annotate_findings_with_assumptions(
            dax_findings + performance_risks + governance
            + [c for c in best_practices if not c.passed],
            assumptions,
        )

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
                    business_decision=business_decision, target_audience=audience,
                    assumptions=assumptions, security_notes=security_notes,
                    refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                    access_notes=access_notes, support_notes=support_notes,
                ),
                io.AUDIT_NARRATOR_SCHEMA, warn, "Audit Narrator", ai_context=ai_context,
            )
            if data and data.get("narrative_overview"):
                narrative = data["narrative_overview"]

        clusters: list[FindingCluster] = []
        strategic_narrative = ""
        if client is not None:
            failed_practices = [c for c in best_practices if not c.passed]
            synth_data = call_llm(
                client, io.AUDIT_SYNTHESIZER_SYSTEM,
                io.audit_synthesizer_input(
                    dax_findings=[
                        {"rule_id": f.rule_id, "measure": f.measure, "table": f.table, "detail": f.detail}
                        for f in dax_findings
                    ],
                    failed_best_practices=[
                        {"rule_id": c.rule_id, "id": c.id, "name": c.name, "detail": c.detail}
                        for c in failed_practices
                    ],
                    performance_risks=[
                        {"rule_id": r.rule_id, "kind": r.kind, "object_name": r.object_name,
                         "table": r.table, "detail": r.detail}
                        for r in performance_risks
                    ],
                    governance=[
                        {"rule_id": g.rule_id, "area": g.area, "detail": g.detail}
                        for g in governance
                    ],
                    unused_assets_summary={
                        "measures": unused_assets.measures,
                        "columns": unused_assets.columns,
                        "tables": unused_assets.tables,
                        "calculated_columns": unused_assets.calculated_columns,
                        "report_pages": unused_assets.report_pages,
                    },
                    business_decision=business_decision, target_audience=audience,
                    assumptions=assumptions, security_notes=security_notes,
                    refresh_notes=refresh_notes, deployment_notes=deployment_notes,
                    access_notes=access_notes, support_notes=support_notes,
                ),
                io.AUDIT_SYNTHESIZER_SCHEMA, warn, "Audit Synthesizer", ai_context=ai_context,
            )
            if synth_data:
                clusters = [
                    FindingCluster(
                        root_cause=c.get("root_cause", ""),
                        # Deduped, order-preserved: the LLM occasionally
                        # repeats a rule_id within one cluster, which would
                        # otherwise render the same "Related findings" link
                        # twice (Day 2).
                        rule_ids=list(dict.fromkeys(c.get("rule_ids", []))),
                        narrative=c.get("narrative", ""),
                        confidence=c.get("confidence", "Medium"),
                    )
                    for c in synth_data.get("clusters", [])
                ]
                strategic_narrative = synth_data.get("strategic_narrative", "")

        # Day 9: runs last, after the deterministic overview and the Audit
        # Narrator/Synthesizer calls have already read ``recommendations``
        # — so neither the narrative prose nor the narrator's own input
        # payload ever picks up an appended code fence meant only for the
        # recommendation card itself.
        _apply_ai_fix_snippets(recommendations, dax_findings, performance_risks, client, warn, ai_context, plan)

        suppressed = audit_rules.get_suppressed_rules()
        meta = build_core_metadata(
            model, "audit", default_audience="BI architects, technical leads, and governance teams",
            owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            author=author, reviewer=reviewer, classification=classification,
            business_decision=business_decision, requirements=requirements,
            security_notes=security_notes, refresh_notes=refresh_notes,
            deployment_notes=deployment_notes, access_notes=access_notes,
            glossary=glossary, assumptions=assumptions, support_notes=support_notes,
        )
        meta.score_trend = audit_rules.get_shared_score_trend(
            ai_context, model.report_name or "UnknownReport",
            health.overall
        )
        ledger = audit_rules.compute_checks_ledger(
            dax_findings, best_practices, performance_risks, governance, suppressed,
        )
        if ai_context is not None:
            ai_context.checks_ledger = ledger
        discrepancies = consistency.find_human_claim_discrepancies(
            security_notes, len(model.roles),
            rls_readable=(getattr(model.meta, "source_format", "") or "").lower() != "pbix",
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
            clusters=clusters,
            strategic_narrative=strategic_narrative,
            checks_run=ledger["run"],
            checks_passed=ledger["passed"],
            checks_failed=ledger["failed"],
            checks_suppressed=ledger["suppressed"],
            checks_by_category=ledger["by_category"],
            discrepancies=[dataclasses.asdict(d) for d in discrepancies],
            requirements_gaps=[dataclasses.asdict(r) for r in requirements_matrix if r.status == "Gap"],
        )

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)
            _run_grounding(doc, client, warn, ai_context)

        # P0: a narrative sentence claiming "the health score" must agree
        # with the one deterministic number this document actually
        # reports (its own KPI strip) — an LLM narrator can misstate it
        # even when given the correct value verbatim in its prompt.
        # Unconditional for the same reason as the punt-leak strip below.
        doc.narrative_overview = enforce_score_consistency(doc.narrative_overview, health.overall, health.band)

        # P0: the one gate every narrative field passes through — see
        # sanitize.sanitize_narratives's own docstring for why this must
        # be centralized rather than each generator inlining its own strip
        # (a leak that survived this exact per-generator approach in
        # audit.py alone is what motivated pulling it out). Unconditional
        # (not gated on ``client``): a field's *initial* draft can itself
        # carry the leak before critic/grounding ever touch it.
        fallbacks = {"narrative_overview": narrative}
        if doc.strategic_narrative:
            fallbacks["strategic_narrative"] = _strategic_narrative_fallback(doc.clusters)
        for i, cluster in enumerate(doc.clusters):
            fallbacks[f"clusters[{i}].narrative"] = _cluster_fallback_narrative(cluster)
        sanitize_narratives(_narrative_triples(doc), fallbacks)

        return doc
