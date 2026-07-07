"""Business User Guide generator — ``SemanticModel`` -> ``UserGuideDocument``.

Reuses ``business_analyst_deterministic`` (already jargon-free page summaries,
navigation guide, and complex-visual explainers — built for the technical
document's Executive Summary section, and just as suitable here) and
``report_facts``/``translate_dax`` for structured page/glossary facts, rather
than re-deriving any of it. Hidden pages are skipped entirely — a business
user's guide has no reason to document a page nobody sees. ``bookmarks``/
``tooltips`` are always empty (today's ``model.json`` has no such data — a
future parser enhancement, out of scope here).

The introduction and each page's ``purpose``/``common_scenarios`` optionally
go through an LLM for warmer prose (``io.USER_GUIDE_WRITER_SYSTEM``), and the
glossary optionally goes through the same DAX Translator agent the technical
doc's Measure Catalog uses (``io.DAX_TRANSLATOR_SYSTEM``) for a real business
definition instead of a mechanical DAX gloss — both with a deterministic
fallback so the document is always complete offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.user_guide_document import GlossaryTerm, PageGuide, UserGuideDocument
from .. import io
from ..context import JobAIContext, build_job_context
from ..critic import apply_critic_pass, apply_results
from ..deterministic import business_analyst_deterministic
from ..grounding import apply_grounding_pass
from ..llm import LLMClient
from ..report_facts import (
    business_plain_english,
    field_parameter_table_names,
    first_sentence,
    report_pages,
    slicers,
)
from .base import Warn, build_core_metadata, call_llm, call_llm_with_retry

_DIMENSION_DEFINITIONS = [
    (("date", "calendar", "month", "year", "quarter"),
     "A time period used to filter and compare data across days, months, or years."),
    (("customer", "client", "account"), "The person or organization the data is about."),
    (("product", "item", "sku"), "The product or item the data is about."),
    (("region", "country", "city", "state", "territory"),
     "A geographic area used to compare performance across locations."),
    (("segment", "category", "group", "type"), "A grouping used to compare different kinds of records."),
]


def _dimension_definition(name: str) -> str:
    lower = name.lower()
    for keywords, definition in _DIMENSION_DEFINITIONS:
        if any(k in lower for k in keywords):
            return definition
    return f"A field used to filter or group the data by {name}."


def _build_glossary(model, client: Optional[LLMClient], warn: Warn,
                     ai_context: Optional[JobAIContext]) -> list[GlossaryTerm]:
    """Priority order per measure: human-provided description -> the DAX
    Translator's *actual* business definition (Phase 0: the job-shared
    ``ai_context.translations`` — the same result the technical doc's
    Measure Catalog consumes, so the two documents describe a measure the
    same way instead of the glossary falling back to a mechanical
    DAX-to-English gloss whenever a translation was available but just
    never consulted) -> the deterministic business-safe fallback -> a typed
    fallback for the rare measure with neither. Never the generic "a custom
    metric specific to this report" bucket — a business glossary that can't
    tell two measures apart isn't documentation."""
    llm_translations: dict[str, str] = {}
    if ai_context is not None and ai_context.translations:
        # Same DAX Translator result the technical doc's Measure Catalog
        # consumes (agents/generators/technical.py::_measure_catalog) — one
        # job-wide call instead of a second one here, so a real business
        # definition ("users who had sales last year but not this year")
        # reaches the glossary too, instead of only ever falling back to the
        # mechanical DAX-to-English gloss.
        for name, t in ai_context.translations.items():
            if t.get("plain_english"):
                llm_translations[name] = first_sentence(t["plain_english"])

    terms: list[GlossaryTerm] = []
    seen_measure_names = set()
    for m in model.all_measures():
        if m.is_hidden or m.name in seen_measure_names:
            continue
        seen_measure_names.add(m.name)
        if m.description:
            definition = first_sentence(m.description)
        elif m.name in llm_translations:
            definition = llm_translations[m.name]
        else:
            definition = business_plain_english(m.name, m.expression, m.format_string) \
                or "Definition pending — see the technical documentation."
        terms.append(GlossaryTerm(term=m.name, plain_definition=definition))

    measure_names = {m.name for m in model.all_measures()}
    field_param_tables = field_parameter_table_names(model)
    seen_dims: set[str] = set()
    for p in model.pages:
        for v in p.visuals:
            for f in v.fields:
                parts = f.split(".")
                leaf = parts[-1]
                if not leaf or leaf in measure_names or leaf in seen_dims:
                    continue
                seen_dims.add(leaf)
                # I4: a field parameter is a UI selector, not a business
                # dimension — label it as what it is instead of guessing a
                # generic "grouping" definition for a name like "select".
                is_field_param = len(parts) > 1 and parts[0] in field_param_tables
                definition = ("A field selector that switches what the chart displays."
                             if is_field_param else _dimension_definition(leaf))
                terms.append(GlossaryTerm(term=leaf, plain_definition=definition))
    return terms


