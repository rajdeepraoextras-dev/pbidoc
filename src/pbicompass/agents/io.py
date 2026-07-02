"""Agent I/O contracts: system prompts, JSON-schema output formats, and the
fact-builders that turn a ``SemanticModel`` into the compact JSON each agent
receives.

The JSON schemas are used both for the Anthropic structured-output
``output_config.format`` and as the validation target when parsing responses.
They follow the structured-output constraints (every object sets
``additionalProperties: false`` and lists its ``required`` keys).
"""

from __future__ import annotations

from ..schemas.model import SemanticModel

# --------------------------------------------------------------------------
# Business Analyst Agent  (-> Executive Summary & Business Guide, §II)
# --------------------------------------------------------------------------
BUSINESS_ANALYST_SYSTEM = """\
You are an expert senior BI consultant writing the core narrative sections of a comprehensive enterprise Power BI documentation handover. Your goal is to write highly detailed, extremely professional, and fluid natural English prose that feels like it was hand-written by a seasoned human analyst. Avoid all robotic summaries, generic phrases, or brief placeholders. Ensure that every single narrative field is thoroughly developed, explaining not just the 'what' but the underlying 'why' and 'how'.

Be highly specific and cross-reference real pages, visuals, slicers, and measures provided in the input. NEVER invent pages or visuals that are not explicitly present in the input model. Do not use formulaic or repetitive sentence patterns (e.g., avoid "This page displays X and Y. The user can filter Z."). Write organic, flowing descriptions.

You must populate the following fields with rich, comprehensive information:
- core_purpose: Write an extensive, multi-paragraph business narrative (at least 6-8 well-structured sentences) explaining the overarching business purpose of this report. Connect the report's name, its primary semantic model components (fact/dimension tables), and its key measures to tell a complete business story. Describe what specific strategic or operational decisions this dashboard directly supports, what business pain points it resolves, and the key analytical questions it is designed to answer.
- pages: Provide a refined, user-friendly human title for EVERY visible page, alongside a highly detailed, comprehensive narrative description (at least 5-8 sentences per page). The summary must read as cohesive, flowing prose. Describe the distinct analytical focus of the page, the user persona who would benefit most from it, and how the visuals and metrics collectively work together to reveal business insights. Avoid merely listing visual types; describe the story they build together.
- navigation_guide: Write a clear, descriptive, step-by-step interactive manual (using multiple descriptive sentences per step) for a new user navigating the report. Reference the exact slicers, fields, and page targets. Explain not just how to click, but how cross-filtering, slicer selections, cross-highlighting, and drill-through paths behave to alter the context of the data and guide user investigation.
- complex_visual_explainers: For complex or specialized visuals (such as decomposition trees, key influencers, scatter charts, maps, gauges, or custom visuals), provide an in-depth, step-by-step guide explaining how a business user should read, interpret, and interact with the visual. Detail the significance of the visual's structure and explain how to draw actionable insights from it.
"""

BUSINESS_ANALYST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["core_purpose", "pages", "navigation_guide", "complex_visual_explainers"],
    "properties": {
        "core_purpose": {"type": "string"},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page_title", "summary"],
                "properties": {
                    "page_title": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
        "navigation_guide": {"type": "array", "items": {"type": "string"}},
        "complex_visual_explainers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["visual", "page", "how_to_read"],
                "properties": {
                    "visual": {"type": "string"},
                    "page": {"type": "string"},
                    "how_to_read": {"type": "string"},
                },
            },
        },
    },
}


def business_analyst_input(model: SemanticModel) -> dict:
    key_measures = [m.name for m in model.all_measures() if not m.is_hidden][:25]
    return {
        "report_name": model.report_name,
        "tables": [
            {"name": t.name, "kind": t.kind, "has_measures": bool(t.measures)}
            for t in model.tables
        ],
        "key_measures": key_measures,
        "pages": [
            {
                "display_name": p.display_name,
                "is_hidden": p.is_hidden,
                "is_drillthrough": p.is_drillthrough,
                "visuals": [
                    {
                        "type": v.type,
                        "title": v.title,
                        "is_slicer": v.is_slicer,
                        "fields": v.fields,
                    }
                    for v in p.visuals
                ],
            }
            for p in model.pages
        ],
    }


