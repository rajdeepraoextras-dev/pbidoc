"""Cross-artifact consistency pass (Day 2 of the post-launch hardening plan).

Every generator's own grounding pass (``agents/grounding.py``, Phase 3)
checks a document's prose against the *model's* metadata digest — "does this
sentence match what's actually in the .pbip file." It has no way to catch a
document contradicting a *sibling* document's own computed conclusions, e.g.
the executive summary calling the model "a well-structured star schema"
while the Audit & Health Report's deterministic star-schema check failed, or
a user guide claiming "no row-level security is configured" when the model
defines three RLS roles. Those are exactly the kind of thing a Fortune-500
reviewer catches in the first five minutes and stops trusting the whole
bundle over.

The Audit & Health Report's deterministic rule engine
(:mod:`pbicompass.agents.audit_rules`) is ground truth here — it is a pure
function of the model, reproducible, never a guess. This module checks every
other document's narrative fields against that ground truth in two layers:

- :func:`check_deterministic_consistency` — a fixed, regex-matched
  vocabulary of the claims most likely to leak into prose and most costly to
  get wrong: star-schema shape, RLS present/absent, refresh configured,
  full description coverage, and fact/dimension table counts. Free, exact,
  no LLM involved — every "quote"/"correction" pair is a safe same-slot
  phrase substitution (e.g. "star schema" -> "snowflake schema"), never a
  full-sentence splice, so there's no D3-style grammar risk to guard against.
- :func:`apply_consistency_pass` — an LLM-routed check, structurally mirroring
  ``grounding.py``'s own ``apply_grounding_pass`` (same ``call_llm`` /
  ``(location, text, setter)`` triple / ``apply_results`` contract), for
  free-text claims outside the fixed vocabulary. Ground truth is a digest of
  the Audit document's verdicts, not the whole-model digest grounding.py uses.

:func:`check_consistency` runs both, deterministic first (its corrections are
exact and free, so the LLM pass then sees the already-corrected text instead
of re-litigating the same claim). Callers apply the merged result via the
existing ``critic.apply_results`` — this *is* the "per-agent regeneration":
each generator's own narrative fields get rewritten in place with the
audit-grounded correction, the same mechanism the critic/grounding passes
already use, rather than a separate whole-document regeneration round-trip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from .deterministic import schema_shape
from .llm import LLMClient

if TYPE_CHECKING:
    from ..schemas.audit_document import AuditDocument
    from ..schemas.model import SemanticModel
    from .context import JobAIContext

Warn = Callable[[str], None]


# -- Ground truth: the Audit & Health Report's own verdicts -------------------

@dataclass
class AuditVerdicts:
    """The fixed-vocabulary facts every other document's prose is checked
    against — read straight off the Audit & Health Report's own computed
    output (never re-derived independently, so there is nothing for this
    module's ground truth to itself disagree with)."""

    schema_shape: str
    is_star_schema: bool
    fact_count: int
    dim_count: int
    rls_role_count: int
    refresh_configured: bool
    description_coverage_pct: Optional[int]


def build_audit_verdicts(model: "SemanticModel", audit_doc: "AuditDocument") -> AuditVerdicts:
    shape, facts, dims = schema_shape(model)
    star_check = next((c for c in audit_doc.best_practices if c.id == "star_schema"), None)

    # Same formula ``audit_rules.check_best_practices``'s "description_coverage"
    # check and ``check_governance``'s "descriptions" finding already use —
    # reused here rather than re-derived so this module's own idea of
    # "coverage" can never drift from the Audit document's.
    measures = model.all_measures()
    visible_columns = [c for t in model.tables for c in t.columns if not c.is_hidden]
    total_describable = len(measures) + len(visible_columns)
    coverage_pct: Optional[int] = None
    if total_describable:
        described = sum(1 for m in measures if m.description) + sum(1 for c in visible_columns if c.description)
        coverage_pct = round(100 * described / total_describable)

    return AuditVerdicts(
        schema_shape=shape,
        is_star_schema=bool(star_check.passed) if star_check else shape.startswith("a star schema"),
        fact_count=len(facts),
        dim_count=len(dims),
        rls_role_count=len(model.roles),
        refresh_configured=bool(getattr(audit_doc.metadata, "refresh_schedule", None)),
        description_coverage_pct=coverage_pct,
    )


def consistency_digest(verdicts: AuditVerdicts) -> str:
    lines = [
        f"Schema shape: {verdicts.schema_shape} (star schema: {'yes' if verdicts.is_star_schema else 'no'}).",
        f"Fact tables: {verdicts.fact_count}. Dimension tables: {verdicts.dim_count}.",
        f"Row-level security roles defined: {verdicts.rls_role_count}.",
        f"Refresh schedule configured: {'yes' if verdicts.refresh_configured else 'no'}.",
    ]
    if verdicts.description_coverage_pct is not None:
        lines.append(f"Description coverage: {verdicts.description_coverage_pct}% of measures/visible "
                     f"columns have a description.")
    return "\n".join(lines)


# -- Human-claim discrepancy check (Day 3) -------------------------------------
#
# Distinct from the star-schema/RLS-count/etc. checks above: those catch an
# AI-*inferred* claim contradicting the Audit document's own verdict. This
# catches a *human-stated* fact (the intake form's Security & RLS Validation
# Notes) contradicting what the model actually contains — never silently
# picked one way or the other, per the roadmap: "You stated X; the model
# shows Y" is itself a consulting-grade deliverable, not just an error to
# suppress.

@dataclass
class HumanClaimDiscrepancy:
    field: str            # the intake field the claim came from, e.g. "security_notes"
    human_claim: str      # the relevant excerpt of what the human said
    model_finding: str    # what the model's own metadata actually shows
    explanation: str      # why these two don't square, in plain language


_RLS_VALIDATED_RE = re.compile(
    r"\b(validated|tested|reviewed|configured|active|in place|enforced)\b.{0,40}\b(RLS|row-level security|roles?)\b"
    r"|\b(RLS|row-level security|roles?)\b.{0,40}\b(validated|tested|reviewed|configured|active|in place|enforced)\b",
    re.IGNORECASE,
)
_RLS_NOT_NEEDED_RE = re.compile(
    r"\bno (?:RLS|row-level security)\b.{0,40}\b(?:needed|required|necessary|applicable)\b"
    r"|\brow-level security (?:is )?not (?:needed|required|necessary|applicable|used)\b"
    r"|\bRLS (?:is )?not (?:needed|required|necessary|applicable|used)\b",
    re.IGNORECASE,
)


def find_human_claim_discrepancies(
    security_notes: Optional[str], rls_role_count: int,
) -> list[HumanClaimDiscrepancy]:
    """Compare the intake form's Security & RLS Validation Notes against the
    model's actual RLS role count. A human claiming RLS is "validated"/
    "configured"/"tested" while the model defines zero roles — or claiming
    RLS is intentionally unused while the model actually defines some — is
    exactly the kind of contradiction neither side should silently resolve:
    both are surfaced as a :class:`HumanClaimDiscrepancy` for a reader to
    reconcile, never guessed at.

    Takes the raw role count (not a full :class:`AuditVerdicts`) so the
    audit generator itself — which computes this discrepancy as part of
    building the very document ``AuditVerdicts`` is normally read back
    from — never needs an already-built :class:`AuditDocument` to call it."""
    if not security_notes:
        return []
    discrepancies: list[HumanClaimDiscrepancy] = []

    validated_match = _RLS_VALIDATED_RE.search(security_notes)
    if validated_match and rls_role_count == 0:
        discrepancies.append(HumanClaimDiscrepancy(
            field="security_notes",
            human_claim=security_notes.strip(),
            model_finding="The model defines 0 row-level security roles.",
            explanation="The intake form describes RLS as validated/configured, but no RLS role "
                        "exists in the model file — confirm whether RLS was removed after this note "
                        "was written, or whether it's enforced somewhere this tool can't see "
                        "(e.g. object-level security, a gateway policy).",
        ))

    not_needed_match = _RLS_NOT_NEEDED_RE.search(security_notes)
    if not_needed_match and rls_role_count > 0:
        from ..render._shared import pluralize  # lazy: avoids the agents<->render import cycle

        role_word = pluralize("role", rls_role_count)
        discrepancies.append(HumanClaimDiscrepancy(
            field="security_notes",
            human_claim=security_notes.strip(),
            model_finding=f"The model defines {rls_role_count} row-level security {role_word}.",
            explanation=f"The intake form states RLS isn't needed/used, but the model actually "
                        f"defines {role_word} — confirm whether they're stale/unused or whether this "
                        f"note is out of date.",
        ))
    return discrepancies


# -- Assumption-to-finding mitigation matching (Day 3) -------------------------

_ASSUMPTION_STOPWORDS = frozenset(
    "the a an is are was were this that these those and or but if of in on at for to with per by as "
    "not no data report model table column measure".split()
)


def _significant_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)
            if w.lower() not in _ASSUMPTION_STOPWORDS}


