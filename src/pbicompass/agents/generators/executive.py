"""Executive Summary generator — ``SemanticModel`` -> ``ExecutiveDocument``.

Reuses deterministic building blocks already computed elsewhere in the
pipeline (model statistics, schema shape, data-source summaries, and the
full audit rule engine for the maintenance note) rather than re-deriving
them — the "Knowledge Generation Layer" fanning out from one parsed model.
Known risks and future recommendations are deliberately *not* pulled
verbatim from the technical/audit text: that prose is written for BI
developers (DAX, USERELATIONSHIP(), CROSSFILTER()) and would violate this
document's "no implementation details" requirement, so they're re-framed in
business language from the same underlying facts instead. Only
``business_purpose``, ``business_value``, and ``maintenance_overview``
optionally go through an LLM for polished prose, with deterministic
templated fallbacks so the document is always complete offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.executive_document import ExecutiveDocument
from .. import audit_rules
from .. import io
from ..deterministic import business_analyst_deterministic, schema_shape
from ..llm import LLMClient
from ..report_facts import data_source_summaries
from .base import Warn, build_core_metadata, call_llm


def _deterministic_business_purpose(model) -> str:
    # Reuses the same deterministic narrative the technical document's
    # Executive Summary section falls back to — already concise (2-3
    # sentences) and free of table/DAX jargon.
    return business_analyst_deterministic(model).core_purpose


def _business_risk_summaries(model) -> list[str]:
    """Business-framed risk summaries — same underlying facts as the
    technical document's Data Model risks, but phrased with no DAX/
    implementation detail (no USERELATIONSHIP(), no bracket notation), since
    the executive document explicitly excludes implementation detail."""
    risks: list[str] = []
    bidirectional = [r for r in model.relationships if r.cross_filter == "both"]
    if bidirectional:
        risks.append(
            f"{len(bidirectional)} relationship(s) use two-way filtering, which can slow down "
            f"the report and produce ambiguous results if not carefully managed."
        )
    inactive = [r for r in model.relationships if not r.is_active]
    if inactive:
        risks.append(
            f"{len(inactive)} relationship(s) are inactive by default and only apply in specific "
            f"calculations — a nuance worth documenting for whoever maintains this report."
        )
    related = {r.from_table for r in model.relationships} | {r.to_table for r in model.relationships}
    disconnected = [t.name for t in model.tables if t.kind in ("fact", "dimension") and t.name not in related]
    if disconnected:
        risks.append(
            f"{len(disconnected)} table(s) are not connected to the rest of the model and may not "
            f"filter or summarize as expected: {', '.join(disconnected)}."
        )
    return risks


def _report_statistics(model) -> dict[str, int]:
    visible_pages = [p for p in model.pages if not p.is_hidden]
    hidden_pages = [p for p in model.pages if p.is_hidden]
    drillthrough_pages = [p for p in model.pages if p.is_drillthrough]
    return {
        "pages": len(model.pages),
        "visible_pages": len(visible_pages),
        "hidden_pages": len(hidden_pages),
        "drillthrough_pages": len(drillthrough_pages),
        "visuals": sum(len(p.visuals) for p in model.pages),
    }


def _security_overview(model) -> str:
    if not model.roles:
        return "No row-level security is configured; every user with report access sees the same data."
    names = ", ".join(r.name for r in model.roles)
    return f"{len(model.roles)} row-level security role(s) restrict access to this report: {names}."


def _architecture_overview(model) -> str:
    shape, facts, dims = schema_shape(model)
    return (f"The data model uses {shape}, integrating {len(facts)} fact table(s) and "
            f"{len(dims)} dimension table(s) via {len(model.relationships)} relationship(s).")


def _dependencies(model) -> list[str]:
    deps = list(data_source_summaries(model))
    deps += [f"Parameter: {e.name}" for e in model.expressions if e.kind == "parameter"]
    return deps


def _business_value(key_kpis: list[str], audience: Optional[str]) -> str:
    who = audience or "stakeholders"
    metrics = ", ".join(key_kpis[:3]) if key_kpis else "key metrics"
    return (f"This report gives {who} direct visibility into {metrics}, reducing reliance on "
            f"manual reporting and supporting faster, data-driven decisions.")


def _maintenance_overview(
    refresh: Optional[str], owner: Optional[str], failed_practice_count: int, governance_count: int,
) -> str:
    parts = [f"Refresh schedule: {refresh}." if refresh else "No refresh schedule has been documented yet."]
    if failed_practice_count or governance_count:
        parts.append(
            f"{failed_practice_count} modeling best-practice gap(s) and {governance_count} governance "
            f"finding(s) from the latest audit should be reviewed periodically."
        )
    else:
        parts.append("No outstanding modeling or governance gaps were found in the latest audit.")
    parts.append(f"Owner of record: {owner}." if owner else "No owner has been assigned for ongoing accountability.")
    return " ".join(parts)


def _future_recommendations(recommendations) -> list[str]:
    # issue + expected_benefit only — the audit's suggested_fix text is
    # written for BI developers (VAR, CROSSFILTER(), DAX-level detail) and
    # doesn't belong in a document that explicitly excludes implementation
    # detail.
    return [f"{r.issue} {r.expected_benefit}" for r in recommendations[:3]]


class ExecutiveSummaryGenerator:
    """Assembles a concise, non-technical summary for managers, executives,
    and project owners — readable in under ten minutes, no implementation
    details."""

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
    ) -> ExecutiveDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()

        key_measures = [m.name for m in model.all_measures() if not m.is_hidden][:5]
        model_risks = _business_risk_summaries(model)

        # Reuse the full deterministic audit engine (Phase 1) for the
        # maintenance note and recommendations, rather than re-deriving
        # best-practice/governance logic here.
        measures = model.all_measures()
        dax_findings = audit_rules.find_dax_findings(measures)
        best_practices = audit_rules.check_best_practices(model)
        performance_risks = audit_rules.find_performance_risks(model)
        governance = audit_rules.check_governance(model, owner=owner, classification=classification)
        unused_assets = audit_rules.find_unused_assets(model)
        recommendations = audit_rules.build_recommendations(
            dax_findings, best_practices, performance_risks, governance, unused_assets,
        )
        failed_practice_count = sum(1 for c in best_practices if not c.passed)

        business_purpose = _deterministic_business_purpose(model)
        business_value = _business_value(key_measures, audience)
        maintenance_overview = _maintenance_overview(
            refresh, owner, failed_practice_count, len(governance),
        )

        if client is not None:
            data = call_llm(
                client, io.EXECUTIVE_WRITER_SYSTEM,
                io.executive_writer_input(
                    business_purpose_draft=business_purpose,
                    key_kpis=key_measures,
                    model_statistics=dict(model.meta.counts),
                    report_statistics=_report_statistics(model),
                    known_risks=model_risks,
                    maintenance_draft=maintenance_overview,
                ),
                io.EXECUTIVE_WRITER_SCHEMA, warn, "Executive Writer",
            )
            if data:
                business_purpose = data.get("business_purpose") or business_purpose
                business_value = data.get("business_value") or business_value
                maintenance_overview = data.get("maintenance_overview") or maintenance_overview

        return ExecutiveDocument(
            metadata=build_core_metadata(
                model, "executive", default_audience="Managers, executives, and project owners",
                owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            ),
            business_purpose=business_purpose,
            key_kpis=key_measures,
            data_sources_summary=data_source_summaries(model),
            refresh_schedule=refresh,
            security_overview=_security_overview(model),
            architecture_overview=_architecture_overview(model),
            model_statistics=dict(model.meta.counts),
            report_statistics=_report_statistics(model),
            business_value=business_value,
            known_risks=model_risks,
            dependencies=_dependencies(model),
            maintenance_overview=maintenance_overview,
            future_recommendations=_future_recommendations(recommendations),
        )