def _simple_visual_description(visual: dict) -> str:
    metrics, dims = visual.get("metrics", []), visual.get("dimensions", [])
    if metrics and dims:
        return f"Shows {', '.join(metrics)} broken down by {', '.join(dims)}."
    if metrics:
        return f"Shows {', '.join(metrics)}."
    if dims:
        return f"Shows a breakdown by {', '.join(dims)}."
    return "Provides supporting detail for this page."


_TIME_KEYWORDS = ("date", "calendar", "month", "year", "quarter", "period", "week", "day")
_GEO_KEYWORDS = ("region", "country", "city", "state", "territory", "continent", "province", "postal", "zip", "geography")
_GEO_CATEGORIES = {"address", "city", "continent", "country", "county", "place",
                   "postalcode", "stateorprovince", "region"}


def _column_lookup(model) -> dict:
    lookup = {}
    for t in model.tables:
        for c in t.columns:
            lookup.setdefault(c.name, c)
    return lookup


def _dimension_kind(name: str, columns: dict) -> str:
    """Classify a dimension as "time", "geo", or "other" — from the parsed
    column's data_type/data_category when available, else from its name.
    Drives which question template a chart pair earns (1.3)."""
    col = columns.get(name)
    if col is not None:
        if col.data_type in ("date", "dateTime"):
            return "time"
        if col.data_category and col.data_category.lower() in _GEO_CATEGORIES:
            return "geo"
    lower = name.lower()
    if any(k in lower for k in _TIME_KEYWORDS):
        return "time"
    if any(k in lower for k in _GEO_KEYWORDS):
        return "geo"
    return "other"


def _chart_pair_questions(visuals: list[dict], columns: dict) -> list[str]:
    """Business questions grounded in the metric+dimension pairs actually
    charted together on the page — never invented beyond what a visual
    shows. A visual with no metric+dimension pair contributes nothing (no
    pair -> no question), so a page with no such visual gets no section at
    all, per the "no mad-libs" quality floor."""
    questions: list[str] = []
    seen: set[str] = set()
    for v in visuals:
        metrics, dims = v.get("metrics", []), v.get("dimensions", [])
        if not (metrics and dims):
            continue
        metric, dim = metrics[0], dims[0]
        kind = _dimension_kind(dim, columns)
        if kind == "time":
            question = f"How has {metric} trended by {dim}?"
        elif kind == "geo":
            question = f"How does {metric} compare across {dim}?"
        else:
            question = f"How is {metric} distributed by {dim}?"
        if question not in seen:
            seen.add(question)
            questions.append(question)
        if len(questions) >= 3:
            break
    return questions


def _navigation_tips(page_filters: list[str], visual_count: int) -> list[str]:
    tips = []
    for field in page_filters:
        tips.append(f"Use the '{field}' filter to narrow down what you see on this page.")
    if visual_count > 1:
        tips.append("Click on any chart to highlight the related data across the rest of the page.")
    return tips


