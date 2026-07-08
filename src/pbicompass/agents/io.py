"""Agent I/O contracts: system prompts, JSON-schema output formats, and the
fact-builders that turn a ``SemanticModel`` into the compact JSON each agent
receives.

The JSON schemas are used both for the Anthropic structured-output
``output_config.format`` and as the validation target when parsing responses.
They follow the structured-output constraints (every object sets
``additionalProperties: false`` and lists its ``required`` keys).
"""

from __future__ import annotations

from typing import Optional

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
- NEVER guess business intent. Do not hedge with "possibly", "likely", or "potentially". When you cannot infer the deeper business meaning, state whatever structural fact you do know (the field's type, its table, a relationship/join role) instead of only punting — write "Purpose could not be inferred automatically; requires business confirmation." only when no structural fact is available either.
- Never invent pages, visuals, fields, measures, or workflows that are not present in the input.
"""

# --------------------------------------------------------------------------
# Per-agent effort tiers (Phase 0): resolved by ``agents/generators/base.py``
# from the ``name`` each call site already passes to ``call_llm``/
# ``call_llm_with_retry``, keyed to how much reasoning the task actually
# needs — extraction/style-check agents stay cheap, synthesis/prose agents
# get the deeper (and costlier) tiers. An explicit ``effort=`` kwarg at the
# call site always wins over this map; an agent absent from the map falls
# back to the client's own default (``None``).
# --------------------------------------------------------------------------
AGENT_EFFORT: dict[str, str] = {
    "Column Describer": "low",
    "Critic": "low",
    "DAX Translator": "medium",
    "User Guide Writer": "medium",
    "Audit Narrator": "medium",
    # Day 7 (AI-Native Phase 4): cross-finding root-cause synthesis — same
    # reasoning depth as the other whole-model synthesis agents below.
    "Audit Synthesizer": "high",
    # Day 9 (AI-Native Phase 4, paid): concrete DAX/M code sketches need the
    # same depth as the synthesis agents — getting the syntax wrong is worse
    # than a vague sentence would have been.
    "AI Fix Snippet Writer": "high",
    "Business Analyst": "high",
    "Data Modeler": "high",
    "Executive Writer": "high",
    # Phase 2: the one whole-model synthesis call per job — the deepest
    # reasoning tier since every other document's quality now leans on it.
    "Report Intelligence": "xhigh",
    # Phase 3: one fact-verification call per document — checking claims
    # against an already-built digest needs less depth than writing the
    # narrative in the first place.
    "Grounding": "medium",
}

# --------------------------------------------------------------------------
# Phase 2: appended to every prompt whose input builder accepts a
# ``report_context`` (the slimmed ``JobAIContext.insights`` produced by the
# Report Intelligence pass — see ``agents/insights.py``). Kept as one shared
# paragraph so every consuming prompt states the same ground rule: use it for
# depth/consistency, never let it override or get echoed verbatim over the
# concrete metadata the rest of the payload already carries.
# --------------------------------------------------------------------------
REPORT_CONTEXT_NOTE = """
You may also be given report_context — a synthesized understanding of the whole report produced by a separate whole-model reasoning pass (business domain, purpose, page workflows, entity definitions, KPI relationships). Use it to write with more depth and consistency with the rest of the report. Never contradict the concrete tables/measures/pages given elsewhere in this input, and never copy report_context's wording verbatim — restate its substance in your own words for this specific field.
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
""" + REPORT_CONTEXT_NOTE + STYLE_RULES

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


def business_analyst_input(model: SemanticModel, report_context: Optional[dict] = None) -> dict:
    key_measures = [m.name for m in model.all_measures() if not m.is_hidden][:25]
    payload = {
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
    if report_context is not None:
        payload["report_context"] = report_context
    return payload


# Batch cap for the Business Analyst prompt's per-page work. Mirrors
# ``dax_translator_batches`` below: a bad/invalid response degrades only the
# pages in its own batch, not the whole document's narrative (1.4).
BUSINESS_ANALYST_BATCH_SIZE = 6


def business_analyst_batches(
    model: SemanticModel, batch_size: int = BUSINESS_ANALYST_BATCH_SIZE,
    report_context: Optional[dict] = None,
) -> list[dict]:
    """Split the report's *visible* pages into bounded batches, each shaped
    like :func:`business_analyst_input`'s return value (same report-wide
    ``tables``/``key_measures`` context, a subset of ``pages``). Hidden pages
    are never sent — the system prompt only asks for a summary "for EVERY
    visible page", so a batch boundary always lines up exactly with a
    contiguous slice of the deterministic visible-pages list callers already
    have, letting a failed batch be mapped straight back to the pages it
    covers."""
    base = business_analyst_input(model, report_context=report_context)
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
- confidence: "High", "Medium", or "Low" — confidence in the inferred BUSINESS meaning (the calculation logic is read from the DAX and is not what this rates). Use "Low" when the name and expression do not make the business intent clear; in that case plain_english should still state what the DAX computes in plain terms (category, aggregation, filters applied) and note that the business meaning requires confirmation — never a bare punt sentence with no information in it.

Return a translation for every measure in the input, keyed by its exact name.
""" + REPORT_CONTEXT_NOTE + STYLE_RULES

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


