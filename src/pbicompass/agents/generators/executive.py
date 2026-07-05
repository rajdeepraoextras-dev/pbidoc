"""Executive Summary generator — ``SemanticModel`` -> ``ExecutiveDocument``.

Reuses deterministic building blocks already computed elsewhere in the
pipeline (model statistics, schema shape, data-source summaries, and the
full audit rule engine for KPI selection, known risks, the maintenance note,
and recommendations) rather than re-deriving them — the "Knowledge
Generation Layer" fanning out from one parsed model. Known risks and future
recommendations pull the *same* ``priority``/``issue``/``why_it_matters``
text the Audit & Health Report and technical document show (never re-derived
independently — that was the earlier source of the three documents
disagreeing on risk counts and ordering), just filtered to drop the "dax"
category, whose issue text names DAX constructs directly and belongs in the
developer-facing documents, not here. ``business_purpose``, ``business_value``,
and ``maintenance_overview`` optionally go through an LLM for polished prose;
Key KPI meanings optionally go through the same DAX Translator agent the
technical doc's Measure Catalog uses, so a KPI is described the same way
everywhere instead of falling back to a mechanical DAX-to-English gloss
whenever an LLM was available but never consulted for it. All with
deterministic fallbacks so the document is always complete offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.executive_document import ExecutiveDocument
from .. import audit_rules
from .. import io
from .. import usage
from ..deterministic import business_analyst_deterministic, schema_shape, translate_dax
from ..llm import LLMClient
from ..report_facts import business_plain_english, data_source_summaries, first_sentence
from .base import Warn, build_core_metadata, call_llm


def _deterministic_business_purpose(model) -> str:
    # Reuses the same deterministic narrative the technical document's
    # Executive Summary section falls back to — already concise (2-3
    # sentences) and free of table/DAX jargon.
    return business_analyst_deterministic(model).core_purpose


def _measure_visual_counts(model) -> dict[str, int]:
    """Measure name -> number of distinct visuals that bind it (not pages —
    ``usage.measure_usage`` already covers pages). Drives KPI selection."""
    measure_names = {m.name for m in model.all_measures()}
    counts: dict[str, int] = {}
    for p in model.pages:
        for v in p.visuals:
            seen_in_visual: set[str] = set()
            for f in v.fields:
                leaf = f.split(".")[-1]
                if leaf in measure_names and leaf not in seen_in_visual:
                    seen_in_visual.add(leaf)
                    counts[leaf] = counts.get(leaf, 0) + 1
    return counts


def _key_kpis(model, client: Optional[LLMClient], warn: Warn, limit: int = 5) -> list[str]:
    """Top measures by real usage — (#visuals using it) x (#pages it appears
    on) — rather than the first N in model order, which can surface a
    title/text-label measure as a "KPI". Excludes hidden measures, orphaned
    measures (never bound to a visual), and Text-category measures. Each
    entry carries a one-line meaning: the measure's own description if set,
    else the DAX Translator's actual business definition when a client is
    available (the same call the technical doc's Measure Catalog and the
    user guide's glossary make, so all three describe a measure the same
    way — see user_guide.py's ``_build_glossary``), else the deterministic
    business-safe fallback."""
    usage_pages = usage.measure_usage(model)
    used = usage.used_measure_names(model)
    visual_counts = _measure_visual_counts(model)

    llm_translations: dict[str, str] = {}
    if client is not None:
        for batch in io.dax_translator_batches(model):
            data = call_llm(client, io.DAX_TRANSLATOR_SYSTEM, batch, io.DAX_TRANSLATOR_SCHEMA, warn, "DAX Translator")
            if data:
                for t in data.get("translations", []):
                    if t.get("plain_english"):
                        llm_translations[t["name"]] = first_sentence(t["plain_english"])

    scored = []
    for i, m in enumerate(model.all_measures()):
        if m.is_hidden or m.name not in used:
            continue
        _, _, category = translate_dax(m.name, m.expression, m.format_string)
        if category == "Text":
            continue
        score = visual_counts.get(m.name, 0) * len(usage_pages.get(m.name, []))
        if m.description:
            meaning = first_sentence(m.description)
        elif m.name in llm_translations:
            meaning = llm_translations[m.name]
        else:
            meaning = business_plain_english(m.name, m.expression, m.format_string)
        scored.append((score, i, m.name, meaning))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [f"{name} — {meaning}" for _, _, name, meaning in scored[:limit]]


_IMPLEMENTATION_JARGON = ("DAX", "CROSSFILTER", "USERELATIONSHIP", "VAR")


def _known_risks(recommendations, limit: int = 5) -> tuple[list[str], set[int]]:
    """Executive-safe known risks: the same deterministic recommendation
    engine behind the Audit & Health Report and the technical document's
    Model Health section (`audit_rules.build_recommendations`), filtered to
    drop the "dax" category outright (its issue text names DAX constructs
    directly) and, defensively, any other recommendation whose issue/
    why-it-matters text happens to name a DAX construct (e.g. a governance
    or modeling finding that explains itself in developer terms) — belongs
    in the technical/audit documents, not here. Capped to the top
    severities. Keeps the three documents' risk lists consistent — same
    underlying findings, same severity order — without re-deriving risk
    detection here. Also returns the ``id()`` of every recommendation shown
    here, so ``_future_recommendations`` can skip them — otherwise the same
    top-severity items surface twice (P6: §11 repeating §9)."""
    safe = []
    for r in recommendations:
        if r.category == "dax":
            continue
        text = f"{r.issue} {r.why_it_matters}"
        if any(term in text for term in _IMPLEMENTATION_JARGON):
            continue
        safe.append(r)
    shown = safe[:limit]
    return [f"[{r.priority}] {r.issue} {r.why_it_matters}" for r in shown], {id(r) for r in shown}


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


def _future_recommendations(recommendations, shown_as_risks: set[int], limit: int = 3) -> list[str]:
    """Next-highest-priority items *not already listed* under Known Risks
    (P6: §11 must not repeat §9) — the audit's suggested_fix text is written
    for BI developers (VAR, CROSSFILTER(), DAX-level detail) and doesn't
    belong in a document that explicitly excludes implementation detail, so
    each item is phrased as ask + benefit on their own clause rather than run
    together into one mashed sentence."""
    remaining = [r for r in recommendations if id(r) not in shown_as_risks]
    return [f"{r.issue} — expected benefit: {r.expected_benefit}" for r in remaining[:limit]]


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

        # Reuse the full deterministic audit engine (Phase 1) for the
        # maintenance note, known risks, and recommendations, rather than
        # re-deriving best-practice/governance logic here.
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

        key_kpis = _key_kpis(model, client, warn)
        key_kpi_names = [k.split(" — ", 1)[0] for k in key_kpis]
        model_risks, risk_ids = _known_risks(recommendations)

        business_purpose = _deterministic_business_purpose(model)
        business_value = _business_value(key_kpi_names, audience)
        maintenance_overview = _maintenance_overview(
            refresh, owner, failed_practice_count, len(governance),
        )

        if client is not None:
            data = call_llm(
                client, io.EXECUTIVE_WRITER_SYSTEM,
                io.executive_writer_input(
                    business_purpose_draft=business_purpose,
                    key_kpis=key_kpi_names,
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
            key_kpis=key_kpis,
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
            future_recommendations=_future_recommendations(recommendations, risk_ids),
        )
