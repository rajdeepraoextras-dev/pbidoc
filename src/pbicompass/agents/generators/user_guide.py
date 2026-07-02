"""Business User Guide generator — ``SemanticModel`` -> ``UserGuideDocument``.

Reuses ``business_analyst_deterministic`` (already jargon-free page summaries,
navigation guide, and complex-visual explainers — built for the technical
document's Executive Summary section, and just as suitable here) and
``report_facts``/``translate_dax`` for structured page/glossary facts, rather
than re-deriving any of it. Hidden pages are skipped entirely — a business
user's guide has no reason to document a page nobody sees. ``bookmarks``/
``tooltips`` are always empty (today's ``model.json`` has no such data — a
future parser enhancement, out of scope here).

Only the introduction and each page's ``purpose``/``common_scenarios``
optionally go through an LLM for warmer prose, with an explicit
jargon-avoidance instruction (see ``io.USER_GUIDE_WRITER_SYSTEM``) and a
deterministic fallback so the document is always complete offline.
"""

from __future__ import annotations

from typing import Optional

from ...schemas.user_guide_document import GlossaryTerm, PageGuide, UserGuideDocument
from .. import io
from ..deterministic import business_analyst_deterministic, translate_dax
from ..llm import LLMClient
from ..report_facts import report_pages, slicers
from .base import Warn, build_core_metadata, call_llm

_CATEGORY_DEFINITIONS = {
    "Revenue": "Money earned, typically before deductions like tax or discounts.",
    "Cost": "Money spent or costs incurred.",
    "Ratio": "A comparison between two values, such as an average or a percentage.",
    "Count": "The number of records or items that match certain criteria.",
    "Time-Intelligence": "How a metric performs over a specific period of time, such as year-to-date.",
    "Ranking": "A ranking of items from highest to lowest (or vice versa) to highlight top or bottom performers.",
    "Text": "A combined or formatted piece of text-based information.",
    "Aggregation": "A summary of a group of values, such as a total or an average.",
    "Other": "A custom metric specific to this report.",
}

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


def _build_glossary(model) -> list[GlossaryTerm]:
    terms: list[GlossaryTerm] = []
    seen_measure_names = set()
    for m in model.all_measures():
        if m.is_hidden or m.name in seen_measure_names:
            continue
        seen_measure_names.add(m.name)
        _, _, category = translate_dax(m.name, m.expression, m.format_string)
        terms.append(GlossaryTerm(
            term=m.name,
            plain_definition=_CATEGORY_DEFINITIONS.get(category, _CATEGORY_DEFINITIONS["Other"]),
        ))

    measure_names = {m.name for m in model.all_measures()}
    seen_dims: set[str] = set()
    for p in model.pages:
        for v in p.visuals:
            for f in v.fields:
                leaf = f.split(".")[-1]
                if leaf and leaf not in measure_names and leaf not in seen_dims:
                    seen_dims.add(leaf)
                    terms.append(GlossaryTerm(term=leaf, plain_definition=_dimension_definition(leaf)))
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


def _business_questions(metrics: list[str], dims: list[str]) -> list[str]:
    questions = [f"What is our {m.lower()}?" for m in metrics[:2]]
    if metrics and dims:
        questions.append(f"How does {metrics[0].lower()} vary by {dims[0].lower()}?")
    return questions


def _navigation_tips(page_filters: list[str], visual_count: int) -> list[str]:
    tips = []
    for field in page_filters:
        tips.append(f"Use the '{field}' filter to narrow down what you see on this page.")
    if visual_count > 1:
        tips.append("Click on any chart to highlight the related data across the rest of the page.")
    return tips


def _common_scenarios(metrics: list[str], dims: list[str], filters: list[str]) -> list[str]:
    scenarios = []
    if metrics:
        scenarios.append(f"Use this page when you want to check {metrics[0].lower()} at a glance.")
    if filters:
        scenarios.append(f"Filter by '{filters[0]}' when you only care about one {filters[0].lower()} at a time.")
    if metrics and dims:
        scenarios.append(f"Compare {metrics[0].lower()} across different {dims[0].lower()} values to spot trends.")
    return scenarios


def _drillthrough_actions(drillthrough_page_names: list[str]) -> list[str]:
    return [f"Right-click a data point and choose Drill through to open '{name}' for more detail."
            for name in drillthrough_page_names]


def _introduction(model, core_purpose: str) -> str:
    return (f"Welcome! This guide explains how to use the '{model.report_name}' report — no "
            f"technical background needed. {core_purpose}")


def _getting_started(pages: list[PageGuide]) -> list[str]:
    tips = []
    if pages:
        tips.append(f"Start on the '{pages[0].page_title}' page for an overview.")
    tips.append("Use the filters at the top of each page to focus on what matters to you.")
    tips.append("Right-click any data point to see more options, including drill-through where available.")
    return tips


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
    ) -> UserGuideDocument:
        warn = on_warning or (lambda _msg: None)
        model.compute_counts()

        analyst = business_analyst_deterministic(model)
        page_summary_by_title = {p.page_title: p.summary for p in analyst.pages}
        explainer_by_page_visual = {(e.page, e.visual): e.how_to_read for e in analyst.complex_visual_explainers}

        pages_facts = report_pages(model)
        slicer_facts = slicers(model)
        drillthrough_page_names = [pf["name"] for pf in pages_facts if pf["drillthrough"]]

        pages: list[PageGuide] = []
        for pf in pages_facts:
            if pf["hidden"]:
                continue  # a business user's guide has no reason to cover pages nobody sees
            name = pf["name"]
            visuals = pf["visuals"]
            metrics = sorted({m for v in visuals for m in v["metrics"]})
            dims = sorted({d for v in visuals for d in v["dimensions"]})
            page_filters = [s["field"].split(".")[-1] for s in slicer_facts if s["page"] == name]

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
                navigation_tips=_navigation_tips(page_filters, len(visuals)),
                business_questions_answered=_business_questions(metrics, dims),
                drillthrough_actions=[] if pf["drillthrough"] else _drillthrough_actions(drillthrough_page_names),
                bookmarks=[],
                tooltips=[],
                common_scenarios=_common_scenarios(metrics, dims, page_filters),
            ))

        introduction = _introduction(model, analyst.core_purpose)
        glossary = _build_glossary(model)
        getting_started = _getting_started(pages)

        if client is not None:
            data = call_llm(
                client, io.USER_GUIDE_WRITER_SYSTEM,
                io.user_guide_writer_input(
                    report_name=model.report_name,
                    introduction_draft=introduction,
                    pages=[
                        {"page_title": p.page_title, "purpose_draft": p.purpose,
                         "common_scenarios_draft": p.common_scenarios}
                        for p in pages
                    ],
                ),
                io.USER_GUIDE_WRITER_SCHEMA, warn, "User Guide Writer",
            )
            if data:
                introduction = data.get("introduction") or introduction
                polished_by_title = {p["page_title"]: p for p in data.get("pages", [])}
                for page in pages:
                    polished = polished_by_title.get(page.page_title)
                    if polished:
                        page.purpose = polished.get("purpose") or page.purpose
                        page.common_scenarios = polished.get("common_scenarios") or page.common_scenarios

        return UserGuideDocument(
            metadata=build_core_metadata(
                model, "user-guide", default_audience="Business users",
                owner=owner, audience=audience, refresh=refresh, version=version, status=status,
            ),
            introduction=introduction,
            pages=pages,
            glossary=glossary,
            getting_started=getting_started,
        )
