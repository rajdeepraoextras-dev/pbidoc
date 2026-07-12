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
- ``unverifiable``  — replaced with the model's own ``correction`` too, when
  it provides one: a rewrite that keeps the sentence's point but drops or
  softens only the part that overreaches beyond the digest, same as
  ``contradicted``. The "Unknown — requires business confirmation."
  convention is now a *last resort*, used only when the model returns no
  usable correction — this mirrors the same "AI may only improve, never
  downgrade" rule the Column Describer already follows (D6): a claim that's
  merely uncheckable against a structural digest (most business
  interpretation — "helps leadership prioritize budget cuts" — is
  inherently uncheckable that way) is not the same as a claim being wrong,
  and shouldn't be nuked to a canned non-answer by default. When the hard
  punt is used and *the claim runs to the end of its sentence*, a plain
  inline substitution reads fine; when the claim is an internal clause
  (more sentence follows after it), the whole sentence is dropped instead
  (see ``_replace_unverifiable_claim`` — this is the D3 fix: substituting
  an already-punctuated sentence fragment mid-clause produced nonsense like
  "However, Unknown — requires business confirmation., are aspects that
  need attention...").

Offline (``client is None``), no digest available, or a failed call all
degrade to a no-op — the regex bracket-name check in ``critic.py`` remains
the deterministic floor regardless.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Optional

from .generators.base import call_llm
from .llm import LLMClient
from .sanitize import is_punt_phrase

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

For each field, identify only concrete, checkable factual claims — a count, a relationship, a \
named table/measure/page, a data source, or a business fact tied to a specific named object in \
the digest. Do NOT flag general business interpretation, benefits, consequences, or \
recommendations ("helps leadership prioritize budget cuts", "enables faster decisions", "should \
be addressed to reduce risk") as claims to verify — those are framing, not checkable facts, and \
are not errors; leave them untouched. Do not flag stylistic or subjective phrasing either — only \
claims that are checkable against the digest's concrete facts.

For each claim, report:
- location: the field's location label, copied from the input.
- quote: the exact substring of the field's text that makes the claim (must appear verbatim in that field).
- verdict: "supported" if the digest confirms it, "contradicted" if the digest states something that conflicts with it, "unverifiable" if the digest neither confirms nor conflicts with it (the claim reaches beyond what the digest can check).
- correction: for "contradicted", the corrected wording grounded in what the digest actually says, phrased to fit naturally in place of the quote. For "unverifiable", a rewrite of the quote that keeps its point and stays specific where possible, but drops or softens only the part that overreaches beyond what the digest can confirm — you are smart enough to salvage nearly every claim this way, so only return an empty string here if truly nothing in the quote can be kept. Empty string always for "supported".

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


# Splits text into sentences, each retaining its own terminal punctuation
# and trailing whitespace, so the pieces can be rejoined losslessly.
_SENTENCE_RE = re.compile(r'[^.!?]*[.!?]+(?:\s+|$)')
# A sentence remainder consisting only of closing quotes/punctuation and
# whitespace: the claim it follows effectively ends the sentence, so an
# inline substitution there reads fine grammatically.
_SENTENCE_END_REMAINDER_RE = re.compile(r"""^[.!?"'’”)\]]*\s*$""")


def _split_sentences(text: str) -> list[str]:
    sentences = _SENTENCE_RE.findall(text)
    consumed = "".join(sentences)
    if len(consumed) < len(text):
        sentences.append(text[len(consumed):])
    return [s for s in sentences if s]


def _replace_unverifiable_claim(current: str, quote: str) -> str:
    """Apply an ``unverifiable`` verdict for ``quote`` inside ``current``.

    If ``quote`` runs to the end of its sentence, a plain inline
    substitution with :data:`UNVERIFIABLE_TEXT` reads fine. If it's an
    internal clause — more sentence text follows it — splicing the
    already-punctuated ``UNVERIFIABLE_TEXT`` in place would break grammar
    (D3), so the whole sentence is dropped instead. Falls back to a plain
    inline substitution if ``quote`` can't be located within a sentence
    (defensive; callers already check ``quote in current``)."""
    idx = current.find(quote)
    if idx == -1:
        return current.replace(quote, UNVERIFIABLE_TEXT)

    sentences = _split_sentences(current)
    pos = 0
    for i, sentence in enumerate(sentences):
        if pos <= idx < pos + len(sentence):
            quote_end = (idx - pos) + len(quote)
            remainder = sentence[quote_end:]
            if _SENTENCE_END_REMAINDER_RE.match(remainder):
                return current.replace(quote, UNVERIFIABLE_TEXT)
            remaining = "".join(s for j, s in enumerate(sentences) if j != i).strip()
            return remaining if remaining else UNVERIFIABLE_TEXT
        pos += len(sentence)
    return current.replace(quote, UNVERIFIABLE_TEXT)


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
            correction = (claim.get("correction") or "").strip()
            if correction and not is_punt_phrase(correction):
                # Prefer the model's own rewrite over the canned punt — same
                # "improve, never downgrade to a non-answer" rule the Column
                # Describer already follows (D6).
                results[location] = current.replace(quote, correction)
                warn(f"{location}: grounding pass softened an unverifiable claim.")
            else:
                results[location] = _replace_unverifiable_claim(current, quote)
                warn(f"{location}: grounding pass flagged an unverifiable claim.")
    return results
