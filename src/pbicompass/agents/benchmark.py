"""Machine-readable output-quality benchmark (v3.0) + deterministic scorer.

``PBICOMPASS_OUTPUT_BENCHMARK.md`` (repo root) is the human-readable rubric:
5 pillars, stable check IDs, hard gates. This module is its executable
counterpart — the IDs here mirror the MD exactly, and bumping the MD's
version means bumping :data:`BENCHMARK_VERSION` too.

Three method tiers, matching how each check can actually be evaluated:

- ``auto``   — evaluated right here, pure Python over the assembled document
  objects and their in-memory Markdown/HTML renders. No LLM, no browser.
- ``judge``  — needs quality judgment; evaluated by the Senior Reviewer
  agent (``agents/reviewer.py``), never by this scorer (``passed=None``
  here).
- ``render`` / ``manual`` — needs a headless browser (Playwright suite) or
  a human; declared for completeness so a report always covers every ID,
  but always ``passed=None`` from this scorer and never iterated on by the
  reviewer loop.

The scorer never mutates a document — it renders copies of text in memory
and reports. Every individual check is wrapped defensively: an unexpected
error in one check degrades that check to ``passed=None`` (with the error
in ``detail``), never the whole run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .sanitize import _PUNT_PHRASE_RE, _SCORE_MENTION_RE, is_low_content_fragment

BENCHMARK_VERSION = "3.0"


# --------------------------------------------------------------------------
# Spec data model
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckSpec:
    id: str                 # stable ID, mirrors PBICOMPASS_OUTPUT_BENCHMARK.md
    pillar: int             # 1..5
    points: int
    method: str             # "auto" | "judge" | "render" | "manual"
    title: str
    pass_criterion: str     # quoted verbatim to the Senior Reviewer
    doc_types: tuple[str, ...] = ()  # () = whole bundle
    # Whether a failure of this check can plausibly be repaired by rewriting
    # narrative prose fields — the only kind of fix the reviewer loop can
    # apply. Structural/render failures are reported as gaps instead.
    prose_fixable: bool = False


@dataclass
class CheckResult:
    check_id: str
    passed: Optional[bool]      # None = not evaluated this run
    detail: str = ""
    locations: list[str] = field(default_factory=list)  # "{doc_type}:{location}"

    def to_dict(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "passed": self.passed,
                "detail": self.detail, "locations": list(self.locations)}


@dataclass
class BenchmarkReport:
    results: list[CheckResult]
    score: int
    max_evaluated_points: int
    gates_triggered: list[str]

    def failing(self) -> list[CheckResult]:
        return [r for r in self.results if r.passed is False]

    def all_evaluated_pass(self) -> bool:
        return not self.failing()

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_version": BENCHMARK_VERSION,
            "score": self.score,
            "max_evaluated_points": self.max_evaluated_points,
            "gates_triggered": list(self.gates_triggered),
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------
# The benchmark spec (v3.0) — IDs/pillars/points mirror the MD rubric.
# --------------------------------------------------------------------------

BENCHMARK_CHECKS: list[CheckSpec] = [
    # Pillar 1 — Trust & Numeric Integrity (30)
    CheckSpec("T1", 1, 8, "auto", "Zero guardrail/placeholder leaks",
              "The punt phrase ('requires business confirmation') appears in no narrative prose; "
              "its only legitimate home is an unexplained description cell (measure/glossary "
              "description fields).", prose_fixable=True),
    CheckSpec("T2", 1, 6, "auto", "One health score everywhere",
              "Every health-score number stated anywhere in the bundle (structured fields and "
              "prose) is the same integer.", prose_fixable=True),
    CheckSpec("T3", 1, 4, "auto", "One set of check counts",
              "Run/passed/failed/suppressed check counts are identical in the audit document, the "
              "technical document, and the job's shared checks ledger."),
    CheckSpec("T4", 1, 6, "auto", "No verdict-narrative contradictions",
              "No narrative field asserts the opposite of a deterministic audit verdict (star "
              "schema, RLS presence, refresh configuration, description coverage, fact/dim "
              "counts).", prose_fixable=True),
    CheckSpec("T5", 1, 3, "auto", "Table-kind classification correct",
              "Date tables classified as dimensions; auto date/time internals excluded from kind "
              "stats; parameter tables not 'unknown'."),
    CheckSpec("T6", 1, 3, "auto", "Clean prose mechanics",
              "No orphan fragments, no doubled punctuation ('..', '——', '.,'), no "
              "doubled phrases, no sentences under 4 content words.", prose_fixable=True),

    # Pillar 2 — Content Completeness & Correctness (25)
    CheckSpec("C1", 2, 3, "auto", "Technical doc: all 19 sections present",
              "All 19 canonical h2 sections render, in order; never reference an artifact (e.g. "
              "'the model diagram is in section 6') unless it actually rendered.",
              doc_types=("technical",), prose_fixable=True),
    CheckSpec("C2", 2, 3, "auto", "DAX dictionary card completeness",
              "Every measure card carries a business description and a calculation explanation.",
              doc_types=("technical",)),
    CheckSpec("C3", 2, 2, "auto", "Audit findings anatomy",
              "Every recommendation carries why-it-matters, a suggested fix, an expected benefit "
              "and an effort estimate; findings carry rule IDs.", doc_types=("audit",)),
    CheckSpec("C4", 2, 3, "auto", "RTM internal coherence",
              "No requirements-matrix row is marked 'Gap' while simultaneously listing matching "
              "evidence; Covered rows always cite evidence."),
    CheckSpec("C5", 2, 2, "auto", "Executive completeness & phrasing",
              "Executive doc carries the health score; data-source phrasing is human ('1 Excel "
              "workbook'), never machine ('File.Contents(s)').", doc_types=("executive",),
              prose_fixable=True),
    CheckSpec("C6", 2, 2, "auto", "User guide quality",
              "Glossary contains business terms only (no 'select'/'select1' field parameters, no "
              "LocalDateTable internals); every page guide states its purpose.",
              doc_types=("user-guide",), prose_fixable=True),
    # v3.0 additions — industry-standard PBI documentation completeness.
    CheckSpec("C7", 2, 2, "auto", "Data dictionary coverage",
              "At least 80% of data-dictionary rows carry a real (non-empty, non-punt) column "
              "description.", doc_types=("technical",)),
    # Was "judge" until a live run proved the judge would deny a refresh
    # schedule that was plainly rendered in the document. The question is
    # factual, so it is answered from the artifacts (see _eval_c8_floor).
    CheckSpec("C8", 2, 2, "auto", "Refresh & gateway configuration documented",
              "A refresh schedule (or refresh notes) is recorded, and the data sources feeding it "
              "are documented — the two things a new owner needs to re-establish refresh.",
              doc_types=("technical", "executive")),
    CheckSpec("C9", 2, 2, "auto", "Security/RLS documented",
              "RLS roles are listed with their filter logic, or the document explicitly states "
              "that no row-level security is configured.", doc_types=("technical",)),
    CheckSpec("C10", 2, 1, "auto", "Lineage traceable",
              "Data sources and their flow into the model are documented whenever the model has "
              "data sources.", doc_types=("technical",)),
    CheckSpec("C11", 2, 1, "auto", "Ownership & contacts populated",
              "A report owner is recorded in the document metadata."),
    CheckSpec("C12", 2, 1, "auto", "Assumptions & limitations present",
              "An assumptions/limitations statement is present in the document metadata."),
    CheckSpec("C13", 2, 1, "auto", "Measure business logic explains why",
              "Measure descriptions explain the business meaning and the why of the calculation, "
              "not just an echo of the measure's name.", doc_types=("technical",),
              prose_fixable=True),

    # Pillar 3 — Visual & Diagram Layer (20)
    CheckSpec("V1", 3, 6, "render", "Intrinsic aspect ratio preserved",
              "Every diagram SVG renders at >= 0.9x its viewBox aspect at desktop/mobile/print."),
    CheckSpec("V2", 3, 4, "auto", "Model diagram quality",
              "No auto date/time internals (DateTableTemplate*/LocalDateTable*) drawn in the "
              "model diagram.", doc_types=("technical",)),
    CheckSpec("V3", 3, 4, "render", "Wireframe occlusion handling",
              "No wireframe card fully hides another's title; overlaps render as ghost outlines."),
    CheckSpec("V4", 3, 3, "render", "Lineage correctness + interaction",
              "Lineage edges match model relationships/usages; anchors resolve."),
    CheckSpec("V5", 3, 3, "render", "Pan-zoom integration hygiene",
              "Zoom/pan works, degrades to static SVG; vendor JS only in docs with diagrams."),

    # Pillar 4 — Design, UX & Accessibility (15)
    CheckSpec("D1", 4, 3, "manual", "Brand consistency",
              "One hero gradient/family and identical component styling across all docs."),
    CheckSpec("D2", 4, 2, "render", "Dark mode",
              "Toggle present in all docs; everything legible in dark mode."),
    CheckSpec("D3", 4, 2, "render", "Mobile 390px",
              "No horizontal page scroll at 390px; tables scroll or stack."),
    CheckSpec("D4", 4, 3, "render", "Print/PDF",
              "@media print present; diagrams full-size static; no interactive chrome in print."),
    CheckSpec("D5", 4, 2, "render", "Accessibility",
              "Diagram SVGs carry roles/labels; pills meet WCAG AA contrast."),
    CheckSpec("D6", 4, 3, "auto", "Microcopy",
              "Correct pluralization (never 'asset(s)'), no machine phrasing "
              "('File.Contents(s)').", prose_fixable=True),

    # Pillar 5 — Differentiators & Claim Integrity (10)
    CheckSpec("X1", 5, 3, "manual", "Provenance honesty",
              "Every section pill matches its true source; AI-inferred never masquerades as "
              "Extracted."),
    CheckSpec("X2", 5, 3, "manual", "Human-context precedence + discrepancy surfacing",
              "Human facts override extraction; contradictions render a Discrepancy callout."),
    CheckSpec("X3", 5, 2, "auto", "Offline guarantee",
              "No external URL in any src/href attribute of any rendered HTML document."),
    CheckSpec("X4", 5, 2, "render", "Completeness meter accuracy",
              "The displayed completeness percentage matches the recomputed empty-human-field "
              "count."),
]

CHECKS_BY_ID: dict[str, CheckSpec] = {c.id: c for c in BENCHMARK_CHECKS}

# Gate -> (triggering check IDs, score cap). Only the gates this scorer can
# actually evaluate pre-render; G2/G5-G8 belong to the render suite.
_GATES: list[tuple[str, tuple[str, ...], int]] = [
    ("G1", ("T1",), 75),
    ("G3", ("T2", "T3"), 80),
    ("G4", ("T4",), 78),
]

# The 19 canonical technical-document sections (render/markdown.py) — C1.
CANONICAL_TECHNICAL_SECTIONS: tuple[str, ...] = (
    "1. Document Control", "2. Executive Summary", "3. Business Requirements",
    "4. Audience & Stakeholders", "5. Data Sources", "6. Data Model",
    "7. Measures & Calculations (DAX Dictionary)", "8. Report Pages & Visuals",
    "9. Filters, Slicers & Navigation", "10. Row-Level Security (RLS)",
    "11. Refresh, Gateway & Performance", "12. Deployment & Environment",
    "13. Access & Permissions", "14. Data Dictionary / Glossary",
    "15. Known Issues, Assumptions & Limitations", "16. Model Health & AI Recommendations",
    "17. Support & Maintenance", "18. Appendix & Sign-off",
    "19. Methodology & Guarantees",
)


# --------------------------------------------------------------------------
# Narrative-field registry — shared with the Senior Reviewer so both walk the
# exact same (location, text, setter) triples each generator already defines
# for the critic/grounding passes. Import is lazy: the generators import
# ``agents.critic``/``agents.grounding``, and neither imports this module, so
# there is no cycle — but keeping it lazy also keeps ``import benchmark``
# cheap for callers that only want the spec.
# --------------------------------------------------------------------------

def narrative_triples_for(doc_type: str, doc: Any) -> list[tuple[str, str, Callable[[str], None]]]:
    from .generators import audit as _audit
    from .generators import executive as _executive
    from .generators import technical as _technical
    from .generators import user_guide as _user_guide

    registry: dict[str, Callable[[Any], list]] = {
        "technical": _technical._narrative_triples,
        "audit": _audit._narrative_triples,
        "executive": _executive._narrative_triples,
        "user-guide": _user_guide._narrative_triples,
    }
    return registry[doc_type](doc)


def bundle_fields(docs: dict[str, Any]) -> dict[str, list[tuple[str, str, Callable[[str], None]]]]:
    return {dtype: narrative_triples_for(dtype, doc) for dtype, doc in docs.items()}


# Locations where the punt phrase is legitimately allowed to remain: the
# unexplained-description cells the Column Describer / DAX Translator fall
# back to. Everywhere else it's a leak (T1/G1).
_PUNT_WHITELIST_RE = re.compile(r"^(?:measure_catalog\.measures\[\d+\]\.(?:plain_english|caveats)"
                                r"|glossary\[\d+\]\.plain_definition)$")

_DOUBLED_PUNCT_RE = re.compile(r"(?<!\.)\.\.(?!\.)|——|\.\s+,|\.,")
_DOUBLED_PHRASE_RE = re.compile(r"\b(\w+(?:\s+\w+){2,5})\s*[—–-]+\s*(?:a\s+)?\1\b",
                                re.IGNORECASE)
_MACHINE_PLURAL_RE = re.compile(r"\b[A-Za-z][A-Za-z.]*\(s\)")
_FILE_CONTENTS_RE = re.compile(r"File\.Contents", re.IGNORECASE)
# Fenced blocks and inline code spans are stripped before any prose-phrasing
# scan — a raw M connection string (`Excel.Workbook(File.Contents(...))`)
# shown *as code* is legitimate; the D6 defect is that phrasing leaking into
# prose ("1 File.Contents(s)").
_MD_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_EXTERNAL_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["']https?://""", re.IGNORECASE)
_AUTO_DATETIME_NAME_RE = re.compile(r"^(?:DateTableTemplate|LocalDateTable)", re.IGNORECASE)
_JUNK_GLOSSARY_RE = re.compile(r"^select\d*$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Scorer
# --------------------------------------------------------------------------

class _Renders:
    """Lazy, memoized in-memory renders — one md/html render per doc at most,
    each individually guarded so a renderer error degrades only the checks
    that needed that render."""

    def __init__(self, docs: dict[str, Any]):
        self._docs = docs
        self._md: dict[str, Optional[str]] = {}
        self._html: dict[str, Optional[str]] = {}

    def md(self, dtype: str) -> Optional[str]:
        if dtype not in self._md:
            self._md[dtype] = self._render(dtype, "md")
        return self._md[dtype]

    def html(self, dtype: str) -> Optional[str]:
        if dtype not in self._html:
            self._html[dtype] = self._render(dtype, "html")
        return self._html[dtype]

    def _render(self, dtype: str, fmt: str) -> Optional[str]:
        if dtype not in self._docs:
            return None
        from ..render import registry
        try:
            return registry.RENDERERS[dtype][fmt](self._docs[dtype])
        except Exception:
            return None


def _all_triples(docs: dict[str, Any], *, include_fenced: bool = False) -> list[tuple[str, str, str]]:
    """Flattened ``(doc_type, location, text)`` across the whole bundle,
    skipping empty fields. Fenced-code fields are skipped by default (same
    exclusion the critic and grounding passes apply when *rewriting*), but
    leak *detection* (T1/T2) must still scan them — a narrative field that
    happens to embed a code snippet can still carry a punt leak in its
    prose around the fence."""
    out: list[tuple[str, str, str]] = []
    for dtype, triples in bundle_fields(docs).items():
        for location, text, _setter in triples:
            if text and (include_fenced or "```" not in text):
                out.append((dtype, location, text))
    return out


def _health_scores(docs: dict[str, Any]) -> dict[str, int]:
    """Structured health-score integers per doc type, wherever one exists."""
    scores: dict[str, int] = {}
    audit = docs.get("audit")
    if audit is not None and getattr(audit, "health", None) is not None:
        scores["audit"] = int(audit.health.overall)
    technical = docs.get("technical")
    if technical is not None and getattr(technical, "health_score", None):
        overall = technical.health_score.get("overall")
        if overall is not None:
            scores["technical"] = int(overall)
    executive = docs.get("executive")
    if executive is not None and getattr(executive, "health", None) is not None:
        scores["executive"] = int(executive.health.overall)
    return scores


def _check_counts(doc: Any) -> Optional[tuple[int, int, int, int]]:
    if doc is None:
        return None
    run = getattr(doc, "checks_run", 0)
    if not run:
        return None
    return (run, getattr(doc, "checks_passed", 0), getattr(doc, "checks_failed", 0),
            getattr(doc, "checks_suppressed", 0))


def _metadata_of(docs: dict[str, Any]) -> Optional[Any]:
    for dtype in ("technical", "audit", "executive", "user-guide"):
        doc = docs.get(dtype)
        if doc is not None and getattr(doc, "metadata", None) is not None:
            return doc.metadata
    return None


def _section_body(md: str, heading_prefix: str) -> str:
    """Body text of the ``## {heading_prefix}...`` section of a markdown
    render (up to the next h2), empty string when the heading is absent."""
    m = re.search(rf"^## {re.escape(heading_prefix)}.*?$", md, re.MULTILINE)
    if not m:
        return ""
    rest = md[m.end():]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[:nxt.start()] if nxt else rest


# -- Individual auto checks --------------------------------------------------
# Each takes the shared evaluation state and returns (passed, detail,
# locations). They never raise to the caller: run_benchmark wraps each call.

def _eval_t1(docs, renders, model, ai_context):
    hits = [f"{dtype}:{loc}" for dtype, loc, text in _all_triples(docs, include_fenced=True)
            if _PUNT_PHRASE_RE.search(text) and not _PUNT_WHITELIST_RE.match(loc)]
    if hits:
        return False, f"punt phrase leaked into {len(hits)} narrative field(s)", hits
    return True, "", []


def _eval_t2(docs, renders, model, ai_context):
    scores = _health_scores(docs)
    if not scores:
        return None, "no health score present in bundle", []
    canonical = set(scores.values())
    if len(canonical) > 1:
        return False, f"structured health scores disagree: {scores}", []
    actual = canonical.pop()
    bad = []
    for dtype, loc, text in _all_triples(docs, include_fenced=True):
        for m in _SCORE_MENTION_RE.finditer(text):
            if int(m.group(1)) != actual:
                bad.append(f"{dtype}:{loc}")
    if bad:
        return False, f"prose states a health score other than {actual}", bad
    return True, "", []


def _eval_t3(docs, renders, model, ai_context):
    tuples = {dtype: _check_counts(docs.get(dtype)) for dtype in ("audit", "technical")}
    seen = {dtype: t for dtype, t in tuples.items() if t is not None}
    if ai_context is not None and getattr(ai_context, "checks_ledger", None):
        ledger = ai_context.checks_ledger
        try:
            seen["ledger"] = (ledger["run"], ledger["passed"], ledger["failed"],
                              ledger["suppressed"])
        except (KeyError, TypeError):
            pass
    if len(seen) < 2:
        return None, "fewer than two check-count sources in this bundle", []
    if len(set(seen.values())) > 1:
        return False, f"check counts disagree: {seen}", []
    return True, "", []


def _eval_t4(docs, renders, model, ai_context):
    audit = docs.get("audit")
    if model is None or audit is None:
        return None, "needs the model and the audit document", []
    from .consistency import build_audit_verdicts, check_deterministic_consistency
    verdicts = build_audit_verdicts(model, audit)
    bad: list[str] = []
    for dtype, doc in docs.items():
        if dtype == "audit":
            continue
        fields = [(loc, text) for loc, text, _ in narrative_triples_for(dtype, doc)
                  if text and "```" not in text]
        conflicts = check_deterministic_consistency(fields, verdicts, warn=lambda _m: None)
        bad.extend(f"{dtype}:{loc}" for loc in conflicts)
    if bad:
        return False, "prose contradicts a deterministic audit verdict", bad
    return True, "", []


def _eval_t5(docs, renders, model, ai_context):
    return None, "fixture-specific expectations; scored by the golden-fixture suite", []


def _eval_t6(docs, renders, model, ai_context):
    bad: list[str] = []
    for dtype, loc, text in _all_triples(docs):
        if (_DOUBLED_PUNCT_RE.search(text) or _DOUBLED_PHRASE_RE.search(text)
                or is_low_content_fragment(text)):
            bad.append(f"{dtype}:{loc}")
    if bad:
        return False, "prose mechanics defect (doubled punctuation/phrase or orphan fragment)", bad
    return True, "", []


def _eval_c1(docs, renders, model, ai_context):
    md = renders.md("technical")
    if md is None:
        return None, "technical doc absent or failed to render", []
    missing = [s for s in CANONICAL_TECHNICAL_SECTIONS if f"## {s}" not in md]
    if missing:
        return False, f"missing canonical sections: {', '.join(missing)}", []
    doc = docs["technical"]
    if re.search(r"model diagram is in section|diagram is in section", md, re.IGNORECASE) \
            and not doc.semantic_model.tables:
        return False, "references a model diagram that did not render", []
    return True, "", []


def _eval_c2(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None:
        return None, "technical doc absent", []
    measures = doc.measure_catalog.measures
    if not measures:
        return True, "no measures in model", []
    incomplete = [m.name for m in measures if not m.plain_english or not m.calculation_logic]
    if incomplete:
        return False, (f"{len(incomplete)}/{len(measures)} measure cards missing description "
                       f"or calculation logic"), []
    return True, "", []


def _eval_c3(docs, renders, model, ai_context):
    doc = docs.get("audit")
    if doc is None:
        return None, "audit doc absent", []
    bad = [r.issue for r in doc.recommendations
           if not (r.why_it_matters and r.suggested_fix and r.expected_benefit and r.effort)]
    findings_missing_rule = sum(
        1 for group in (doc.dax_findings, doc.performance_risks, doc.governance)
        for f in group if not f.rule_id)
    if bad or findings_missing_rule:
        return False, (f"{len(bad)} recommendation(s) incomplete; "
                       f"{findings_missing_rule} finding(s) missing a rule ID"), []
    return True, "", []


def _eval_c4(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None or not doc.requirements_matrix:
        return None, "no requirements matrix in this bundle", []
    bad = []
    for row in doc.requirements_matrix:
        status = (row.get("status") or "").lower()
        evidence = row.get("evidence") or []
        if status == "gap" and evidence:
            bad.append(f"'{row.get('text', '')[:60]}' marked Gap but lists evidence")
        if status == "covered" and not evidence:
            bad.append(f"'{row.get('text', '')[:60]}' marked Covered without evidence")
    if bad:
        return False, "; ".join(bad), []
    return True, "", []


def _eval_c5(docs, renders, model, ai_context):
    doc = docs.get("executive")
    if doc is None:
        return None, "executive doc absent", []
    md = renders.md("executive")
    problems = []
    if doc.health is None:
        problems.append("no health score on the executive document")
    if md is not None:
        prose = _MD_CODE_RE.sub("", md)
        if _FILE_CONTENTS_RE.search(prose) or _MACHINE_PLURAL_RE.search(prose):
            problems.append("machine phrasing in executive prose")
    if problems:
        return False, "; ".join(problems), []
    return True, "", []


def _eval_c6(docs, renders, model, ai_context):
    doc = docs.get("user-guide")
    if doc is None:
        return None, "user guide absent", []
    junk = [t.term for t in doc.glossary
            if _JUNK_GLOSSARY_RE.match(t.term) or _AUTO_DATETIME_NAME_RE.match(t.term)]
    missing_purpose = [p.page_title for p in doc.pages if not p.purpose]
    locations = [f"user-guide:glossary[{i}].plain_definition"
                 for i, t in enumerate(doc.glossary) if t.term in junk]
    if junk or missing_purpose:
        return False, (f"junk glossary terms: {junk or 'none'}; pages missing purpose: "
                       f"{missing_purpose or 'none'}"), locations
    return True, "", []


def _eval_c7(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None:
        return None, "technical doc absent", []
    rows = doc.semantic_model.data_dictionary
    if not rows:
        return None, "no data dictionary rows", []
    described = sum(1 for r in rows
                    if (r.get("description") or "").strip()
                    and not _PUNT_PHRASE_RE.search(r.get("description", "")))
    pct = round(100 * described / len(rows))
    if pct < 80:
        return False, f"only {pct}% of {len(rows)} data-dictionary rows carry a real description", []
    return True, f"{pct}% described", []


def _eval_c9(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None:
        return None, "technical doc absent", []
    if doc.security.roles:
        # Roles listed at all is the pass bar; filter detail depends on what
        # the source file exposes.
        return True, f"{len(doc.security.roles)} RLS role(s) documented", []
    md = renders.md("technical")
    if md is None:
        return None, "technical doc failed to render", []
    body = _section_body(md, "10. Row-Level Security")
    if len(body.strip()) > 20:
        return True, "explicit no-RLS statement present", []
    return False, "no RLS roles and no explicit no-RLS statement in section 10", []


def _eval_c10(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None:
        return None, "technical doc absent", []
    lineage = doc.lineage
    if lineage.source_systems or lineage.data_sources_inventory or lineage.transformations:
        return True, "", []
    if model is not None and not getattr(model, "data_sources", None):
        return True, "model declares no data sources", []
    return False, "no lineage/data-source documentation", []


def _eval_c11(docs, renders, model, ai_context):
    meta = _metadata_of(docs)
    if meta is None:
        return None, "no document metadata found", []
    if getattr(meta, "owner", None):
        return True, "", []
    return False, "no report owner recorded", []


def _eval_c12(docs, renders, model, ai_context):
    meta = _metadata_of(docs)
    if meta is None:
        return None, "no document metadata found", []
    if getattr(meta, "assumptions", None):
        return True, "", []
    return False, "no assumptions/limitations statement recorded", []


def _eval_c13(docs, renders, model, ai_context):
    # The deterministic translator intentionally states mechanics only; the
    # stronger business-rationale contract applies when the AI translator
    # actually ran and supplied job-shared translations.
    if ai_context is None or not getattr(ai_context, "translations", None):
        return None, "AI measure translations not present", []
    technical = docs.get("technical")
    if technical is None:
        return None, "technical document not present", []
    weak = []
    for i, measure in enumerate(technical.measure_catalog.measures):
        plain = (measure.plain_english or "").strip()
        logic = (measure.calculation_logic or "").strip()
        name_words = re.sub(r"\W+", " ", measure.name.casefold()).strip()
        plain_words = re.sub(r"\W+", " ", plain.casefold()).strip()
        too_thin = len(re.findall(r"[A-Za-z]+", plain)) < 7
        name_echo = plain_words == name_words or plain_words in {
            f"the {name_words}", f"{name_words} measure", f"the {name_words} measure",
        }
        same_as_mechanics = plain_words == re.sub(r"\W+", " ", logic.casefold()).strip()
        if (not plain or not logic or too_thin or name_echo or same_as_mechanics
                or not _adds_meaning(plain, measure.name, logic)):
            weak.append(f"technical:measure_catalog.measures[{i}].plain_english")
    if weak:
        return False, "measure definitions lack distinct business meaning or interpretation rationale", weak
    return True, "all measure definitions include meaning and interpretation rationale", []


# Words that carry no meaning of their own when deciding whether a measure
# description says anything the name and the mechanics didn't already.
_C13_STOPWORDS = frozenset(
    "a an the this that these those and or but of in on at for to with by as is are was were be "
    "been being it its from into over under per each any all no not calculates calculated "
    "calculate computes compute returns return measure value values amount total sum count number "
    "based which when where than then if else via using".split()
)


def _adds_meaning(plain: str, name: str, logic: str) -> bool:
    """Does ``plain`` say anything the measure's name and mechanics didn't?

    C13 asks whether a measure description explains business meaning rather than
    echoing the name. That used to be approximated with a whitelist of
    "rationale" keywords (so/because/track/compare/...), which is whack-a-mole:
    every unlisted phrasing reads as a failure. It false-flagged real prose
    twice — first "…indicator for comparing cost centers…", then, after the word
    list was widened, "…core metric for budget performance and corrective
    action" and "…critical trigger for budget review and re-forecasting". Both
    plainly explain the why; neither contained a listed word. A whitelist cannot
    enumerate how people write.

    So measure the thing the check actually cares about: information *added*.
    A description that contributes several content words beyond the measure's
    own name and its restated mechanics is saying something extra — whatever
    vocabulary it chose. One that doesn't ("The total spend.") is exactly the
    echo C13 exists to catch.
    """
    def _tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", (text or "").casefold())
                if len(w) > 2 and w not in _C13_STOPWORDS}

    novel = _tokens(plain) - _tokens(name) - _tokens(logic)
    return len(novel) >= 3


def _eval_c8_floor(docs, renders, model, ai_context):
    """C8: is refresh documented well enough for a new owner to re-establish it?

    This used to defer to the Senior Reviewer once a bare schedule string
    existed. That was a mistake: every part of the question is a *fact* the
    scorer can check, and handing a checkable fact to a judge invites it to be
    wrong about it. It was — twice, verifiably: on bundles whose technical §11
    plainly read "Refresh schedule: Daily 06:00 UTC via on-premises gateway",
    the judge reported "no refresh schedule is present in any document" and
    failed the check. Per this module's own principle, deterministic checks are
    the trustworthy ones; so C8 now answers from the artifacts.

    Passes when the schedule (or refresh notes) is recorded AND the data
    sources feeding it are documented — the two things a new owner needs. A
    sparse intake that genuinely documents no refresh still fails, which is
    honest rather than generous.
    """
    meta = _metadata_of(docs)
    if meta is None:
        return None, "no document metadata found", []
    refresh = getattr(meta, "refresh_schedule", None) or getattr(meta, "refresh_notes", None)
    if not refresh:
        return False, "no refresh schedule or refresh notes recorded", []

    technical = docs.get("technical")
    lineage = getattr(technical, "lineage", None) if technical is not None else None
    sources_documented = bool(
        (getattr(lineage, "source_systems", None) or [])
        or (getattr(lineage, "data_sources_inventory", None) or [])
        or (getattr(model, "data_sources", None) or [])
    )
    if not sources_documented:
        return False, ("a refresh schedule is recorded but no data source is documented, so a new "
                       "owner still could not re-establish refresh"), []
    return True, "refresh schedule and its data sources are both documented", []


def _eval_v2(docs, renders, model, ai_context):
    doc = docs.get("technical")
    if doc is None:
        return None, "technical doc absent", []
    drawn = [t.get("name", "") for t in doc.semantic_model.tables]
    internals = [n for n in drawn if _AUTO_DATETIME_NAME_RE.match(n)]
    if internals:
        return False, f"auto date/time internals drawn in model diagram: {internals}", []
    return True, "", []


def _eval_d6(docs, renders, model, ai_context):
    bad = []
    for dtype in docs:
        md = renders.md(dtype)
        if md is None:
            continue
        # Prose only: code spans stripped, and markdown table rows dropped —
        # a technical-doc inventory cell legitimately holds a raw source
        # type like "File.Contents"; the D6 defect is that phrasing (or an
        # "asset(s)" pluralization) leaking into sentences a person reads.
        prose = "\n".join(line for line in _MD_CODE_RE.sub("", md).splitlines()
                          if not line.lstrip().startswith("|"))
        if _FILE_CONTENTS_RE.search(prose) or _MACHINE_PLURAL_RE.search(prose):
            bad.append(dtype)
    if bad:
        return False, f"machine phrasing/pluralization in: {', '.join(bad)}", []
    return True, "", []


def _eval_x3(docs, renders, model, ai_context):
    bad = []
    for dtype in docs:
        html = renders.html(dtype)
        if html is None:
            continue
        if _EXTERNAL_URL_RE.search(html):
            bad.append(dtype)
    if bad:
        return False, f"external URL in src/href of: {', '.join(bad)}", []
    return True, "", []


_AUTO_EVALUATORS: dict[str, Callable] = {
    "T1": _eval_t1, "T2": _eval_t2, "T3": _eval_t3, "T4": _eval_t4,
    "T5": _eval_t5, "T6": _eval_t6,
    "C1": _eval_c1, "C2": _eval_c2, "C3": _eval_c3, "C4": _eval_c4,
    "C5": _eval_c5, "C6": _eval_c6, "C7": _eval_c7, "C8": _eval_c8_floor,
    "C9": _eval_c9, "C10": _eval_c10, "C11": _eval_c11, "C12": _eval_c12,
    "C13": _eval_c13,
    "V2": _eval_v2, "D6": _eval_d6, "X3": _eval_x3,
}


def run_benchmark(
    docs: dict[str, Any],
    *,
    model: Any = None,
    ai_context: Any = None,
) -> BenchmarkReport:
    """Evaluate every ``auto`` check over the assembled documents (plus their
    in-memory renders) and return a full :class:`BenchmarkReport` — one
    :class:`CheckResult` per spec ID, ``passed=None`` for anything this
    scorer can't evaluate (judge/render/manual, or a check whose inputs are
    missing from this bundle). Never mutates a document, never raises for a
    single misbehaving check."""
    renders = _Renders(docs)
    results: list[CheckResult] = []
    for spec in BENCHMARK_CHECKS:
        evaluator = _AUTO_EVALUATORS.get(spec.id)
        if evaluator is None:
            results.append(CheckResult(spec.id, None, f"{spec.method}-method; not evaluated by scorer"))
            continue
        try:
            passed, detail, locations = evaluator(docs, renders, model, ai_context)
        except Exception as exc:
            passed, detail, locations = None, f"check errored: {type(exc).__name__}: {exc}", []
        results.append(CheckResult(spec.id, passed, detail, locations))

    by_id = {r.check_id: r for r in results}
    score = sum(CHECKS_BY_ID[r.check_id].points for r in results if r.passed)
    max_evaluated = sum(CHECKS_BY_ID[r.check_id].points for r in results if r.passed is not None)
    gates = [gate for gate, triggers, _cap in _GATES
             if any(by_id[t].passed is False for t in triggers)]
    for gate, triggers, cap in _GATES:
        if gate in gates:
            score = min(score, cap)
    return BenchmarkReport(results=results, score=score,
                           max_evaluated_points=max_evaluated, gates_triggered=gates)