# --------------------------------------------------------------------------
# DAX Translator Agent  (-> Measure Catalog plain-English, §V)
# --------------------------------------------------------------------------
DAX_TRANSLATOR_SYSTEM = """\
You are an expert senior DAX developer and data architect documenting measures for an enterprise data dictionary designed for both technical analysts and business stakeholders. For each measure, you will receive its name, home table, DAX expression, and format string. Write a clear, highly detailed, and natural-language explanation for each metric. Write in complete, professional sentences, as though a human expert were explaining the metric's utility and logic to a colleague.

Explain WHAT the measure represents in business terms, WHY it exists, how it should be interpreted, and how it behaves under different filter contexts. Avoid restating the DAX code literally, and NEVER write short, lazy stubs (e.g., do not write "Calculates sum of sales").

For each measure, populate:
- plain_english: A comprehensive 4-6 sentence business definition in natural, flowing prose. Describe the business meaning, the mathematical concept in plain terms, and how a business user should interpret the resulting value (including how it relates to other metrics). Provide a concrete business example if helpful.
- caveats: A thorough breakdown of filters, exclusions, time-intelligence, grain dependencies, or specific edge-case behaviors (e.g., "ignores filters on the Region table", "only evaluates orders where the Status is not Canceled", "performs division and returns blank if the denominator is zero to prevent errors", "compares the current calendar period to the same period in the prior fiscal year"). Be explicit and detailed. If there are no caveats, provide an empty string.
- category: One of: Revenue, Cost, Ratio, Count, Time-Intelligence, Ranking, Text, Aggregation, Other.

Return a translation for every single measure given in the input, keyed by its exact name.
"""

DAX_TRANSLATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["translations"],
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "plain_english", "caveats", "category"],
                "properties": {
                    "name": {"type": "string"},
                    "plain_english": {"type": "string"},
                    "caveats": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["Revenue", "Cost", "Ratio", "Count",
                                 "Time-Intelligence", "Other"],
                    },
                },
            },
        }
    },
}


def dax_translator_input(model: SemanticModel) -> dict:
    return {
        "measures": [
            {
                "name": m.name,
                "table": m.table,
                "expression": m.expression,
                "format_string": m.format_string,
            }
            for m in model.all_measures()
        ]
    }


# Batch caps for the DAX Translator prompt. A real-world model can have
# hundreds of measures with long DAX expressions; packing them all into one
# call makes the prompt (and the response) huge and slow, risking a timeout
# on the whole job. Each batch stays under both a measure-count and a total
# DAX-length budget.
DAX_TRANSLATOR_BATCH_SIZE = 25
DAX_TRANSLATOR_BATCH_CHARS = 8000


def dax_translator_batches(model: SemanticModel) -> list[dict]:
    """Split all measures into bounded batches, each shaped like
    :func:`dax_translator_input`'s return value, so a large model is
    translated across several smaller, bounded LLM calls instead of one."""
    batches: list[dict] = []
    batch: list[dict] = []
    batch_chars = 0
    for m in model.all_measures():
        entry = {
            "name": m.name,
            "table": m.table,
            "expression": m.expression,
            "format_string": m.format_string,
        }
        entry_chars = len(m.expression or "")
        if batch and (
            len(batch) >= DAX_TRANSLATOR_BATCH_SIZE
            or batch_chars + entry_chars > DAX_TRANSLATOR_BATCH_CHARS
        ):
            batches.append({"measures": batch})
            batch, batch_chars = [], 0
        batch.append(entry)
        batch_chars += entry_chars
    if batch:
        batches.append({"measures": batch})
    return batches


