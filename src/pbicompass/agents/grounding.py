"""The grounding & verification pass (Phase 3 of ``AI_NATIVE_PLAN.md``): the
trust layer on top of the critic pass (5.3).

The critic (``agents/critic.py``) only judges *style* — banned words,
name-echo prose, an unknown-bracketed-name warning it can't itself fix.
Nothing before this phase actually checks whether a narrative sentence is
*true* against the model. This module runs one call per generated document,
after the critic pass, feeding the document's own labelled narrative fields
(the same triple mechanism ``critic.py`` already uses) alongside Phase 2's
whole-model digest (``JobAIContext.model_digest``), and asks the LLM to
verify each factual claim against it:

- ``supported``    — left untouched.
- ``contradicted``  — replaced with the model's own ``correction``.
- ``unverifiable``  — replaced with the established
  "Unknown — requires business confirmation." convention (never a guess).

Offline (``client is None``), no digest available, or a failed call all
degrade to a no-op — the regex bracket-name check in ``critic.py`` remains
the deterministic floor regardless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from .generators.base import call_llm
from .llm import LLMClient

if TYPE_CHECKING:
    from .context import JobAIContext

# The established convention used elsewhere (Column Describer's fallback,
# DAX Translator's low-confidence business meaning) for a claim that cannot
# be confirmed — never a guess, never silently dropped.
UNVERIFIABLE_TEXT = "Unknown — requires business confirmation."

GROUNDING_SYSTEM = """\
You are a fact-checker verifying claims in an enterprise Power BI handover document against \
the report's own metadata. You receive labelled narrative fields already written for this \
document, and a digest of the whole model (every table/column, measure/DAX, relationship, \
page, RLS role, data source, and audit finding counts).

For each field, identify every concrete factual claim it makes about the report (a count, a \
relationship, a named table/measure/page, a data source, a business fact tied to a specific \
object) and verify it against the digest. Do not flag stylistic or subjective phrasing — only \
claims that are checkable against the digest's concrete facts.

For each claim, report:
- location: the field's location label, copied from the input.
- quote: the exact substring of the field's text that makes the claim (must appear verbatim in that field).
- verdict: "supported" if the digest confirms it, "contradicted" if the digest states something that conflicts with it, "unverifiable" if the digest neither confirms nor conflicts with it (the claim reaches beyond what the digest can check).
- correction: for "contradicted" only, the corrected wording grounded in what the digest actually says, phrased to fit naturally in place of the quote. Empty string for "supported"/"unverifiable".

Only report claims you are actually checking — most fields will have few or none. Never invent a table, measure, page, or relationship not present in the digest. Never flag a claim as contradicted or unverifiable just because the digest doesn't repeat it word-for-word — a claim is supported whenever the digest's facts are consistent with it.
"""

GROUNDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["location", "quote", "verdict", "correction"],
                "properties": {
                    "location": {"type": "string"},
                    "quote": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["supported", "contradicted", "unverifiable"]},
                    "correction": {"type": "string"},
                },
            },
        },
    },
}


def grounding_input(fields: dict[str, str], model_digest: str) -> dict:
    return {"fields": fields, "model_digest": model_digest}


def apply_grounding_pass(
    fields: list[tuple[str, str]],
    client: Optional[LLMClient],
    *,
    model_digest: Optional[str],
    warn: Optional[Callable[[str], None]] = None,
    ai_context: Optional["JobAIContext"] = None,
) -> dict[str, str]:
    """Run the grounding pass over ``[(location, text), ...]``. Returns
    ``{location: corrected_text}`` for every location whose text changed —
    mirrors ``critic.py::apply_critic_pass``'s contract exactly so callers
    can feed the result straight into the same ``apply_results``.

    A no-op (returns ``{}``) when offline, when ``model_digest`` is missing
    (no shared job context was available to this generator), or on any call
    failure — grounding is a quality layer on top of an already-complete
    document, never a requirement for one."""
    warn = warn or (lambda _msg: None)
    if client is None or not model_digest:
        return {}

    working = {location: text for location, text in fields if text and "```" not in text}
    if not working:
        return {}

    try:
        response = call_llm(
            client, GROUNDING_SYSTEM, grounding_input(working, model_digest), GROUNDING_SCHEMA,
            warn, "Grounding", ai_context=ai_context,
        )
    except Exception as exc:  # pragma: no cover - defensive, mirrors call_llm's own contract
        warn(f"Grounding: LLM call failed, skipping verification pass ({exc})")
        return {}
    if not response:
        return {}

    results: dict[str, str] = {}
    for claim in response.get("claims", []):
        location = (claim.get("location") or "").strip()
        quote = (claim.get("quote") or "").strip()
        verdict = claim.get("verdict")
        if not location or not quote or location not in working:
            continue
        current = results.get(location, working[location])
        if quote not in current:
            continue
        if verdict == "contradicted":
            correction = (claim.get("correction") or "").strip()
            if correction:
                results[location] = current.replace(quote, correction)
                warn(f"{location}: grounding pass corrected a contradicted claim.")
        elif verdict == "unverifiable":
            results[location] = current.replace(quote, UNVERIFIABLE_TEXT)
            warn(f"{location}: grounding pass flagged an unverifiable claim.")
    return results
