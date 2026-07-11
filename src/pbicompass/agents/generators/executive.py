"""Executive Summary generator — ``SemanticModel`` -> ``ExecutiveDocument``.

Six sections (G.1): Purpose & Value, Key KPIs, Top Risks & Recommended
Actions, Data & Refresh at a Glance, Ownership & Accountability, What's Next
— reads in under ten minutes and prints to no more than two pages. Reuses
deterministic building blocks already computed elsewhere in the pipeline
(schema shape, data-source summaries, and the full audit rule engine for KPI
selection and risks) rather than re-deriving them. Top risks pull the *same*
``priority``/``issue``/``why_it_matters``/``rule_id`` the Audit & Health
Report and technical document show (never re-derived independently — that
was the earlier source of the three documents disagreeing on risk counts
and ordering) — one merged, ranked list, not two that used to repeat each
other (P6). ``purpose``, ``business_value``, and ``maintenance_note``
optionally go through an LLM for polished prose; Key KPI meanings optionally
go through the same DAX Translator agent the technical doc's Measure Catalog
uses. All with deterministic fallbacks so the document is always complete
offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.executive_document import (
    ExecutiveDocument, ExecutiveNextStep, ExecutivePageThumbnail, ExecutiveRisk,
)
from .. import audit_rules
from .. import io
from .. import usage
from ..consistency import AuditVerdicts, check_consistency
from ..context import JobAIContext, build_job_context
from ..traceability import build_requirements_matrix, coverage_stat
from ..critic import apply_critic_pass, apply_results
from ..deterministic import business_analyst_deterministic, schema_shape, translate_dax
from ..grounding import apply_grounding_pass
from ..llm import LLMClient
from ..report_facts import business_plain_english, data_source_type_counts, first_sentence
from .base import Warn, build_core_metadata, call_llm


def _deterministic_purpose(model) -> str:
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


def _key_kpis(model, client: Optional[LLMClient], warn: Warn,
              ai_context: Optional[JobAIContext], limit: int = 5) -> list[str]:
    """Top measures by real usage — (#visuals using it) x (#pages it appears
    on) — rather than the first N in model order, which can surface a
    title/text-label measure as a "KPI". Excludes hidden measures, orphaned
    measures (never bound to a visual), and Text-category measures. Each
    entry carries a one-line meaning: the measure's own description if set,
    else the DAX Translator's actual business definition when available
    (Phase 0: the shared ``ai_context.translations`` — the same job-wide
    result the technical doc's Measure Catalog and the user guide's glossary
    consume, so all three describe a measure the same way — see
    user_guide.py's ``_build_glossary``), else the deterministic
    business-safe fallback."""
    usage_pages = usage.measure_usage(model)
    used = usage.used_measure_names(model)
    visual_counts = _measure_visual_counts(model)

    llm_translations: dict[str, str] = {}
    if ai_context is not None and ai_context.translations:
        for name, t in ai_context.translations.items():
            if t.get("plain_english"):
                llm_translations[name] = first_sentence(t["plain_english"])

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


def _top_risks(recommendations, limit: int = 5) -> tuple[list[ExecutiveRisk], set[str]]:
    """Executive-safe top risks (G.1): the same deterministic recommendation
    engine behind the Audit & Health Report (``audit_rules.build_recommendations``),
    filtered to drop the "dax" category outright (its issue text names DAX
    constructs directly) and, defensively, any recommendation whose text
    happens to name a DAX construct. Each risk is phrased as a consequence
    (``issue`` + ``why_it_matters``) plus a specific ask (``suggested_fix``,
    with any appended fix-snippet code block stripped — that detail belongs
    in the audit/technical docs, not here) and carries its own ``rule_id``
    so the rendered document can deep-link to the exact audit finding (I5).
    One merged, ranked list — "Known Risks" and "Future Recommendations"
    used to be two lists that repeated each other (P6); there's only one
    now, so ``_next_steps`` can safely show whatever didn't make the cut."""
    safe = []
    for r in recommendations:
        if r.category == "dax":
            continue
        text = f"{r.issue} {r.why_it_matters}"
        if any(term in text for term in _IMPLEMENTATION_JARGON):
            continue
        safe.append(r)
    shown = safe[:limit]
    risks = [
        ExecutiveRisk(
            severity=r.priority,
            consequence=f"{r.issue} {r.why_it_matters}".strip(),
            ask=_business_safe_ask(r.suggested_fix),
            rule_id=getattr(r, "rule_id", "") or "",
        )
        for r in shown
    ]
    shown_ids = {r.rule_id for r in risks if r.rule_id}
    return risks, shown_ids


def _business_safe_ask(suggested_fix: str) -> str:
    """The audit engine's ``suggested_fix`` text is written for BI
    developers and sometimes names DAX constructs directly (e.g.
    "...use CROSSFILTER() in specific DAX measures...") even when the
    ``issue``/``why_it_matters`` text stays business-safe — this document
    excludes implementation detail entirely, so a fix that can't be
    paraphrased safely degrades to a generic-but-true delegation instead of
    ever showing DAX/CROSSFILTER/USERELATIONSHIP/VAR to an executive."""
    fix = suggested_fix.split("\n\n```", 1)[0].strip()
    if any(term in fix for term in _IMPLEMENTATION_JARGON):
        return "Ask your Power BI developer or BI team to apply the technical fix documented in the audit report."
    return fix


def _apply_reframed_risks(top_risks: list[ExecutiveRisk], reframed: Optional[list[dict]]) -> None:
    """Overwrite each risk's ``consequence``/``ask`` with the Executive
    Writer's business-language reframing (D1), matched back positionally —
    the prompt asks for the same order/count as ``known_risks``. Ignored
    (deterministic wording kept) if the count doesn't line up, since a
    mismatched response can't be safely matched to the right risk."""
    if not reframed or len(reframed) != len(top_risks):
        return
    for risk, r in zip(top_risks, reframed):
        consequence = (r.get("consequence") or "").strip()
        ask = (r.get("ask") or "").strip()
        if consequence:
            risk.consequence = consequence
        if ask:
            risk.ask = ask


def _business_value(key_kpis: list[str], audience: Optional[str]) -> str:
    who = audience or "stakeholders"
    metrics = ", ".join(key_kpis[:3]) if key_kpis else "key metrics"
    return (f"This report gives {who} direct visibility into {metrics}, reducing reliance on "
            f"manual reporting and supporting faster, data-driven decisions.")


def _maintenance_note(failed_practice_count: int, governance_count: int) -> str:
    from ...render._shared import pluralize_count

    total = failed_practice_count + governance_count
    if total:
        return (f"{pluralize_count('item', total)} from the latest review should be checked periodically "
                f"to keep this report reliable and secure.")
    return "No outstanding items were found in the latest review; the report is in good shape to maintain."


def _next_step_action(r) -> str:
    """Business-safe one-line action for a "What's Next" row: the same
    DAX/CROSSFILTER/USERELATIONSHIP/VAR-stripping rule Top Risks' asks use
    (``_business_safe_ask``), applied to this recommendation's own issue +
    expected benefit so the table never leaks implementation jargon either."""
    text = f"{r.issue} — expected benefit: {r.expected_benefit}"
    if any(term in text for term in _IMPLEMENTATION_JARGON):
        return "Ask your Power BI developer or BI team to review this item from the audit report."
    return text


def _next_steps(recommendations, shown_rule_ids: set[str], metadata, warn: Warn,
                 limit: int = 5) -> list[ExecutiveNextStep]:
    """"What's next" (Day 5): up to ``limit`` remaining remediation items not
    already covered by Top Risks, as severity/action/effort rows — a
    boardroom reader triages a short table in seconds rather than reading
    prose bullets. Document-completeness is an internal production concern,
    not something an executive reader needs to see (D1) — it's reported to
    ``warn`` (surfaced as a job warning) instead of rendered into the doc."""
    from ...render._shared import compute_completeness, pluralize

    remaining = [r for r in recommendations if (getattr(r, "rule_id", "") or "") not in shown_rule_ids]
    steps = [
        ExecutiveNextStep(
            severity=r.priority, action=_next_step_action(r), effort=r.effort,
            rule_id=getattr(r, "rule_id", "") or "",
        )
        for r in remaining[:limit]
    ]
    _pct, missing_count, missing_fields = compute_completeness(metadata)
    if missing_count:
        warn(f"Executive Summary: {pluralize('field', missing_count)} still need business input: "
             f"{', '.join(missing_fields)}.")
    return steps


def _page_thumbnails(model, limit: int = 6) -> tuple[list[ExecutivePageThumbnail], int]:
    """Up to ``limit`` visible report pages as small wireframe thumbnails
    (Day 5 "Report at a glance"), plus the total number eligible (so the
    renderer can note "+N more" rather than growing the section unbounded).
    Reuses the exact SVG the technical document and user guide already
    build full-size (``report_facts.report_pages``), never a second drawing
    of the same page. Skips gracefully (empty list) when the model carries
    no layout coordinates (the pbix-parsed path) — ``render_wireframe``
    already returns falsy in that case, same guard every other wireframe
    consumer uses."""
    from ...render._shared import anchor_slug
    from ..report_facts import report_pages

    pages = [p for p in report_pages(model) if not p["hidden"] and p.get("wireframe_svg")]
    thumbnails = [
        ExecutivePageThumbnail(name=p["name"], svg=p["wireframe_svg"], anchor=f"page-{anchor_slug(p['name'])}")
        for p in pages[:limit]
    ]
    return thumbnails, len(pages)


def _narrative_triples(doc: ExecutiveDocument) -> list[tuple[str, str, "callable"]]:
    """The executive doc's narrative fields as ``(location, text, setter)``
    triples — shared by the critic (5.3) and grounding (Phase 3) passes so
    neither re-derives the other's field list."""
    triples: list[tuple[str, str, "callable"]] = []

    def _set_purpose(v: str) -> None:
        doc.purpose = v
    triples.append(("purpose", doc.purpose, _set_purpose))

    def _set_business_value(v: str) -> None:
        doc.business_value = v
    triples.append(("business_value", doc.business_value, _set_business_value))

    def _set_maintenance_note(v: str) -> None:
        doc.maintenance_note = v
    triples.append(("maintenance_note", doc.maintenance_note, _set_maintenance_note))

    for i, risk in enumerate(doc.top_risks):
        def _set_consequence(v: str, _r=risk) -> None:
            _r.consequence = v
        triples.append((f"top_risks[{i}].consequence", risk.consequence, _set_consequence))
    return triples


def _run_critic(doc: ExecutiveDocument, model, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
    """5.3: one critic pass over the executive doc's narrative fields."""
    known_names = {t.name for t in model.tables}
    known_names |= {m.name for m in model.all_measures()}

    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_critic_pass(fields, client, known_names=known_names, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


def _run_grounding(doc: ExecutiveDocument, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
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
    doc: ExecutiveDocument, client, warn: Warn, ai_context: Optional[JobAIContext],
    audit_verdicts: Optional[AuditVerdicts],
) -> None:
    """Day 2: cross-artifact consistency check against the sibling Audit &
    Health Report's verdicts — its deterministic layer needs no LLM, so this
    runs even offline; a no-op only when no Audit document was generated
    alongside this one in the same job."""
    if audit_verdicts is None:
        return
    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = check_consistency(fields, client, verdicts=audit_verdicts, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


class ExecutiveSummaryGenerator:
    """Assembles a concise, non-technical summary for managers, executives,
    and project owners — readable in under ten minutes, no implementation
    details, no raw file paths, no model statistics beyond the KPI strip."""

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
        audit_verdicts: Optional[AuditVerdicts] = None,
        requirements_matrix: Optional[list] = None,
    ) -> ExecutiveDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()
        if ai_context is None and client is not None:
            ai_context = build_job_context(model, client, warn)
        if requirements_matrix is None:
            requirements_matrix = build_requirements_matrix(
                model, requirements, client, warn, ai_context=ai_context,
            )

        # Reuse the full deterministic audit engine (Phase 1) for the
        # maintenance note, top risks, and next steps, rather than
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
        health = audit_rules.compute_health_score(
            dax_findings, best_practices, performance_risks, governance, unused_assets,
        )

        key_kpis = _key_kpis(model, client, warn, ai_context)
        key_kpi_names = [k.split(" — ", 1)[0] for k in key_kpis]
        top_risks, shown_rule_ids = _top_risks(recommendations)

        purpose = _deterministic_purpose(model)
        if business_decision:
            # Day 3: the human-stated decision anchors the purpose sentence
            # even offline — the deterministic draft otherwise reads
            # generically ("this report supports spending pattern analysis")
            # with nothing tying it to what the business actually uses it for.
            purpose = f"{purpose} This report exists to support: {business_decision}"
        business_value = _business_value(key_kpi_names, audience)
        maintenance_note = _maintenance_note(failed_practice_count, len(governance))

        if client is not None:
            data = call_llm(
                client, io.EXECUTIVE_WRITER_SYSTEM,
                io.executive_writer_input(
                    business_purpose_draft=purpose,
                    key_kpis=key_kpi_names,
                    model_statistics=dict(model.meta.counts),
                    report_statistics={
                        "pages": len(model.pages),
                        "visible_pages": sum(1 for p in model.pages if not p.is_hidden),
                    },
                    known_risks=[
                        {"rule_id": r.rule_id, "severity": r.severity, "consequence": r.consequence, "ask": r.ask}
                        for r in top_risks
                    ],
                    maintenance_draft=maintenance_note,
                    report_context=ai_context.insights if ai_context is not None else None,
                    business_decision=business_decision,
                    target_audience=audience,
                ),
                io.EXECUTIVE_WRITER_SCHEMA, warn, "Executive Writer", ai_context=ai_context,
            )
            if data:
                purpose = data.get("business_purpose") or purpose
                business_value = data.get("business_value") or business_value
                maintenance_note = data.get("maintenance_overview") or maintenance_note
                _apply_reframed_risks(top_risks, data.get("reframed_risks"))

        metadata = build_core_metadata(
            model, "executive", default_audience="Managers, executives, and project owners",
            owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            author=author, reviewer=reviewer, classification=classification,
            business_decision=business_decision, requirements=requirements,
            security_notes=security_notes, refresh_notes=refresh_notes,
            deployment_notes=deployment_notes, access_notes=access_notes,
            glossary=glossary, assumptions=assumptions, support_notes=support_notes,
        )
        metadata.score_trend = audit_rules.get_shared_score_trend(
            ai_context, model.report_name or "UnknownReport", health.overall,
        )

        page_thumbnails, page_count = _page_thumbnails(model)

        doc = ExecutiveDocument(
            metadata=metadata,
            purpose=purpose,
            business_value=business_value,
            key_kpis=key_kpis,
            top_risks=top_risks,
            data_source_types=data_source_type_counts(model),
            refresh_schedule=refresh,
            refresh_notes=refresh_notes,
            maintenance_note=maintenance_note,
            steward=None,  # sourced from the enrichment file (5.1) once wired in
            classification=classification,
            next_steps=_next_steps(recommendations, shown_rule_ids, metadata, warn),
            requirements_coverage=coverage_stat(requirements_matrix),
            health=health,
            page_thumbnails=page_thumbnails,
            page_count=page_count,
        )

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)
            _run_grounding(doc, client, warn, ai_context)
        _run_consistency(doc, client, warn, ai_context, audit_verdicts)

        return doc