def annotate_findings_with_assumptions(findings: list, assumptions: Optional[str], *, min_overlap: int = 2) -> int:
    """For each line of the intake form's Business Assumptions & Limitations
    field, check for significant keyword overlap against each finding's
    ``detail`` text; on a match, append a "Mitigated per human input" note
    quoting the assumption. A reviewer then sees the finding is already
    accounted for by a documented business constraint, rather than reading
    as an unexplained gap the human never addressed.

    Mutates ``findings`` in place (each item must have a ``.detail`` string
    attribute — ``DaxFinding``/``PerformanceRisk``/``GovernanceFinding``/
    ``BestPracticeCheck`` all do) and returns the count annotated. A
    conservative ``min_overlap`` (2 shared significant words by default)
    keeps this a real match, not a coincidental one-word overlap."""
    if not assumptions:
        return 0
    lines = [ln.strip() for ln in assumptions.split("\n") if ln.strip()]
    if not lines:
        return 0
    annotated = 0
    for finding in findings:
        detail = getattr(finding, "detail", "")
        if not detail:
            continue
        finding_words = _significant_words(detail)
        for line in lines:
            if len(finding_words & _significant_words(line)) >= min_overlap:
                finding.detail = f'{detail} (Mitigated per human input: "{line}")'
                annotated += 1
                break
    return annotated


