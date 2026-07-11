"""Deterministic output-sanitation guards (AI-Native roadmap D2/D3/D6).

Small, pure-Python checks that protect user-facing prose from LLM failure
modes seen in production output:

- :func:`is_meta_commentary` (D2/D3) — the model occasionally returns an
  internal editing directive, a reference to the document's own data
  structures, or a fragment of its own system-prompt guardrail wording
  instead of the content a field asked for, e.g. "Consider providing a
  more specific description of how 'select' is used" shipped as a
  glossary definition, "Remove the duplicated entry as it is identical to
  glossary[15].plain_definition.", or a leaked fragment of ``io.py``'s own
  "Only write exactly ... when no such structural fact is available
  either." guardrail wording. None of these are factual claims, so the
  grounding pass (Phase 3) never catches them — this is a cheaper,
  earlier, deterministic net. This is the shared gate every critic/
  grounding replacement passes through (``critic.apply_results``), so it
  intentionally stays conservative — pattern-matched, not length-based —
  to never reject a legitimate short factual correction.
- :func:`is_low_content_fragment` (D3) — a narrower, opt-in check for
  callers that do sentence-granular splicing (grounding's own
  replacement logic): a short, lowercase-starting clause is the shape
  left behind when such a splice stranded a dependent clause from the
  sentence it was cut out of, e.g. "when no such structural fact is
  available either." Not folded into ``is_meta_commentary`` because that
  function's blast radius is every field in every generator, including
  legitimately short replacements.
- :func:`is_punt_phrase` (D6) — the model's own "I don't know" sentences
  ("Unknown — requires business confirmation.", "Business meaning could
  not be inferred automatically; requires business confirmation."). Used
  by callers to enforce "the LLM may only improve, never downgrade": a
  punt is never allowed to overwrite an existing, real deterministic
  description.

Each is a guard a caller applies at the merge point where an LLM result
would otherwise overwrite a value already computed elsewhere — none of
these functions invent replacement text; callers always fall back to
whatever deterministic/prior text they already had.
"""

from __future__ import annotations

import re
from typing import Optional

_STARTS_WITH_DIRECTIVE = re.compile(
    r"^\s*(Consider|Remove|Verify|Ensure|Add a|Provide|Explain)\b", re.IGNORECASE
)
_META_REFERENCE = re.compile(
    r"glossary\[|plain_definition|the duplicated entry"
    # The trailing clause of io.py's own column/measure-describer
    # guardrails ("Only write exactly \"Unknown — requires business
    # confirmation.\" when no such structural fact is available either.")
    # — an orphan fragment of this leaking means the model echoed its own
    # instructions instead of producing content.
    r"|structural fact is available either|only write exactly\b",
    re.IGNORECASE,
)

# Function words ignored when judging whether a short clause has enough
# real content to be legitimate standalone prose (see
# :func:`is_low_content_fragment`).
_STOPWORDS = frozenset(
    "a an the is are was were be been being to of in on for when either "
    "such that this it its as by and or but if so no not only exactly "
    "which who what where do does did has have had will would can could "
    "may might".split()
)


def _content_word_count(text: str) -> int:
    return sum(1 for w in re.findall(r"[A-Za-z']+", text) if w.lower() not in _STOPWORDS)