# --------------------------------------------------------------------------
# Data Modeler Agent  (-> Semantic Model narrative, §IV)
# --------------------------------------------------------------------------
DATA_MODELER_SYSTEM = """\
You are an expert enterprise data-modeling and database architect. You will receive the table metadata (with fact/dimension classifications) and relationship definitions (cardinality, cross-filter direction, active flags) of a Power BI semantic model. Write a comprehensive, detailed technical summary of the model's design and structure for BI developers.

Your response must include:
- summary: A detailed, multi-paragraph architectural narrative (at least 6-8 sentences) explaining the model's schema design (e.g., star schema, snowflake schema, or flat structure). Detail the roles of the key fact and dimension tables, explain the flow of filter propagation across relationships, and discuss the architectural choices, grain design, and how the tables support analytical reporting.
- risks: A thorough, itemized list of any modeling risks, smells, or antipatterns identified (e.g., bi-directional filters that could cause ambiguity, inactive relationships requiring USERELATIONSHIP, circular references, disconnected tables, or missing relationships). For each risk listed, write a detailed explanation of the exact logical or performance impact it could have, along with a brief recommendation of a mitigation pattern. Return an empty list if no risks are found.
"""

DATA_MODELER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "risks"],
    "properties": {
        "summary": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}


def data_modeler_input(model: SemanticModel) -> dict:
    return {
        "tables": [
            {"name": t.name, "kind": t.kind, "has_measures": bool(t.measures),
             "columns": len(t.columns)}
            for t in model.tables
        ],
        "relationships": [
            {
                "from": f"{r.from_table}[{r.from_column}]",
                "to": f"{r.to_table}[{r.to_column}]",
                "cardinality": f"{r.from_cardinality}-to-{r.to_cardinality}",
                "cross_filter": r.cross_filter,
                "is_active": r.is_active,
            }
            for r in model.relationships
        ],
    }


# --------------------------------------------------------------------------
# Column Describer Agent  (-> Data Dictionary descriptions, §IV)
# --------------------------------------------------------------------------
COLUMN_DESCRIBER_SYSTEM = """\
You are an expert senior database documenter and data architect. Your goal is to write clear, natural-language business and technical descriptions for columns in a Power BI data model.

For each column, explain what the field stores, its business relevance, and how it is typically used in analysis (e.g. as a key dimension for filtering, grouping, or as a value to be aggregated). Write in complete, professional sentences, keeping each description concise but informative (1-3 sentences).

Return a description for every single column given in the input.
"""

COLUMN_DESCRIBER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["columns"],
    "properties": {
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "column", "description"],
                "properties": {
                    "table": {"type": "string"},
                    "column": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        }
    },
}


def column_describer_input(model: SemanticModel) -> dict:
    cols = []
    for t in model.tables:
        for c in t.columns:
            if not c.is_hidden:
                cols.append({
                    "table": t.name,
                    "column": c.name,
                    "data_type": c.data_type,
                    "is_calculated": c.is_calculated,
                })
    return {"columns": cols}


# --------------------------------------------------------------------------
# Audit Narrator Agent  (-> Audit & Health Report narrative_overview)
# --------------------------------------------------------------------------
AUDIT_NARRATOR_SYSTEM = """\
You are a senior BI architect writing the opening narrative of an Audit & Health Report for other architects, technical leads, and governance stakeholders. You are given the deterministic findings already computed for this model: an overall health score and its component scores, a complexity assessment, and counts of DAX findings, failed best-practice checks, performance risks, governance findings, and unused assets, plus the top few prioritized recommendations.

Write a concise, professional overview (4-6 sentences) that:
- States the overall health score and what it means in plain terms.
- Names the one or two component areas dragging the score down the most, and the one or two that are strongest.
- Calls out the single most important recommendation to act on first, and why.
- Uses a confident, direct, enterprise tone — no filler, no generic hedging, no restating every input number back verbatim.

Do not invent findings beyond what is given. Do not recommend anything that is not already in the provided recommendations list.
"""

AUDIT_NARRATOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narrative_overview"],
    "properties": {
        "narrative_overview": {"type": "string"},
    },
}