# -- Layer 1: deterministic fixed-vocabulary check -----------------------------

_STAR_SCHEMA_RE = re.compile(r"\bstar[\s-]schema\b", re.IGNORECASE)
_NOT_STAR_SCHEMA_RE = re.compile(r"\b(?:not|isn't|is not|n't)\s+(?:a\s+|structured\s+as\s+a\s+)?star[\s-]schema\b",
                                  re.IGNORECASE)

_NO_RLS_RE = re.compile(r"\bno row-level security\b|\bno RLS\b|\bwithout row-level security\b", re.IGNORECASE)
_RLS_COUNT_RE = re.compile(r"\b(\d+)\s+(?:RLS|row-level security)\s+roles?\b", re.IGNORECASE)

_REFRESH_NOT_CONFIGURED_RE = re.compile(
    r"\brefresh(?:es)?(?: schedule)? (?:is |are |has |have )?(?:not been )?"
    r"(?:not configured|unconfigured|not set|not scheduled)\b",
    re.IGNORECASE,
)

_FACT_COUNT_RE = re.compile(r"\b(\d+)\s+fact tables?\b", re.IGNORECASE)
_DIM_COUNT_RE = re.compile(r"\b(\d+)\s+dimension tables?\b", re.IGNORECASE)

_FULL_COVERAGE_RE = re.compile(
    r"\b(?:all|every) measures?(?:\s*/\s*|\s+and\s+)?(?:columns?)?\s*(?:are|is|have)?\s*"
    r"(?:fully |completely )?documented\b"
    r"|\bevery measure has a description\b",
    re.IGNORECASE,
)