def _drillthrough_actions(drillthrough_page_names: list[str]) -> list[str]:
    return [f"Right-click a data point and choose Drill through to open '{name}' for more detail."
            for name in drillthrough_page_names]


def _introduction(model, core_purpose: str, insights: Optional[dict] = None) -> str:
    """Phase 2: when the whole-model synthesis has a confident report
    purpose, it seeds this intro's core_purpose instead of the generic
    deterministic one — the same free upgrade ``technical.py``'s Business
    Analyst fallback gets, purchased by a call already made for this job."""
    if insights:
        rp = insights.get("report_purpose") or {}
        if rp.get("statement") and rp.get("confidence") in ("High", "Medium"):
            core_purpose = rp["statement"]
    return (f"Welcome! This guide explains how to use the '{model.report_name}' report — no "
            f"technical background needed. {core_purpose}")


def _getting_started(pages: list[PageGuide]) -> list[str]:
    tips = []
    if pages:
        tips.append(f"Start on the '{pages[0].page_title}' page for an overview.")
    tips.append("Use the filters at the top of each page to focus on what matters to you.")
    tips.append("Right-click any data point to see more options, including drill-through where available.")
    return tips


def _narrative_triples(doc: UserGuideDocument) -> list[tuple[str, str, "callable"]]:
    """The user guide's narrative fields as ``(location, text, setter)``
    triples — shared by the critic (5.3) and grounding (Phase 3) passes so
    neither re-derives the other's field list."""
    triples: list[tuple[str, str, "callable"]] = []

    def _set_introduction(v: str) -> None:
        doc.introduction = v
    triples.append(("introduction", doc.introduction, _set_introduction))

    for i, page in enumerate(doc.pages):
        def _set_purpose(v: str, _p=page) -> None:
            _p.purpose = v
        triples.append((f"pages[{i}].purpose", page.purpose, _set_purpose))

    for i, term in enumerate(doc.glossary):
        def _set_definition(v: str, _t=term) -> None:
            _t.plain_definition = v
        triples.append((f"glossary[{i}].plain_definition", term.plain_definition, _set_definition))
    return triples


