"""The critic pass (5.3): a post-generation quality gate over a document's
own narrative prose fields.

Deterministic checks (banned marketing words, back-to-back duplicate
sentences) always run first, in pure Python, and are auto-fixed directly —
no LLM needed. A name-existence check flags prose that references a bracketed
object name (``[Something]``) not found in the model; it can't be
auto-fixed (the critic doesn't know the *right* name), so it's only ever
surfaced as a warning. Only after that does one LLM call per document (cheap
model/low effort) judge the subtler style rules — name-echo prose, generic
filler — that pure-Python rules can't catch. Skipped silently when offline
(``client is None``).

Callers collect their own ``(location, text, setter)`` triples from whichever
document schema they own (technical/executive/user-guide/audit each have a
different shape) — this module stays doc-shape-agnostic and only deals in
labelled text.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .generators.base import call_llm
from .llm import LLMClient

STYLE_RULES = """
Editorial guidelines for clean enterprise documentation:
1. Avoid banned marketing buzzwords: revolutionary, disruptive, next-gen, synergy, state-of-the-art, paradigm shift.
2. Avoid generic name-echo prose (e.g. 'Total Sales calculates the total sales' or 'Active users shows active users'). Explain *how* or *why*.
3. Do not include duplicated sentences back-to-back.
4. Verify that any objects mentioned (measures, tables, pages) exist in the model.
"""

BANNED_WORDS = (
    "revolutionary", "disruptive", "next-gen", "synergy",
    "state-of-the-art", "paradigm shift",
)

CRITIC_SYSTEM = f"""You are an expert technical editor. Review the labelled text fields below
against the style rules and output any violations. Each field is keyed by its
location label — echo that same label back in each violation so the caller
knows which field to fix. For each violation, provide the exact quote to be
replaced and a suggested fix that resolves the issue. Only flag genuine
problems; most fields will have none.

{STYLE_RULES}
"""

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "violations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "The field's location label, copied from the input."},
                    "quote": {"type": "string"},
                    "rule": {"type": "string"},
                    "suggested_fix": {"type": "string"}
                },
                "required": ["location", "quote", "rule", "suggested_fix"]
            }
        }
    },
    "required": ["violations"]
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_BRACKETED_NAME = re.compile(r"\[([A-Za-z0-9_ ]+)\]")


def _strip_banned_words(text: str) -> str:
    cleaned = text
    for word in BANNED_WORDS:
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        if pattern.search(cleaned):
            cleaned = pattern.sub("", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _dedupe_adjacent_sentences(text: str) -> str:
    sentences = _SENTENCE_SPLIT.split(text)
    out: list[str] = []
    prev_norm: Optional[str] = None
    for s in sentences:
        norm = s.strip().lower()
        if norm and norm == prev_norm:
            continue
        out.append(s)
        prev_norm = norm or prev_norm
    return " ".join(s for s in out if s).strip()


def _unknown_bracketed_names(text: str, known_names: set[str]) -> list[str]:
    return sorted({
        m.group(1).strip() for m in _BRACKETED_NAME.finditer(text)
        if m.group(1).strip() and m.group(1).strip() not in known_names
    })


def apply_critic_pass(
    fields: list[tuple[str, str]],
    client: Optional[LLMClient],
    *,
    known_names: Optional[set[str]] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> dict[str, str]:
    """Run the critic over ``[(location, text), ...]``. Returns
    ``{location: corrected_text}`` for every location whose text changed —
    from the deterministic pre-pass, the LLM pass, or both."""
    warn = warn or (lambda _msg: None)
    known_names = known_names or set()
    results: dict[str, str] = {}
    working: dict[str, str] = {}

    for location, text in fields:
        if not text:
            continue
        if "```" in text:
            # A fenced code block (e.g. a fix-snippet's DAX/TMDL) — never run
            # sentence-splitting or hand it to the style LLM; both risk
            # mangling code that happens to contain '.'/'!'/'?'.
            continue
        cleaned = _dedupe_adjacent_sentences(_strip_banned_words(text))
        working[location] = cleaned
        if cleaned != text:
            results[location] = cleaned

        unknown = _unknown_bracketed_names(cleaned, known_names)
        if unknown:
            warn(f"{location}: references unknown object(s) {', '.join(unknown)} — verify wording.")

    if client is not None and working:
        try:
            response = call_llm(
                client, CRITIC_SYSTEM, {"fields": working}, CRITIC_SCHEMA, warn, "Critic",
            )
        except Exception as exc:  # pragma: no cover - defensive, mirrors call_llm's own contract
            warn(f"Critic: LLM call failed, skipping style pass ({exc})")
            response = None

        if response:
            for v in response.get("violations", []):
                location = (v.get("location") or "").strip()
                quote = (v.get("quote") or "").strip()
                fix = v.get("suggested_fix", "")
                if not location or not quote or location not in working:
                    continue
                current = results.get(location, working[location])
                if quote in current:
                    results[location] = current.replace(quote, fix)

    return results


def apply_results(triples: list[tuple[str, str, Callable[[str], None]]], results: dict[str, str]) -> None:
    """Write ``results`` (from :func:`apply_critic_pass`) back onto whatever
    object each ``setter`` closes over — callers build ``triples`` from their
    own document schema (``(location, original_text, setter)``)."""
    for location, _original, setter in triples:
        if location in results:
            setter(results[location])
