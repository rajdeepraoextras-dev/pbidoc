"""The Senior Reviewer pass: a benchmark-gated, whole-bundle review loop.

Runs after all documents in a job are generated and before any rendering is
persisted. The deterministic scorer (``agents/benchmark.py``) evaluates every
``auto`` benchmark check first; the Senior Reviewer LLM then judges the
``judge``-method checks and proposes targeted prose fixes for any failing,
prose-fixable check. Fixes are applied through the exact same safety
envelope every other quality pass already uses — the generators' own
``(location, text, setter)`` triples, ``critic.apply_results`` (rejects
meta-commentary), a re-grounding pass over each changed document, and the
unconditional ``sanitize_narratives`` final gate — then the scorer re-runs.
The loop repeats until everything evaluated passes or ``max_fix_cycles`` is
hit (wall-clock guard: a user is waiting on the job).

Hard guardrails, mirroring the rest of the pipeline:

- Deterministic facts are ground truth. The reviewer is told, and the fix
  applier enforces, that a fix may never downgrade real prose to the punt
  phrase ("Unknown — requires business confirmation.") — the same
  improve-never-downgrade rule the Column Describer follows (D6).
- Structural problems (a missing section, a render-only defect) are
  reported as ``gaps``, never attempted as prose edits.
- Any LLM failure degrades gracefully: ``call_llm`` returns ``None`` and the
  loop simply stops with whatever the deterministic scorer measured — the
  quality pass is a layer on top of an already-complete bundle, never a
  requirement for one.

The resulting :class:`QualityReport` is internal-only: logged/attached to
job telemetry, never rendered into a user-facing document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .benchmark import (
    BENCHMARK_CHECKS,
    BENCHMARK_VERSION,
    CHECKS_BY_ID,
    BenchmarkReport,
    narrative_triples_for,
    run_benchmark,
)
from .critic import apply_results
from .generators.base import call_llm
from .grounding import apply_grounding_pass
from .io import STYLE_RULES
from .llm import LLMClient
from .sanitize import is_punt_phrase, sanitize_narratives

Warn = Callable[[str], None]


SENIOR_REVIEWER_SYSTEM = f"""\
You are a Big-4 senior QA partner performing the final review of a complete Power BI \
documentation bundle (technical documentation, audit & health report, executive summary, user \
guide) before it ships to a client. You receive the bundle's narrative fields, a digest of the \
model the documents describe (ground truth), the job's deterministic check counts, and a fixed \
benchmark checklist with the deterministic scorer's failures already identified.

Your tasks:
1. verdicts: For each checklist item given to you, judge whether the bundle passes it. Judge only \
the check IDs provided — never invent new ones.
2. fixes: For failures that can be repaired by rewriting one of the given narrative fields, \
provide the fix. Each fix names the doc_type, the field's exact location label (copied from the \
input), the check it addresses, and revised_text — the complete replacement prose for that field.
3. gaps: Anything failing that cannot be fixed by rewriting a listed field (a missing section, a \
structural or rendering defect, missing human input) goes here, never in fixes.