def _run_critic(doc: UserGuideDocument, model, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
    """5.3: one critic pass over the user guide's narrative fields."""
    known_names = {t.name for t in model.tables}
    known_names |= {m.name for m in model.all_measures()}
    known_names |= {p.display_name for p in model.pages}

    triples = _narrative_triples(doc)
    fields = [(loc, text) for loc, text, _ in triples]
    results = apply_critic_pass(fields, client, known_names=known_names, warn=warn, ai_context=ai_context)
    apply_results(triples, results)


def _run_grounding(doc: UserGuideDocument, client, warn: Warn, ai_context: Optional[JobAIContext]) -> None:
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


class BusinessGuideGenerator:
    """Teaches a business user how to use the report without needing the
    developer — no table/DAX/semantic-model talk, written like onboarding a
    new employee."""

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
    ) -> UserGuideDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()
        if ai_context is None and client is not None:
            ai_context = build_job_context(model, client, warn)

        analyst = business_analyst_deterministic(model)
        page_summary_by_title = {p.page_title: p.summary for p in analyst.pages}
        explainer_by_page_visual = {(e.page, e.visual): e.how_to_read for e in analyst.complex_visual_explainers}

        pages_facts = report_pages(model)
        slicer_facts = slicers(model)
        columns = _column_lookup(model)
        drillthrough_page_names = [pf["name"] for pf in pages_facts if pf["drillthrough"]]

        pages: list[PageGuide] = []
        for pf in pages_facts:
            if pf["hidden"]:
                continue  # a business user's guide has no reason to cover pages nobody sees
            name = pf["name"]
            visuals = pf["visuals"]
            metrics = sorted({m for v in visuals for m in v["metrics"]})
            # slicers() dedupes on the full qualified field (e.g. "Orders.Type"
            # vs "Restaurant.Type" are legitimately distinct there), but a
            # business user only sees the leaf name — two different fields
            # that share a leaf name (a common case) must still collapse to
            # one "Type (2 slicers)" line here, or the duplicate leaks back in
            # as "Type, Type" and a doubled nav-tip bullet (1.7).
            page_slicers = [s for s in slicer_facts if s["page"] == name]
            leaf_counts: dict[str, int] = {}
            filter_fields: list[str] = []
            for s in page_slicers:
                leaf = s["field"].split(".")[-1]
                if leaf not in leaf_counts:
                    filter_fields.append(leaf)
                leaf_counts[leaf] = leaf_counts.get(leaf, 0) + s["count"]
            page_filters = [
                f"{leaf} ({leaf_counts[leaf]} slicers)" if leaf_counts[leaf] > 1 else leaf
                for leaf in filter_fields
            ]

            visual_descriptions = [
                {"visual": v["label"],
                 "what_it_shows": explainer_by_page_visual.get((name, v["label"]), _simple_visual_description(v))}
                for v in visuals
            ]

            pages.append(PageGuide(
                page_title=name,
                purpose=page_summary_by_title.get(name, f"This page covers {', '.join(metrics[:3]) or 'supporting detail'}."),
                main_kpis=metrics[:5],
                visual_descriptions=visual_descriptions,
                filters=page_filters,
                navigation_tips=_navigation_tips(filter_fields, len(visuals)),
                business_questions_answered=_chart_pair_questions(visuals, columns),
                drillthrough_actions=[] if pf["drillthrough"] else _drillthrough_actions(drillthrough_page_names),
                bookmarks=[],
                tooltips=[],
                common_scenarios=[],
                wireframe_svg=pf.get("wireframe_svg"),
            ))

        report_context = ai_context.insights if ai_context is not None else None
        introduction = _introduction(model, analyst.core_purpose, report_context)
        glossary = _build_glossary(model, client, warn, ai_context)
        getting_started = _getting_started(pages)

        if client is not None:
            # Batched by page (io.user_guide_writer_batches) with one retry per
            # batch: a failed/invalid batch degrades only the pages it covers
            # (kept on their deterministic purpose/common_scenarios), rather
            # than silently falling back for the whole guide (1.4).
            page_drafts = [
                {"page_title": p.page_title, "purpose_draft": p.purpose,
                 "common_scenarios_draft": p.common_scenarios}
                for p in pages
            ]
            introduction_set = False
            offset = 0
            for batch in io.user_guide_writer_batches(model.report_name, introduction, page_drafts,
                                                       report_context=report_context):
                batch_titles = [p["page_title"] for p in batch["pages"]]
                data = call_llm_with_retry(client, io.USER_GUIDE_WRITER_SYSTEM, batch, io.USER_GUIDE_WRITER_SCHEMA,
                                            ai_context=ai_context, name="User Guide Writer")
                if data:
                    if not introduction_set and data.get("introduction"):
                        introduction, introduction_set = data["introduction"], True
                    polished_by_title = {p["page_title"]: p for p in data.get("pages", [])}
                    for page in pages[offset:offset + len(batch_titles)]:
                        polished = polished_by_title.get(page.page_title)
                        if polished:
                            page.purpose = polished.get("purpose") or page.purpose
                            page.common_scenarios = polished.get("common_scenarios") or page.common_scenarios
                elif batch_titles:
                    warn(f"User Guide Writer: AI narrative unavailable for pages: {', '.join(batch_titles)} "
                         f"— deterministic summary used")
                offset += len(batch_titles)

        doc = UserGuideDocument(
            metadata=build_core_metadata(
                model, "user-guide", default_audience="Business users",
                owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            ),
            introduction=introduction,
            pages=pages,
            glossary=glossary,
            getting_started=getting_started,
        )

        if client is not None:
            _run_critic(doc, model, client, warn, ai_context)
            _run_grounding(doc, client, warn, ai_context)

        return doc