def audit_narrator_input(
    health_overall: int,
    health_band: str,
    component_scores: dict[str, int],
    complexity_level: str,
    dax_finding_count: int,
    failed_practice_count: int,
    performance_risk_count: int,
    governance_finding_count: int,
    unused_asset_count: int,
    top_recommendations: list[dict[str, str]],
) -> dict:
    return {
        "health_overall": health_overall,
        "health_band": health_band,
        "component_scores": component_scores,
        "complexity_level": complexity_level,
        "dax_finding_count": dax_finding_count,
        "failed_practice_count": failed_practice_count,
        "performance_risk_count": performance_risk_count,
        "governance_finding_count": governance_finding_count,
        "unused_asset_count": unused_asset_count,
        "top_recommendations": top_recommendations,
    }


# --------------------------------------------------------------------------
# Executive Writer Agent  (-> Executive Summary narrative prose)
# --------------------------------------------------------------------------
EXECUTIVE_WRITER_SYSTEM = """\
You are a senior BI consultant writing an executive summary for managers, executives, and project owners who will spend at most ten minutes reading it. You are given deterministic drafts already computed for this report — a business purpose, key KPIs, model/report statistics, known risks, and a maintenance note — and you polish them into confident, concise executive prose.

Populate exactly these three fields:
- business_purpose: 2-4 sentences stating what business problem this report solves and what decisions it supports. No table names, no DAX, no "semantic model" — describe the business subject matter only.
- business_value: 2-3 sentences on the concrete value this report delivers (time saved, decisions enabled, risk reduced) — grounded in the KPIs and purpose given, not generic praise.
- maintenance_overview: 1-3 sentences summarizing what it takes to keep this report healthy, using the maintenance draft and risk/finding counts given — plain language, no jargon.

Do not invent facts, statistics, or risks beyond what is given. Do not restate every input number verbatim — synthesize. No filler, no generic AI phrasing like "in today's data-driven world."
"""

EXECUTIVE_WRITER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["business_purpose", "business_value", "maintenance_overview"],
    "properties": {
        "business_purpose": {"type": "string"},
        "business_value": {"type": "string"},
        "maintenance_overview": {"type": "string"},
    },
}


def executive_writer_input(
    business_purpose_draft: str,
    key_kpis: list[str],
    model_statistics: dict[str, int],
    report_statistics: dict[str, int],
    known_risks: list[str],
    maintenance_draft: str,
) -> dict:
    return {
        "business_purpose_draft": business_purpose_draft,
        "key_kpis": key_kpis,
        "model_statistics": model_statistics,
        "report_statistics": report_statistics,
        "known_risks": known_risks,
        "maintenance_draft": maintenance_draft,
    }


# --------------------------------------------------------------------------
# User Guide Writer Agent  (-> Business User Guide prose)
# --------------------------------------------------------------------------
USER_GUIDE_WRITER_SYSTEM = """\
You are writing a Business User Guide for a Power BI report, aimed at a business user who has never seen it before — write as if training a new employee on their first day. You are given deterministic drafts already computed for the report's introduction and for each page: a purpose draft and a list of common-scenario drafts, built from the report's pages, fields, and filters.

Rewrite them into warm, plain-English prose that:
- Never uses the words "table", "DAX", "semantic model", "measure" (say "metric" or name it directly), "column", "relationship", or "query" — describe what the user sees and does, not how the report is built.
- For each page, keep the purpose to 2-3 sentences: what this page is for and what questions it helps answer.
- Keep common scenarios short and concrete — "Use this page when you want to..." — grounded only in the fields and filters given, never invented.
- For the introduction, write 2-4 welcoming sentences that orient a first-time user to the report as a whole.

Do not invent pages, fields, or workflows beyond what is given in the drafts.
"""

USER_GUIDE_WRITER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["introduction", "pages"],
    "properties": {
        "introduction": {"type": "string"},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["page_title", "purpose", "common_scenarios"],
                "properties": {
                    "page_title": {"type": "string"},
                    "purpose": {"type": "string"},
                    "common_scenarios": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def user_guide_writer_input(
    report_name: str,
    introduction_draft: str,
    pages: list[dict],
) -> dict:
    return {
        "report_name": report_name,
        "introduction_draft": introduction_draft,
        "pages": pages,
    }