Hard rules for revised_text:
- The model digest and check counts are ground truth. Never change a number, name, count, or \
verdict to disagree with them, and never invent a table, measure, page, or relationship not \
present in the digest.
- Never write the sentence "Unknown — requires business confirmation." or any variant of it. If \
a claim cannot be verified, rewrite it to the strongest statement the digest does support.
- revised_text is final replacement prose for the whole field: no editing directives, no \
meta-commentary, no mention of this review, the benchmark, check IDs, or the revision process.
- Preserve everything in the original field that is already correct; change only what the check \
requires.
{STYLE_RULES}"""


REVIEWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts", "fixes", "gaps"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id", "passed", "note"],
                "properties": {
                    "check_id": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "note": {"type": "string"},
                },
            },
        },
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["doc_type", "location", "revised_text", "check_id"],
                "properties": {
                    "doc_type": {"type": "string"},
                    "location": {"type": "string"},
                    "revised_text": {"type": "string"},
                    "check_id": {"type": "string"},
                },
            },
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["check_id", "doc_type", "description"],
                "properties": {
                    "check_id": {"type": "string"},
                    "doc_type": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
    },
}

_DIGEST_CHAR_BUDGET = 15_000


def reviewer_input(
    docs: dict[str, Any],
    report: BenchmarkReport,
    judge_ids: list[str],
    ai_context: Any,
) -> dict:
    """The Senior Reviewer's payload: the checks it must judge (all judge-
    method IDs plus every failing auto check), the scorer's failure details,
    every narrative field per document (code-fenced fields excluded, same as
    grounding), and the truncated model digest as ground truth."""
    failing = report.failing()
    check_ids = list(dict.fromkeys(judge_ids + [r.check_id for r in failing]))
    checks = [{"id": cid, "title": CHECKS_BY_ID[cid].title,
               "pass_criterion": CHECKS_BY_ID[cid].pass_criterion}
              for cid in check_ids if cid in CHECKS_BY_ID]
    documents = {
        dtype: {loc: text for loc, text, _ in narrative_triples_for(dtype, doc)
                if text and "```" not in text}
        for dtype, doc in docs.items()
    }
    payload: dict = {
        "benchmark_version": BENCHMARK_VERSION,
        "checks": checks,
        "deterministic_failures": [r.to_dict() for r in failing],
        "documents": documents,
    }
    digest = getattr(ai_context, "model_digest", None)
    if digest:
        payload["model_digest"] = digest[:_DIGEST_CHAR_BUDGET]
    ledger = getattr(ai_context, "checks_ledger", None)
    if ledger:
        payload["checks_ledger"] = ledger
    return payload


def _apply_fixes(docs: dict[str, Any], fixes: list[dict], warn: Warn) -> set[str]:
    """Apply reviewer fixes onto the documents through the established
    safety envelope. Returns the doc types actually changed.

    Guards, in order: unknown doc type / unknown location / empty text are
    dropped; a fix that would downgrade real prose to the punt phrase is
    dropped (improve-never-downgrade, D6); code-fenced fields are never
    touched; and ``apply_results`` itself still rejects meta-commentary —
    the same choke point every critic/grounding result passes through."""
    changed: set[str] = set()
    by_doc: dict[str, list[dict]] = {}
    for f in fixes or []:
        by_doc.setdefault(f.get("doc_type", ""), []).append(f)

    for doc_type, doc_fixes in by_doc.items():
        doc = docs.get(doc_type)
        if doc is None:
            continue
        triples = narrative_triples_for(doc_type, doc)
        originals = {loc: text for loc, text, _ in triples}
        results: dict[str, str] = {}
        for f in doc_fixes:
            loc = (f.get("location") or "").strip()
            revised = (f.get("revised_text") or "").strip()
            if loc not in originals or not revised:
                continue
            original = originals[loc]
            if revised == original:
                continue
            if "```" in original:
                continue
            if is_punt_phrase(revised) and not is_punt_phrase(original):
                warn(f"Senior Reviewer: rejected a punt-phrase downgrade for {doc_type}:{loc}")
                continue
            results[loc] = revised
        if results:
            apply_results(triples, results)
            # apply_results may still have rejected everything (meta-
            # commentary guard) — only count the doc as changed when a field
            # actually moved.
            post = {loc: text for loc, text, _ in narrative_triples_for(doc_type, doc)}
            if any(post.get(loc) != originals.get(loc) for loc in results):
                changed.add(doc_type)
    return changed


@dataclass
class QualityReport:
    """Internal-only outcome of the review loop — job telemetry, never
    rendered into a user-facing document."""
    benchmark_version: str
    score: int
    max_evaluated_points: int
    iterations: int
    results: list[dict]
    unresolved: list[str]
    gaps: list[dict] = field(default_factory=list)
    gates_triggered: list[str] = field(default_factory=list)
    reviewer_ran: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_version": self.benchmark_version,
            "score": self.score,
            "max_evaluated_points": self.max_evaluated_points,
            "iterations": self.iterations,
            "unresolved": list(self.unresolved),
            "gates_triggered": list(self.gates_triggered),
            "gaps": list(self.gaps),
            "reviewer_ran": self.reviewer_ran,
            "results": list(self.results),
        }

    def summary_line(self) -> str:
        unresolved = ", ".join(self.unresolved) if self.unresolved else "none"
        return (f"quality: score={self.score}/{self.max_evaluated_points} "
                f"(benchmark v{self.benchmark_version}, {self.iterations} fix cycle(s), "
                f"unresolved: {unresolved})")


def _reground_and_sanitize(docs: dict[str, Any], changed: set[str], client: LLMClient,
                           warn: Warn, ai_context: Any) -> None:
    """Re-verify each changed document's prose against the model digest and
    run the unconditional final sanitation gate — the same critic-then-
    grounding-then-sanitize order each generator itself ends with, minus the
    critic (the reviewer's own prompt already enforces the style rules)."""
    digest = getattr(ai_context, "model_digest", None)
    for dtype in changed:
        doc = docs[dtype]
        if digest:
            triples = narrative_triples_for(dtype, doc)
            fields = [(loc, text) for loc, text, _ in triples]
            grounded = apply_grounding_pass(fields, client, model_digest=digest,
                                            warn=warn, ai_context=ai_context)
            apply_results(triples, grounded)
        sanitize_narratives(narrative_triples_for(dtype, doc))


def run_review_loop(
    docs: dict[str, Any],
    model: Any,
    client: Optional[LLMClient],
    warn: Optional[Warn],
    ai_context: Any,
    *,
    max_fix_cycles: int = 2,
) -> QualityReport:
    """Score the bundle, then (when a client is available) run up to
    ``max_fix_cycles`` Senior Reviewer fix-and-rescore cycles until every
    evaluated benchmark check passes. Always returns a
    :class:`QualityReport`; never raises out of an LLM failure and never
    leaves a document in a worse state than it entered (every applied fix
    passes the same guards the existing quality passes use)."""
    warn = warn or (lambda _msg: None)
    report = run_benchmark(docs, model=model, ai_context=ai_context)
    judge_ids = [c.id for c in BENCHMARK_CHECKS if c.method == "judge"]
    judge_verdicts: dict[str, dict] = {}
    gaps: list[dict] = []
    iterations = 0
    reviewer_ran = False

    if client is not None and ai_context is not None:
        for cycle in range(max_fix_cycles):
            fixable_failures = [r for r in report.failing()
                                if CHECKS_BY_ID[r.check_id].prose_fixable]
            failed_judges = [cid for cid, v in judge_verdicts.items() if not v["passed"]]
            # First cycle always consults the reviewer (the judge checks are
            # its job); later cycles only when something actionable remains.
            if cycle > 0 and not fixable_failures and not failed_judges:
                break

            response = call_llm(
                client, SENIOR_REVIEWER_SYSTEM,
                reviewer_input(docs, report, judge_ids, ai_context),
                REVIEWER_SCHEMA, warn, "Senior Reviewer", ai_context=ai_context,
            )
            if response is None:
                break
            reviewer_ran = True

            for v in response.get("verdicts", []):
                cid = (v.get("check_id") or "").strip()
                if cid in CHECKS_BY_ID:
                    judge_verdicts[cid] = {"passed": bool(v.get("passed")),
                                           "note": v.get("note", "")}
            for g in response.get("gaps", []):
                if (g.get("check_id") or "") in CHECKS_BY_ID:
                    gaps.append({"check_id": g["check_id"],
                                 "doc_type": g.get("doc_type", ""),
                                 "description": g.get("description", "")})

            changed = _apply_fixes(docs, response.get("fixes", []), warn)
            if not changed:
                break
            iterations = cycle + 1
            _reground_and_sanitize(docs, changed, client, warn, ai_context)
            report = run_benchmark(docs, model=model, ai_context=ai_context)
            failed_judges = [cid for cid, v in judge_verdicts.items() if not v["passed"]]
            if report.all_evaluated_pass() and not failed_judges:
                break

    # Merge the reviewer's judge verdicts into the final results: a judge
    # check the scorer necessarily left unevaluated gets the reviewer's
    # verdict; a judged failure whose fields were subsequently fixed keeps
    # the failing verdict only if the loop never got to re-run (honest
    # pessimism beats silently marking it resolved).
    results = []
    unresolved = []
    score = report.score
    for r in report.results:
        entry = r.to_dict()
        verdict = judge_verdicts.get(r.check_id)
        if verdict is not None and r.passed is None:
            entry["passed"] = verdict["passed"]
            entry["detail"] = verdict["note"]
            if verdict["passed"]:
                score += CHECKS_BY_ID[r.check_id].points
        if entry["passed"] is False:
            unresolved.append(r.check_id)
        results.append(entry)
    max_evaluated = sum(CHECKS_BY_ID[e["check_id"]].points for e in results
                        if e["passed"] is not None)
    # Judge points were added on top of the scorer's (already gate-capped)
    # score — re-apply the same caps so a triggered gate still bounds the
    # final number.
    from .benchmark import _GATES
    for gate, _triggers, cap in _GATES:
        if gate in report.gates_triggered:
            score = min(score, cap)

    return QualityReport(
        benchmark_version=BENCHMARK_VERSION,
        score=min(score, max_evaluated) if max_evaluated else score,
        max_evaluated_points=max_evaluated,
        iterations=iterations,
        results=results,
        unresolved=unresolved,
        gaps=gaps,
        gates_triggered=list(report.gates_triggered),
        reviewer_ran=reviewer_ran,
    )
