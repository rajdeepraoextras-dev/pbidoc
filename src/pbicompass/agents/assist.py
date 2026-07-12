"""Two lightweight, single-field helpers behind the intake form's Notes tab
(``/app/api/assist/fill`` and ``/app/api/assist/format`` in ``service/app.py``):
"AI Fill" drafts one field from the report's own structure, "Format" cleans up
whatever the user already typed. Deliberately not part of the document
generators in ``agents/generators/`` — these run before a job even exists, on
demand, one field at a time, and always over a fixed engine (MeshAPI) rather
than whatever the caller's chosen job engine is, per product decision: a
free-form drafting aid should be cheap and available regardless of which
engine the eventual job uses.

The anti-fabrication rule below exists because the report file only describes
the *model* (tables, measures, roles, refresh mode) -- it knows nothing about
deployment, people, or process. A field like "Support Escalation & Maintenance
Policy" has no structural answer in the file at all; asking an LLM to fill it
anyway invites confident-sounding invented contacts/SLAs ("fiction"). The
system prompt below draws that line explicitly instead of leaving it to the
model's own judgement.
"""

from __future__ import annotations

import json
from typing import Optional

from .llm import LLMClient
from ..schemas.model import SemanticModel

# Field key -> the exact label/placeholder copy already shown in the intake
# form (app.html's Notes tab) -- kept in lockstep with that copy so the
# prompt asks for exactly what the form's own UI promises the field holds.
ASSIST_FIELDS: dict[str, dict[str, str]] = {
    "business_decision": {
        "label": "Primary Business Decision / Impact",
        "guidance": "What key business decision or operational process does this report drive?",
    },
    "requirements": {
        "label": "Business Requirements",
        "guidance": "The business requirements / KPI definitions this report satisfies, one per line.",
    },
    "security_notes": {
        "label": "Security & RLS Validation Notes",
        "guidance": "Notes on RLS/OLS validation methods, tested roles, and active rules.",
    },
    "refresh_notes": {
        "label": "Gateway, Latency & Refresh Details",
        "guidance": "Gateway configuration, scheduled refresh intervals, and typical refresh runtime.",
    },
    "deployment_notes": {
        "label": "Workspaces & App Deployment",
        "guidance": "Environments (Dev, Test, Prod), app workspace links, and deployment pipelines.",
    },
    "access_notes": {
        "label": "Permissions & Workspace Access Control",
        "guidance": "Active user groups, workspace roles, and app permissions.",
    },
    "glossary": {
        "label": "Glossary of Key Business Terms",
        "guidance": "Definitions of specific calculations, acronyms, or business rules used in the report.",
    },
    "assumptions": {
        "label": "Business Assumptions & Data Limitations",
        "guidance": "Constraints such as data latency, currency conversion, or excluded divisions.",
    },
    "support_notes": {
        "label": "Support Escalation & Maintenance Policy",
        "guidance": "First-line support contacts, SLA policies, and code backup practices.",
    },
}

ASSIST_TEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {"text": {"type": "string"}},
}

ASSIST_FILL_SYSTEM = """You are a senior BI consultant helping a colleague fill in one field of a Power BI report intake form. This form's answers feed directly into an AI-generated handover document, so accuracy matters more than completeness.

You are given:
- report_facts: structural facts extracted directly from the Power BI report file (tables, measures, pages, security roles, data source types, refresh/partition modes). Treat this as ground truth about the model -- but it says nothing about deployment, operations, or people.
- form_context: whatever the user has already typed into the other fields of this same form.
- field_label / field_guidance: the exact field you are drafting now.
- current_text: whatever is already typed into this field, if anything -- refine or extend it rather than discarding it outright when it is already substantive.

Write the content for field_label only. Rules:
- Ground every concrete claim in report_facts or form_context. Listing real role names and the tables they filter, real measure/table names, real page titles is exactly what is wanted.
- NEVER invent operational or organizational facts that cannot come from a report file: named people, ticket systems, SLA hours, gateway machine names, environment URLs, deployment pipeline tool names, dates. If the field asks for this kind of detail and neither report_facts nor form_context supplies it, do not fabricate specifics -- instead write 2-3 sentences of concrete guidance addressed to the user on what to record here (e.g. "Record the gateway's data source name and the on-premises gateway cluster it runs on, plus the scheduled refresh times."), never asserted as if already true.
- Do not pad, do not use marketing language, do not restate the field's own label back as a sentence.
- Plain prose, or a short line-per-item list when field_guidance itself describes a list (e.g. "one per line") -- no markdown headers or bullet characters.
- 40-120 words, unless the field is naturally a short list.

Return only the field's text."""

ASSIST_FORMAT_SYSTEM = """Fix the grammar, spelling, capitalization, and punctuation of the given text. Preserve its exact meaning and every fact -- do not add, remove, or reinterpret information, and do not add new sentences. Do not change technical terms, product names, or proper nouns. Preserve existing line breaks: each input line stays its own output line. Return only the corrected text, nothing else."""


def build_report_summary(model: SemanticModel) -> dict:
    """Compact, structural-only facts about ``model`` for the fill prompt --
    no row data (the app never reads any), no connection strings, no M/DAX
    source text beyond a measure's own name/table, matching the "metadata
    only" claim already made on the intake form itself."""
    key_measures = [
        {"name": m.name, "table": m.table}
        for m in model.all_measures() if not m.is_hidden
    ][:60]
    return {
        "report_name": model.report_name,
        "tables": [{"name": t.name, "kind": t.kind} for t in model.tables if not t.is_hidden],
        "key_measures": key_measures,
        "pages": [p.display_name for p in model.pages if not p.is_hidden],
        "roles": [
            {
                "name": r.name,
                "model_permission": r.model_permission,
                "filtered_tables": [tp.table for tp in r.table_permissions],
            }
            for r in model.roles
        ],
        "partition_modes": sorted({
            p.mode for t in model.tables for p in t.partitions if p.mode
        }),
        "data_source_types": sorted({d.type for d in model.data_sources if d.type}),
    }


def fill_field(
    client: LLMClient, field: str, report_facts: dict,
    form_context: dict, current_text: Optional[str] = None,
) -> str:
    """Draft ``field``'s content from ``report_facts`` (see
    :func:`build_report_summary`) and whatever else the user has already
    typed elsewhere on the form. Raises on any client failure -- the caller
    (the ``/app/api/assist/fill`` route) turns that into a 502."""
    meta = ASSIST_FIELDS[field]
    payload: dict = {
        "field_label": meta["label"],
        "field_guidance": meta["guidance"],
        "report_facts": report_facts,
        "form_context": {k: v for k, v in form_context.items() if v},
    }
    if current_text and current_text.strip():
        payload["current_text"] = current_text.strip()
    result = client.complete_json(
        ASSIST_FILL_SYSTEM, json.dumps(payload, ensure_ascii=False),
        ASSIST_TEXT_SCHEMA, effort="medium",
    )
    return (result or {}).get("text", "").strip()


def format_text(client: LLMClient, text: str) -> str:
    """Grammar/punctuation cleanup of ``text``, meaning otherwise untouched."""
    result = client.complete_json(
        ASSIST_FORMAT_SYSTEM, json.dumps({"text": text}, ensure_ascii=False),
        ASSIST_TEXT_SCHEMA, effort="low",
    )
    return (result or {}).get("text", "").strip()
