"""Shared per-job AI context (Phase 0 of ``AI_NATIVE_PLAN.md``).

Before this module, ``technical.py::_measure_catalog``,
``executive.py::_key_kpis``, and ``user_guide.py::_build_glossary`` each ran
the DAX Translator over every measure independently — up to 3x redundant
spend in a ``--document all`` job. ``build_job_context`` now runs it once;
every generator's ``generate(...)`` takes an optional ``ai_context`` kwarg
so both entry points (``cli.py``, ``service/worker.py``) can build it once
before their doc-type loop and hand the same instance to each generator.

``ai_context=None`` is always a fully-supported input: a generator called
directly (as every existing test does) builds its own context on demand,
so direct-import callers keep working unchanged — the shared-context path is
purely an optimization for multi-document jobs, never a requirement.

Phase 2 adds the Report Intelligence pass here too: one whole-model
synthesis call, run before the DAX Translator batches (so the translator
itself can consume the resulting ``report_context``), with its result
stashed on ``JobAIContext.insights`` — the field Phase 0 reserved for this.
The deterministic digest that call is built from is stashed separately on
``JobAIContext.model_digest`` regardless of whether the synthesis call
itself succeeds — Phase 3's grounding pass (``agents/grounding.py``) needs
that same digest as its own verification ground truth, and digest-building
has no LLM involved, so there is no reason to couple its availability to
the Report Intelligence call's success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import io
from .llm import LLMClient
from ..schemas.model import SemanticModel

Warn = Callable[[str], None]


@dataclass
class JobAIContext:
    """Content-free: holds derived AI results and call telemetry, never raw
    report metadata beyond what the translations/insights already surface."""

    # Measure name -> DAX Translator result (plain_english/calculation_logic/
    # caveats/category/confidence). ``None`` means either offline or the
    # translator produced nothing usable — callers fall back per-measure.
    translations: Optional[dict[str, dict]] = None

    # Populated by Phase 2's report-intelligence pass; ``None`` until then.
    insights: Optional[dict] = None

    # The deterministic whole-model digest the Report Intelligence call was
    # built from (Phase 2) — kept even when that call itself fails/is
    # offline, since Phase 3's grounding pass reuses it as pure ground-truth
    # text and needs no LLM to have succeeded for it to exist.
    model_digest: Optional[str] = None

    # Rule-engine ledger (4.1 / J.A.1) — ``audit_rules.compute_checks_ledger``'s
    # result, stashed here by whichever document generator computes it first
    # (currently the Audit & Health Report) so a sibling document generated
    # in the same job (technical.py's §16) reuses the identical counts
    # instead of re-deriving them a different way and disagreeing.
    checks_ledger: Optional[dict] = None

    # Score-trend string (4.5) — ``audit_rules.get_and_update_score_history``
    # both reads *and appends to* the on-disk history file, so calling it
    # more than once per job for the same report double-writes the run and
    # makes the second call compare its score against the first call's
    # freshly-written entry from the same run. ``get_shared_score_trend``
    # computes it once per job and caches the result here (including a
    # legitimate ``None`` — history off, or no prior run) so every document
    # generator in a multi-doc job (audit/technical/executive all render a
    # score trend) reuses the same value. ``_trend_set`` distinguishes "not
    # computed yet" from "computed, and the answer was None".
    score_trend: Optional[str] = None
    _score_trend_set: bool = False

    # Job-sandbox-scoped LLM response cache path (service only); ``None``
    # means "use the client-wide default" (``LLMResponseCache``'s own
    # ``PBICOMPASS_LLM_CACHE`` env-var lookup, e.g. the CLI's persistent
    # cache). Passed explicitly rather than via env var so concurrent jobs
    # in the same worker process never race on a shared environment
    # variable.
    cache_path: Optional[str] = None

    # Per-agent call/token counters — content-free (names and integers only).
    usage: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, agent: str, *, calls: int = 1, input_tokens: int = 0, output_tokens: int = 0) -> None:
        bucket = self.usage.setdefault(agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        bucket["calls"] += calls
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens


def _compute_audit_summary(model: SemanticModel) -> dict:
    """Deterministic finding-count summary for the Report Intelligence
    digest (``insights.build_model_digest``) — the same rule engine every
    document generator already runs independently (audit.py, technical.py's
    ``_health_and_recommendations``, executive.py), computed here with no
    owner/classification context since this pass runs before either is
    resolved by the caller; close enough for a reasoning aid that is never
    rendered to a reader verbatim."""
    from . import audit_rules

    audit_rules.reset_suppressed_rules()
    measures = model.all_measures()
    dax_findings = audit_rules.find_dax_findings(measures)
    best_practices = audit_rules.check_best_practices(model)
    performance_risks = audit_rules.find_performance_risks(model)
    governance = audit_rules.check_governance(model)
    unused_assets = audit_rules.find_unused_assets(model)
    health = audit_rules.compute_health_score(
        dax_findings, best_practices, performance_risks, governance, unused_assets,
    )
    complexity = audit_rules.compute_complexity(model)
    return {
        "health_overall": health.overall,
        "health_band": health.band,
        "complexity_level": complexity.level,
        "dax_finding_count": len(dax_findings),
        "failed_practice_count": sum(1 for c in best_practices if not c.passed),
        "performance_risk_count": len(performance_risks),
        "governance_finding_count": len(governance),
        "unused_asset_count": (
            len(unused_assets.measures) + len(unused_assets.columns)
            + len(unused_assets.tables) + len(unused_assets.calculated_columns)
            + len(unused_assets.report_pages)
        ),
    }


def _build_insights(
    model: SemanticModel, client: LLMClient, warn: Warn, ctx: "JobAIContext", digest: str,
) -> Optional[dict]:
    """Phase 2: the one whole-model Report Intelligence call, over the
    ``digest`` the caller already built. ``None`` on offline/failure — every
    downstream ``report_context`` consumer already treats a missing value as
    "reason from the concrete metadata alone"."""
    from . import insights
    from .generators.base import call_llm

    return call_llm(
        client, insights.REPORT_INTELLIGENCE_SYSTEM,
        insights.report_intelligence_input(digest),
        insights.REPORT_INTELLIGENCE_SCHEMA, warn, "Report Intelligence", ai_context=ctx,
    )


def build_job_context(
    model: SemanticModel,
    client: Optional[LLMClient],
    warn: Warn,
    *,
    cache_path: Optional[str] = None,
    business_decision: Optional[str] = None,
    target_audience: Optional[str] = None,
    assumptions: Optional[str] = None,
    security_notes: Optional[str] = None,
    refresh_notes: Optional[str] = None,
    deployment_notes: Optional[str] = None,
    access_notes: Optional[str] = None,
    support_notes: Optional[str] = None,
) -> JobAIContext:
    """Run the Report Intelligence pass and the DAX Translator once for the
    whole job and stash both results. Offline (``client is None``) or a
    fully failed pass degrade ``insights``/``translations`` to ``None`` —
    every consumer already has a deterministic (or context-free) fallback
    for that case.

    Insights are built *before* the DAX Translator batches so the
    translator's own prompt can consume the resulting ``report_context``
    (Phase 2) — the one deliberate ordering dependency in this function.

    The intake-form fields (``business_decision`` through ``support_notes``)
    are forwarded into the DAX Translator's own prompt as ``human_context``
    (the same channel the per-document narrative agents use) — job-shared,
    so every document type's measure translations benefit from the same
    human-supplied assumptions/context, not just whichever document type
    happens to build this job context first.
    """
    # Local import: ``generators.base`` needs ``io.AGENT_EFFORT``, and
    # ``generators/__init__.py`` (via audit/executive/technical/user_guide,
    # each needing ``JobAIContext`` for their ``generate(...)`` signature)
    # imports this module — a module-level import here would cycle back
    # into a not-yet-defined ``JobAIContext``.
    from . import insights as insights_mod
    from .generators.base import call_llm

    ctx = JobAIContext(cache_path=cache_path)
    if client is None:
        return ctx

    ctx.model_digest = insights_mod.build_model_digest(model, _compute_audit_summary(model))
    ctx.insights = _build_insights(model, client, warn, ctx, ctx.model_digest)

    merged: dict[str, dict] = {}
    for batch in io.dax_translator_batches(
        model, report_context=ctx.insights,
        business_decision=business_decision, target_audience=target_audience,
        assumptions=assumptions, security_notes=security_notes,
        refresh_notes=refresh_notes, deployment_notes=deployment_notes,
        access_notes=access_notes, support_notes=support_notes,
    ):
        data = call_llm(
            client, io.DAX_TRANSLATOR_SYSTEM, batch, io.DAX_TRANSLATOR_SCHEMA,
            warn, "DAX Translator", ai_context=ctx,
        )
        if data:
            merged.update({t["name"]: t for t in data.get("translations", [])})
    ctx.translations = merged or None
    return ctx