def _shape_short_label(shape: str) -> str:
    if shape.startswith("a star schema"):
        return "star schema"
    if shape.startswith("a snowflake schema"):
        return "snowflake schema"
    if shape.startswith("a multi-fact"):
        return "multi-fact (galaxy) schema"
    if shape.startswith("a flat"):
        return "flat, disconnected model"
    return "non-star relational model"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _check_star_schema(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    if _NOT_STAR_SCHEMA_RE.search(text):
        return None  # already correctly hedged — nothing to correct
    m = _STAR_SCHEMA_RE.search(text)
    if m and not v.is_star_schema:
        return (m.group(0), _shape_short_label(v.schema_shape))
    return None


def _check_rls(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    m = _NO_RLS_RE.search(text)
    if m and v.rls_role_count > 0:
        return (m.group(0), _plural(v.rls_role_count, "row-level security role"))
    m = _RLS_COUNT_RE.search(text)
    if m and int(m.group(1)) != v.rls_role_count:
        return (m.group(0), _plural(v.rls_role_count, "RLS role"))
    return None


def _check_refresh(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    m = _REFRESH_NOT_CONFIGURED_RE.search(text)
    if m and v.refresh_configured:
        return (m.group(0), "refresh is configured")
    return None


def _check_fact_count(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    m = _FACT_COUNT_RE.search(text)
    if m and int(m.group(1)) != v.fact_count:
        return (m.group(0), _plural(v.fact_count, "fact table"))
    return None


def _check_dim_count(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    m = _DIM_COUNT_RE.search(text)
    if m and int(m.group(1)) != v.dim_count:
        return (m.group(0), _plural(v.dim_count, "dimension table"))
    return None


def _check_description_coverage(text: str, v: AuditVerdicts) -> Optional[tuple[str, str]]:
    m = _FULL_COVERAGE_RE.search(text)
    if m and v.description_coverage_pct is not None and v.description_coverage_pct < 100:
        return (m.group(0), f"{v.description_coverage_pct}% of measures/columns are documented")
    return None


_CHECKERS = (
    _check_star_schema, _check_rls, _check_refresh,
    _check_fact_count, _check_dim_count, _check_description_coverage,
)


def check_deterministic_consistency(
    fields: list[tuple[str, str]], verdicts: AuditVerdicts, *, warn: Optional[Warn] = None,
) -> dict[str, str]:
    """Scan ``[(location, text), ...]`` for the fixed-vocabulary claims and
    return ``{location: corrected_text}`` for every location that changed —
    same contract as ``critic.apply_critic_pass``/``grounding.apply_grounding_pass``
    so callers can feed the result straight into ``apply_results``."""
    warn = warn or (lambda _msg: None)
    results: dict[str, str] = {}
    for location, text in fields:
        if not text:
            continue
        current = text
        for checker in _CHECKERS:
            hit = checker(current, verdicts)
            if hit is None:
                continue
            quote, correction = hit
            if quote not in current:
                continue
            current = current.replace(quote, correction)
            warn(f"{location}: consistency pass corrected a claim that contradicted the "
                 f"Audit & Health Report ({quote!r} -> {correction!r}).")
        if current != text:
            results[location] = current
    return results


# -- Layer 2: LLM-routed free-text check ---------------------------------------

CONSISTENCY_SYSTEM = """\
You are a consistency-checker comparing prose in one generated Power BI report document against \
the verified verdicts already computed by this same tool's deterministic Audit & Health Report for \
the identical model. The audit verdicts are ground truth: rule-based analysis of the model's actual \
metadata, never a guess.

You receive labelled narrative fields from a document (executive summary, technical documentation, \
or user guide) generated for the same report, plus a digest of the Audit & Health Report's verdicts \
(schema shape, RLS role count, refresh configuration, description coverage, fact/dimension table \
counts).

For each field, identify any claim that directly CONTRADICTS a specific audit verdict — e.g. the \
field claims "a well-structured star schema" while the verdict says the model is not a star schema; \
the field claims row-level security is configured while the verdict says 0 RLS roles exist; the \
field states a fact/dimension table count that doesn't match the verdict's count. Do not flag \
stylistic differences, subjective framing, or claims the verdicts don't cover — only genuine, \
checkable contradictions.

For each contradiction, report:
- location: the field's location label, copied from the input.
- quote: the exact substring of the field's text that makes the contradicted claim (must appear verbatim in that field).
- correction: the corrected wording, grounded in the actual verdict, phrased to fit naturally in place of the quote.

Only report contradictions you are actually checking against a given verdict — most fields will have \
none. Never invent a verdict not present in the input.
"""

CONSISTENCY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contradictions"],
    "properties": {
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["location", "quote", "correction"],
                "properties": {
                    "location": {"type": "string"},
                    "quote": {"type": "string"},
                    "correction": {"type": "string"},
                },
            },
        },
    },
}


def consistency_input(fields: dict[str, str], verdicts_digest: str) -> dict:
    return {"fields": fields, "audit_verdicts": verdicts_digest}


def apply_consistency_pass(
    fields: list[tuple[str, str]],
    client: Optional[LLMClient],
    *,
    verdicts: Optional[AuditVerdicts],
    warn: Optional[Warn] = None,
    ai_context: Optional["JobAIContext"] = None,
) -> dict[str, str]:
    """LLM-routed cross-artifact check (Day 2) — mirrors
    ``grounding.apply_grounding_pass``'s contract exactly. A no-op (returns
    ``{}``) when offline or no audit verdicts are available (e.g. the Audit
    document wasn't generated in this job) — matches every other quality
    layer's "never a requirement" degrade pattern."""
    warn = warn or (lambda _msg: None)
    if client is None or verdicts is None:
        return {}

    # Local import: ``generators.base`` needs ``io.AGENT_EFFORT``, and
    # ``generators/__init__.py`` (via every generator module, each needing
    # this module for its own ``generate(...)`` signature) imports this
    # module — a module-level import here would cycle back into a
    # not-yet-defined ``consistency`` module (mirrors ``context.py``'s own
    # ``build_job_context`` for the identical reason).
    from .generators.base import call_llm

    working = {location: text for location, text in fields if text and "```" not in text}
    if not working:
        return {}

    try:
        response = call_llm(
            client, CONSISTENCY_SYSTEM, consistency_input(working, consistency_digest(verdicts)),
            CONSISTENCY_SCHEMA, warn, "Consistency Checker", ai_context=ai_context,
        )
    except Exception as exc:  # pragma: no cover - defensive, mirrors call_llm's own contract
        warn(f"Consistency: LLM call failed, skipping cross-artifact check ({exc})")
        return {}
    if not response:
        return {}

    results: dict[str, str] = {}
    for item in response.get("contradictions", []):
        location = (item.get("location") or "").strip()
        quote = (item.get("quote") or "").strip()
        correction = (item.get("correction") or "").strip()
        if not location or not quote or not correction or location not in working:
            continue
        current = results.get(location, working[location])
        if quote not in current:
            continue
        results[location] = current.replace(quote, correction)
        warn(f"{location}: consistency pass corrected a claim that contradicted the "
             f"Audit & Health Report.")
    return results


def check_consistency(
    fields: list[tuple[str, str]],
    client: Optional[LLMClient],
    *,
    verdicts: Optional[AuditVerdicts],
    warn: Optional[Warn] = None,
    ai_context: Optional["JobAIContext"] = None,
) -> dict[str, str]:
    """Run both layers and merge: the deterministic pass first (exact, free),
    then the LLM-routed pass over the already-corrected text — so a claim the
    fixed vocabulary already fixed is never re-litigated by the LLM, and the
    LLM's broader net still runs on top. Returns ``{location: corrected_text}``
    for ``apply_results``; a no-op when no audit verdicts are available."""
    if verdicts is None:
        return {}
    det_results = check_deterministic_consistency(fields, verdicts, warn=warn)
    carried = [(loc, det_results.get(loc, text)) for loc, text in fields]
    llm_results = apply_consistency_pass(carried, client, verdicts=verdicts, warn=warn, ai_context=ai_context)
    merged = dict(det_results)
    merged.update(llm_results)
    return merged