def dax_translator_input(model: SemanticModel, report_context: Optional[dict] = None) -> dict:
    payload = {
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
    if report_context is not None:
        payload["report_context"] = report_context
    return payload


# Batch caps for the DAX Translator prompt. A real-world model can have
# hundreds of measures with long DAX expressions; packing them all into one
# call makes the prompt (and the response) huge and slow, risking a timeout
# on the whole job. Each batch stays under both a measure-count and a total
# DAX-length budget.
DAX_TRANSLATOR_BATCH_SIZE = 25
DAX_TRANSLATOR_BATCH_CHARS = 8000


def dax_translator_batches(model: SemanticModel, report_context: Optional[dict] = None) -> list[dict]:
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
    if report_context is not None:
        for b in batches:
            b["report_context"] = report_context
    return batches


# --------------------------------------------------------------------------
# Data Modeler Agent  (-> Semantic Model narrative, §IV)
# --------------------------------------------------------------------------
DATA_MODELER_SYSTEM = """\
You are an enterprise data-modeling architect. You receive the table metadata (fact/dimension classification) and relationship definitions (cardinality, cross-filter direction, active flags) of a Power BI semantic model. Write the Data Model section of a handover document for the BI developer who inherits it.

Your response must include:
- summary: 3-5 sentences. Name the schema shape (star, snowflake, galaxy, flat), the fact table(s) and their grain if inferable, the key dimensions, and anything a maintainer must know about filter propagation. Facts only — no praise of the design, no generic architecture prose.
- risks: One item per concrete modeling risk found (bi-directional filters, inactive relationships, many-to-many, disconnected tables, missing relationships). Each item: the specific objects involved, the failure it can cause, and the standard mitigation, in 1-2 sentences (e.g. "Sales ↔ Targets is many-to-many; totals can double-count — add a bridge table on Region key."). Empty list if none.
""" + REPORT_CONTEXT_NOTE + STYLE_RULES

DATA_MODELER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "risks"],
    "properties": {
        "summary": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}