def is_low_content_fragment(text: Optional[str], min_content_words: int = 4) -> bool:
    """True when ``text`` is a short, lowercase-starting clause with fewer
    than ``min_content_words`` real (non-stopword) words (D3) — the shape
    left behind when sentence-granular grounding replacement strands a
    dependent clause from the sentence it was cut out of, e.g. "when no
    such structural fact is available either." Gated on a lowercase start
    so a genuinely short but *complete* sentence (e.g. the deterministic
    fallback "No description set.") is never flagged — only fragments that
    also fail to open like a sentence."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or not stripped[0].islower():
        return False
    return _content_word_count(stripped) < min_content_words


def is_meta_commentary(text: Optional[str]) -> bool:
    """True when ``text`` reads like an internal editing directive, a
    reference to the document's own data structures, or a leaked fragment
    of the system prompt's own guardrail wording, rather than actual prose
    (D2/D3).

    Deliberately does *not* fold in :func:`is_low_content_fragment` — this
    function is the shared gate ``critic.apply_results`` runs every
    critic/grounding replacement through for every field in every
    generator, including legitimate short factual corrections, so a
    generic "too few content words" check here would silently drop good
    replacements. Callers that specifically do sentence-granular splicing
    (grounding's own replacement logic, D3) should call
    :func:`is_low_content_fragment` themselves at that narrower point."""
    if not text:
        return False
    return bool(_STARTS_WITH_DIRECTIVE.search(text) or _META_REFERENCE.search(text))


def is_punt_phrase(text: Optional[str]) -> bool:
    """True when ``text`` is empty or one of the established "I don't
    know" sentences (D6) — used to stop the LLM from downgrading a good
    deterministic/prior description to a punt."""
    if not text:
        return True
    return "requires business confirmation" in text.lower()


def sanitize(text: Optional[str], fallback: str) -> str:
    """Return ``text`` unless it is meta-commentary (D2), in which case
    fall back to ``fallback`` — the deterministic or prior-good text."""
    if text and not is_meta_commentary(text):
        return text
    return fallback


# Tolerant of both dash characters the punt sentence has shipped with
# (em dash from ``grounding.UNVERIFIABLE_TEXT``, en dash/hyphen from any
# hand-written variant elsewhere), flexible whitespace, and an optional
# trailing period — matched case-insensitively.
_PUNT_PHRASE_RE = re.compile(
    r"unknown\s*[—–-]\s*requires\s+business\s+confirmation\.?",
    re.IGNORECASE,
)
# Same sentence-preserving split ``grounding.py`` uses — duplicated (not
# imported) to keep this module free of a dependency on the grounding pass;
# both need to agree on where a sentence ends, not share an object.
_SENTENCE_RE = re.compile(r"[^.!?]*[.!?]+(?:\s+|$)")


def _split_sentences(text: str) -> list[str]:
    sentences = _SENTENCE_RE.findall(text)
    consumed = "".join(sentences)
    if len(consumed) < len(text):
        sentences.append(text[len(consumed):])
    return [s for s in sentences if s]


def strip_punt_leak(text: Optional[str], fallback: str) -> str:
    """Remove the "Unknown — requires business confirmation." punt
    sentence (P0) from narrative prose — summaries, root-cause
    explanations, calculation text. Its one legitimate home is an
    unexplained column/measure description; anywhere else it's a leaked
    placeholder, usually from the grounding pass replacing more than one
    claim in the same field with the identical canned sentence (e.g.
    "Address the Unknown — requires business confirmation. Its resolution
    will both eliminate unused calculated columns and Unknown — requires
    business confirmation. Unknown — requires business confirmation.").

    Drops each *whole sentence* containing the phrase rather than the bare
    substring — a substring removal would strand a dangling fragment
    ("Address the ."), the same grammar failure D3 already fixed for
    grounding's own mid-clause splicing. If nothing content-bearing
    survives (empty, or under ~4 real words), returns ``fallback`` — the
    deterministic explanation already computed elsewhere — rather than
    shipping a gutted paragraph."""
    if not text:
        return fallback
    if not _PUNT_PHRASE_RE.search(text):
        return text
    kept = [s for s in _split_sentences(text) if not _PUNT_PHRASE_RE.search(s)]
    cleaned = "".join(kept).strip()
    if _content_word_count(cleaned) < 4:
        return fallback
    return cleaned


# Anchored on the phrase "health score" (never just a bare number, which
# would false-positive on findings counts, table counts, etc.), tolerant of
# the several ways an LLM narrator phrases it ("health score of this model
# is 78", "an overall health score of 78/100", "scores 78 overall") — up to
# 40 non-digit characters between the anchor and the number it's claiming.
_SCORE_MENTION_RE = re.compile(r"health\s+score[^.\d]{0,40}?(\d{1,3})", re.IGNORECASE)


def enforce_score_consistency(text: Optional[str], actual_score: int, band: str) -> str:
    """Any sentence claiming a "health score" number must agree with
    ``actual_score`` (P0) — an LLM narrator can misstate it even when given
    the correct value verbatim in its prompt (a "78" in the summary prose
    next to a "79/100" in the same document's own KPI strip is exactly the
    kind of self-contradiction a reviewer catches in the first minute).
    Any sentence whose claimed number disagrees is replaced wholesale with
    a deterministic, always-correct sentence — never patched in place,
    since a narrator that got the number wrong may have gotten the
    surrounding classification wrong too. Sentences that don't mention a
    score number, or state the right one, are returned unchanged."""
    if not text:
        return text
    changed = False
    out = []
    for sentence in _split_sentences(text):
        m = _SCORE_MENTION_RE.search(sentence)
        if m and int(m.group(1)) != actual_score:
            out.append(f"The overall health score is {actual_score}, classified as '{band}'. ")
            changed = True
        else:
            out.append(sentence)
    return "".join(out).strip() if changed else text
