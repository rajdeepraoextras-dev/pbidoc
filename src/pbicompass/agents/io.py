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
# Shared editorial standard — appended to every prose agent's system prompt.
# One definition so the "consultant, not chatbot" bar is enforced uniformly.
# --------------------------------------------------------------------------
STYLE_RULES = """
Editorial standard (applies to every field you write):
- Write like a senior consultant producing enterprise handover documentation, not a marketing writer. Enterprise readers skim: every sentence must carry a concrete fact or instruction.
- BANNED phrases and their variants: "comprehensive overview", "empowers stakeholders", "helps decision making", "provides insights", "strategic tool", "robust", "powerful", "leverage", "seamless", "in today's data-driven world". Never use marketing language or exaggeration.
- Prefer the specific over the generic. Bad: "The dashboard empowers stakeholders to make informed decisions." Good: "Regional managers use this page to compare sales performance across cities."
- State each fact exactly once. Do not restate in one field what another field already says; do not pad with restatements of the input.
- NEVER guess. If a purpose or meaning cannot be inferred from the input, write "Purpose could not be inferred automatically; requires business confirmation." Do not hedge with "possibly", "likely", or "potentially".
- Never invent pages, visuals, fields, measures, or workflows that are not present in the input.
"""

# --------------------------------------------------------------------------
# Business Analyst Agent  (-> Executive Summary & Business Guide, §II)
# --------------------------------------------------------------------------
BUSINESS_ANALYST_SYSTEM = """\
You are a senior BI consultant writing the executive summary and page documentation of an enterprise Power BI handover document. You receive the report's real tables, measures, pages, and visuals. Ground everything in that input.

Populate:
- core_purpose: Maximum 120 words. State (1) what business area the report covers, (2) the main KPIs it tracks, (3) who the primary users are, and (4) the specific decisions it supports (e.g. "weekly regional sales planning", "menu pricing reviews"). No filler sentences.
- pages: For EVERY visible page provide:
  - page_title: a clean human title.
  - summary: 2-3 sentences on what the page shows and the decision it supports. Do not list every visual — the visual inventory is documented elsewhere.
  - users: the role(s) who would use this page (e.g. "Sales managers; regional leads"), inferred from its content. If no role can be inferred, write "Requires business confirmation."
  - business_questions: 2-4 realistic questions a business user answers on this page. Phrase them the way a manager would ask ("Which cities generate the highest revenue?", "Is customer retention improving?") — never "What is <measure name>?".
  - decisions: one sentence naming the concrete decision or action this page informs (e.g. "Prioritise regional marketing spend toward cities with declining sales").
  - confidence: "High", "Medium", or "Low" — how confident you are in the inferred purpose. Use "Low" whenever the page content is too generic to infer intent.
- navigation_guide: Short imperative steps (one sentence each) referencing the exact slicers, fields, and drill-through targets. Include cross-filtering behaviour only where it exists in the input.
- complex_visual_explainers: Only for genuinely non-obvious visuals (decomposition trees, key influencers, scatter, maps, gauges, waterfall, custom visuals). 2-3 sentences: how to read it and what to act on. Skip bar/line/card visuals.
""" + STYLE_RULES

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
                "required": ["page_title", "summary", "users",
                             "business_questions", "decisions", "confidence"],
                "properties": {
                    "page_title": {"type": "string"},
                    "summary": {"type": "string"},
                    "users": {"type": "string"},
                    "business_questions": {"type": "array", "items": {"type": "string"}},
                    "decisions": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
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


# Batch cap for the Business Analyst prompt's per-page work. Mirrors
# ``dax_translator_batches`` below: a bad/invalid response degrades only the
# pages in its own batch, not the whole document's narrative (1.4).
BUSINESS_ANALYST_BATCH_SIZE = 6


def business_analyst_batches(model: SemanticModel, batch_size: int = BUSINESS_ANALYST_BATCH_SIZE) -> list[dict]:
    """Split the report's *visible* pages into bounded batches, each shaped
    like :func:`business_analyst_input`'s return value (same report-wide
    ``tables``/``key_measures`` context, a subset of ``pages``). Hidden pages
    are never sent — the system prompt only asks for a summary "for EVERY
    visible page", so a batch boundary always lines up exactly with a
    contiguous slice of the deterministic visible-pages list callers already
    have, letting a failed batch be mapped straight back to the pages it
    covers."""
    base = business_analyst_input(model)
    visible_pages = [p for p in base["pages"] if not p["is_hidden"]]
    if not visible_pages:
        return [base]
    return [
        {**base, "pages": visible_pages[i:i + batch_size]}
        for i in range(0, len(visible_pages), batch_size)
    ]


# --------------------------------------------------------------------------
# DAX Translator Agent  (-> Measure Catalog plain-English, §V)
# --------------------------------------------------------------------------
DAX_TRANSLATOR_SYSTEM = """\
You are a senior DAX developer documenting measures for an enterprise data dictionary read by both analysts and business stakeholders. For each measure you receive its name, home table, DAX expression, and format string.

For each measure, populate:
- plain_english: the business definition, 1-2 sentences. What the number means to the business and how to interpret it (e.g. "Total invoiced revenue for the period in view, before refunds."). Do not describe the DAX here.
- calculation_logic: 1-2 sentences on how it is computed, in plain terms (e.g. "Sums Sale_Value over Orders, restricted to the year selected on the Date slicer."). Do not repeat the business definition.
- caveats: filters, exclusions, time-intelligence behaviour, division-by-zero handling, or grain dependencies — stated as facts read from the DAX (e.g. "Excludes orders with Status = 'Canceled'; returns blank when the denominator is zero."). Empty string if none.
- category: One of: Revenue, Cost, Ratio, Count, Time-Intelligence, Ranking, Text, Aggregation, Other.
- confidence: "High", "Medium", or "Low" — confidence in the inferred BUSINESS meaning (the calculation logic is read from the DAX and is not what this rates). Use "Low" when the name and expression do not make the business intent clear; in that case plain_english must say "Business meaning could not be inferred automatically; requires business confirmation." rather than a guess.

Return a translation for every measure in the input, keyed by its exact name.
""" + STYLE_RULES

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
                "required": ["name", "plain_english", "calculation_logic",
                             "caveats", "category", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "plain_english": {"type": "string"},
                    "calculation_logic": {"type": "string"},
                    "caveats": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["Revenue", "Cost", "Ratio", "Count",
                                 "Time-Intelligence", "Ranking", "Text",
                                 "Aggregation", "Other"],
                    },
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
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
You are an enterprise data-modeling architect. You receive the table metadata (fact/dimension classification) and relationship definitions (cardinality, cross-filter direction, active flags) of a Power BI semantic model. Write the Data Model section of a handover document for the BI developer who inherits it.

Your response must include:
- summary: 3-5 sentences. Name the schema shape (star, snowflake, galaxy, flat), the fact table(s) and their grain if inferable, the key dimensions, and anything a maintainer must know about filter propagation. Facts only — no praise of the design, no generic architecture prose.
- risks: One item per concrete modeling risk found (bi-directional filters, inactive relationships, many-to-many, disconnected tables, missing relationships). Each item: the specific objects involved, the failure it can cause, and the standard mitigation, in 1-2 sentences (e.g. "Sales ↔ Targets is many-to-many; totals can double-count — add a bridge table on Region key."). Empty list if none.
""" + STYLE_RULES

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
You are writing the data dictionary of an enterprise Power BI handover document. For each column, write ONE sentence stating what the field stores and its primary use (key for joins, dimension for filtering/grouping, or value for aggregation). Example: "Order ship date; used for delivery-lag analysis and time-based filtering."

Rules:
- Maximum one sentence, no padding such as "This column stores..." — start with the content.
- Never write "potentially used for", "possibly", or "likely". If the meaning cannot be determined from the name, type, and table, write exactly: "Unknown — requires business confirmation."
- Do not fabricate business intent.

Return a description for every column in the input.
""" + STYLE_RULES

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
""" + STYLE_RULES

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
You are a senior BI consultant writing an executive summary for managers and project owners who will skim it in under two minutes. You are given deterministic drafts already computed for this report — a business purpose, key KPIs, model/report statistics, known risks, and a maintenance note — and you compress them into decision-focused executive prose. The three fields together must total UNDER 250 words; executives do not read long paragraphs.

Populate exactly these three fields:
- business_purpose: 2-3 sentences: the business problem this report addresses, its main KPIs, its primary users, and the decisions it supports. No table names, no DAX, no "semantic model".
- business_value: 1-2 sentences on the concrete value delivered (decisions enabled, time saved, risk reduced) — grounded strictly in the KPIs and purpose given, not generic praise.
- maintenance_overview: 1-2 sentences: the known risks that matter and what it takes to keep the report healthy, in plain language.

Do not invent facts, statistics, or risks beyond what is given. Do not restate every input number — synthesize.
""" + STYLE_RULES

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

Rewrite them into plain, direct English that:
- Never uses the words "table", "DAX", "semantic model", "measure" (say "metric" or name it directly), "column", "relationship", or "query" — describe what the user sees and does, not how the report is built.
- For each page, keeps the purpose to 2-3 sentences: what this page is for and what questions it helps answer.
- Writes common scenarios as role-based tasks where a role is inferable from the page content, e.g. "Sales manager: compare this month's revenue across regions before the Monday review" or "Operations: check which restaurants are behind on order volume." Where no role is inferable, use "Use this page when you want to..." phrasing. Ground every scenario strictly in the fields and filters given — never invented.
- For the introduction, writes 2-3 sentences that orient a first-time user: what the report covers and where to start.

Do not invent pages, fields, roles, or workflows beyond what is given in the drafts.
""" + STYLE_RULES

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


# Batch cap mirroring ``business_analyst_batches`` — a failed/invalid batch
# only degrades the pages in that batch, not the whole guide (1.4).
USER_GUIDE_WRITER_BATCH_SIZE = 8


def user_guide_writer_batches(
    report_name: str, introduction_draft: str, pages: list[dict],
    batch_size: int = USER_GUIDE_WRITER_BATCH_SIZE,
) -> list[dict]:
    """Split ``pages`` (each ``{page_title, purpose_draft, common_scenarios_draft}``)
    into bounded batches, each shaped like :func:`user_guide_writer_input`'s
    return value."""
    if not pages:
        return [user_guide_writer_input(report_name, introduction_draft, [])]
    return [
        user_guide_writer_input(report_name, introduction_draft, pages[i:i + batch_size])
        for i in range(0, len(pages), batch_size)
    ]