def data_modeler_input(model: SemanticModel, report_context: Optional[dict] = None) -> dict:
    payload = {
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
    if report_context is not None:
        payload["report_context"] = report_context
    return payload


# --------------------------------------------------------------------------
# Column Describer Agent  (-> Data Dictionary descriptions, §IV)
# --------------------------------------------------------------------------
COLUMN_DESCRIBER_SYSTEM = """\
You are writing the data dictionary of an enterprise Power BI handover document. For each column, write ONE sentence stating what the field stores and its primary use (key for joins, dimension for filtering/grouping, or value for aggregation). Example: "Order ship date; used for delivery-lag analysis and time-based filtering."

Rules:
- Maximum one sentence, no padding such as "This column stores..." — start with the content.
- Never write "potentially used for", "possibly", or "likely". If the deeper business meaning cannot be determined from the name, type, and table, still state the structural fact you can see — its data type, its table, and whether it looks like a join key/identifier (e.g. "Numeric identifier in Department; likely a join key to a dimension table."). Only write exactly "Unknown — requires business confirmation." when no such structural fact is available either.
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
# Audit Synthesizer Agent  (AI-Native Phase 4 / Day 7 — -> AuditDocument
# clusters + strategic_narrative)
# --------------------------------------------------------------------------
AUDIT_SYNTHESIZER_SYSTEM = """\
You are a principal BI consultant performing root-cause synthesis across a model's already-computed audit findings — DAX findings, failed best-practice checks, performance-risk signals, governance findings, and unused-asset entries, each tagged with a stable rule_id and, where applicable, a table/object name.

Findings are reported today as an isolated, flat list. Your job is to spot findings that trace back to one underlying root cause and group them, so the reader sees the pattern and the one fix instead of a wall of disconnected line items. For example: Auto Date/Time being enabled can itself be a performance-risk finding, can make a star-schema check fail (its hidden local date tables get miscounted as extra fact tables), and can leave a batch of hidden calculated columns and tables reported as unused assets — all tracing back to one setting, all clearing together if it is disabled.

For each root cause you can support using only the rule_ids and table/object names given in the input (never invent a finding that is not present):
- Name the underlying root cause in one clear phrase.
- List every rule_id of a finding that belongs to this cluster (2 or more — a single finding is not a cluster).
- Write a 1-2 sentence narrative naming the root cause, what it explains, and the one fix that resolves the whole cluster.
- Rate your confidence (High/Medium/Low) that these findings genuinely share this cause rather than merely co-occurring.

If no findings share a clear, defensible root cause, return an empty clusters list — do not force a grouping.

Then write a strategic_narrative (2-4 sentences): the remediation story in priority order — which root cause (or, absent any cluster, which single finding) to fix first and why.
""" + STYLE_RULES

AUDIT_SYNTHESIZER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clusters", "strategic_narrative"],
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["root_cause", "rule_ids", "narrative", "confidence"],
                "properties": {
                    "root_cause": {"type": "string"},
                    "rule_ids": {"type": "array", "items": {"type": "string"}},
                    "narrative": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["High", "Medium", "Low"]},
                },
            },
        },
        "strategic_narrative": {"type": "string"},
    },
}


def audit_synthesizer_input(
    dax_findings: list[dict],
    failed_best_practices: list[dict],
    performance_risks: list[dict],
    governance: list[dict],
    unused_assets_summary: dict,
) -> dict:
    return {
        "dax_findings": dax_findings,
        "failed_best_practices": failed_best_practices,
        "performance_risks": performance_risks,
        "governance_findings": governance,
        "unused_assets_summary": unused_assets_summary,
    }


# --------------------------------------------------------------------------
# AI Fix Snippet Writer  (AI-Native Phase 4 / Day 9 — paid feature: a
# concrete DAX/M/Tabular-Editor-script sketch appended to a recommendation
# that today only carries prose, never one that already has a deterministic
# code fix). Every snippet is explicitly labelled "AI-suggested — review
# before applying" by the caller, never presented as a verified fix.
# --------------------------------------------------------------------------
AI_FIX_SNIPPET_SYSTEM = """\
You are a senior Power BI / DAX consultant writing short, concrete code sketches that resolve specific audit recommendations. For each recommendation you receive its issue, why it matters, its existing prose-only suggested fix, its category, and (when available) the real object name(s) from the model it concerns.

For each recommendation, write ONE short code sketch (at most ~15 lines) that a modeler could adapt directly:
- category "dax" -> a DAX measure/expression sketch (language "dax").
- category "performance" -> a DAX or Power Query sketch addressing the specific object (language "dax" or "m", whichever fits).
- any other category -> a short Tabular Editor C# script or Power Query snippet if one genuinely helps (language "csharp" or "m"); otherwise language "text" with 2-3 concrete steps, not prose repeating the existing suggested fix.

Rules:
- Reference the real object name(s) given for that recommendation verbatim when any are given — never invent a table/measure/column name that was not given to you.
- Do not repeat the existing suggested_fix prose; add the concrete "how", not another restatement of "what"/"why".
- If you cannot produce anything more concrete than the existing suggested fix already is, omit that recommendation from your response entirely rather than padding it.
- Never include instructions to the reader about this task itself (no "consider providing", "verify", "note that this is AI-generated" — the caller already labels it as AI-suggested).
""" + STYLE_RULES

AI_FIX_SNIPPET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["snippets"],
    "properties": {
        "snippets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_id", "language", "code"],
                "properties": {
                    "rule_id": {"type": "string", "description": "Copied verbatim from the input item."},
                    "language": {"type": "string", "enum": ["dax", "m", "csharp", "text"]},
                    "code": {"type": "string"},
                },
            },
        },
    },
}


def ai_fix_snippet_input(items: list[dict]) -> dict:
    return {"recommendations": items}


# --------------------------------------------------------------------------
# Executive Writer Agent  (-> Executive Summary narrative prose)
# --------------------------------------------------------------------------
EXEC_STYLE_RULES = """
This document goes to executives and business owners, never to IT/audit staff. In every field you write (including reframed risks):
- BANNED vocabulary: "governance finding(s)", "best practice(s)", "audit", "compliance", "gap(s)", "% complete", "fields still need", "modeling risk(s)". Say what the actual business impact is instead (e.g. not "a governance finding" but "report access isn't restricted by role").
- Phrase every risk as a business consequence ("what happens if this isn't fixed") followed by a plain-English ask of a named role ("ask your BI team to ..."), never as an audit-log entry.
- Never mention document completeness, missing metadata fields, or internal review percentages — that belongs in an internal job log, not this document.
"""

EXECUTIVE_WRITER_SYSTEM = """\
You are a senior BI consultant writing an executive summary for managers and project owners who will skim it in under two minutes. You are given deterministic drafts already computed for this report — a business purpose, key KPIs, model/report statistics, known risks, and a maintenance note — and you compress them into decision-focused executive prose. The three prose fields together must total UNDER 250 words; executives do not read long paragraphs.

Populate exactly these fields:
- business_purpose: 2-3 sentences: the business problem this report addresses, its main KPIs, its primary users, and the decisions it supports. No table names, no DAX, no "semantic model".
- business_value: 1-2 sentences on the concrete value delivered (decisions enabled, time saved, risk reduced) — grounded strictly in the KPIs and purpose given, not generic praise.
- maintenance_overview: 1-2 sentences: the known risks that matter and what it takes to keep the report healthy, in plain language.
- reframed_risks: for every entry in known_risks, return one item that echoes back its rule_id exactly (or "" if it had none) and rewrites its consequence and ask in plain business language per the rules below. Same order and count as known_risks — never add, drop, or merge entries.

Do not invent facts, statistics, or risks beyond what is given. Do not restate every input number — synthesize.
""" + REPORT_CONTEXT_NOTE + STYLE_RULES + EXEC_STYLE_RULES

EXECUTIVE_WRITER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["business_purpose", "business_value", "maintenance_overview", "reframed_risks"],
    "properties": {
        "business_purpose": {"type": "string"},
        "business_value": {"type": "string"},
        "maintenance_overview": {"type": "string"},
        "reframed_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_id", "consequence", "ask"],
                "properties": {
                    "rule_id": {"type": "string"},
                    "consequence": {"type": "string"},
                    "ask": {"type": "string"},
                },
            },
        },
    },
}


def executive_writer_input(
    business_purpose_draft: str,
    key_kpis: list[str],
    model_statistics: dict[str, int],
    report_statistics: dict[str, int],
    known_risks: list[dict[str, str]],
    maintenance_draft: str,
    report_context: Optional[dict] = None,
) -> dict:
    payload = {
        "business_purpose_draft": business_purpose_draft,
        "key_kpis": key_kpis,
        "model_statistics": model_statistics,
        "report_statistics": report_statistics,
        "known_risks": known_risks,
        "maintenance_draft": maintenance_draft,
    }
    if report_context is not None:
        payload["report_context"] = report_context
    return payload


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
""" + REPORT_CONTEXT_NOTE + STYLE_RULES

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
    report_context: Optional[dict] = None,
) -> dict:
    payload = {
        "report_name": report_name,
        "introduction_draft": introduction_draft,
        "pages": pages,
    }
    if report_context is not None:
        payload["report_context"] = report_context
    return payload


# Batch cap mirroring ``business_analyst_batches`` — a failed/invalid batch
# only degrades the pages in that batch, not the whole guide (1.4).
USER_GUIDE_WRITER_BATCH_SIZE = 8


def user_guide_writer_batches(
    report_name: str, introduction_draft: str, pages: list[dict],
    batch_size: int = USER_GUIDE_WRITER_BATCH_SIZE,
    report_context: Optional[dict] = None,
) -> list[dict]:
    """Split ``pages`` (each ``{page_title, purpose_draft, common_scenarios_draft}``)
    into bounded batches, each shaped like :func:`user_guide_writer_input`'s
    return value."""
    if not pages:
        return [user_guide_writer_input(report_name, introduction_draft, [], report_context=report_context)]
    return [
        user_guide_writer_input(report_name, introduction_draft, pages[i:i + batch_size],
                                 report_context=report_context)
        for i in range(0, len(pages), batch_size)
    ]
