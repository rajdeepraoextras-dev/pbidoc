"""Tests for ``pbicompass.agents.reviewer`` — the benchmark-gated Senior
Reviewer loop. Uses in-process fake clients branching on the system prompt
(the pattern from ``test_critic.py``/``test_agents.py``); no network.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents.benchmark import narrative_triples_for
from pbicompass.agents.context import JobAIContext
from pbicompass.agents.generators import DOCUMENT_TYPES
from pbicompass.agents.reviewer import QualityReport, run_review_loop
from pbicompass.render import registry
from pbicompass.schemas.model import SemanticModel

CS_FIXTURE = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"

_INTAKE = dict(
    owner="Jane Doe",
    refresh="Daily 06:00 UTC",
    assumptions="Spend data excludes intercompany transfers.",
)


def _model():
    return SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))


def _make_docs(model):
    return {dtype: gen.generate(model, None, **_INTAKE)
            for dtype, gen in DOCUMENT_TYPES.items()}


def _ai_context() -> JobAIContext:
    # A minimal shared context: the digest is what grounding and the
    # reviewer treat as ground truth; content is irrelevant to the fakes.
    return JobAIContext(model_digest="Tables: Actual, Plan, Department. Measures: Total Spend.")


class FakeReviewerClient:
    """Branches on the system prompt: Senior Reviewer calls get scripted
    responses (one per call, in order); grounding calls get no claims."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.reviewer_calls = 0
        self.grounding_calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort=None) -> dict:
        if "senior QA partner" in system:
            self.reviewer_calls += 1
            if not self.responses:
                return {"verdicts": [], "fixes": [], "gaps": []}
            return self.responses.pop(0)
        if "fact-checker" in system:
            self.grounding_calls += 1
            return {"claims": []}
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


class RaisingClient:
    def complete_json(self, system, user, schema, *, effort=None):
        raise RuntimeError("provider down")


def _pass_all_verdicts():
    return [{"check_id": "C8", "passed": True, "note": "adequate"},
            {"check_id": "C13", "passed": True, "note": "explains why"}]


class ReviewerLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = _model()

    def test_fix_applied_and_loop_converges(self):
        docs = _make_docs(self.model)
        docs["executive"].purpose = "This dashboard has issues.. It tracks spend."  # T6 defect
        fixed_text = "Finance managers use this report to compare actual spend against plan."
        client = FakeReviewerClient([{
            "verdicts": _pass_all_verdicts(),
            "fixes": [{"doc_type": "executive", "location": "purpose",
                       "revised_text": fixed_text, "check_id": "T6"}],
            "gaps": [],
        }])
        report = run_review_loop(docs, self.model, client, None, _ai_context())
        self.assertEqual(docs["executive"].purpose, fixed_text)
        self.assertEqual(report.iterations, 1)
        self.assertEqual(client.reviewer_calls, 1)
        self.assertTrue(report.reviewer_ran)
        self.assertNotIn("T6", report.unresolved)

    def test_iteration_cap_stops_at_two_cycles(self):
        docs = _make_docs(self.model)
        docs["executive"].purpose = "This dashboard has issues.. It tracks spend."
        # Every "fix" changes the text but never removes the defect.
        def bad_fix(n):
            return {"verdicts": _pass_all_verdicts(),
                    "fixes": [{"doc_type": "executive", "location": "purpose",
                               "revised_text": f"Still broken.. attempt {n}.",
                               "check_id": "T6"}],
                    "gaps": []}
        client = FakeReviewerClient([bad_fix(1), bad_fix(2), bad_fix(3)])
        report = run_review_loop(docs, self.model, client, None, _ai_context())
        self.assertEqual(report.iterations, 2)
        self.assertEqual(client.reviewer_calls, 2)
        self.assertIn("T6", report.unresolved)

    def test_graceful_degradation_on_llm_failure(self):
        docs = _make_docs(self.model)
        before = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        report = run_review_loop(docs, self.model, RaisingClient(), None, _ai_context())
        after = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        self.assertEqual(before, after, "a failed reviewer must never change a document")
        self.assertIsInstance(report, QualityReport)
        self.assertFalse(report.reviewer_ran)
        self.assertEqual(report.iterations, 0)
        self.assertTrue(report.results)

    def test_offline_returns_deterministic_report_only(self):
        docs = _make_docs(self.model)
        report = run_review_loop(docs, self.model, None, None, _ai_context())
        self.assertFalse(report.reviewer_ran)
        self.assertEqual(report.iterations, 0)
        self.assertGreater(report.max_evaluated_points, 0)

    def test_no_downgrade_guards_reject_bad_fixes(self):
        docs = _make_docs(self.model)
        original_purpose = docs["executive"].purpose
        self.assertTrue(original_purpose)
        warnings: list[str] = []
        client = FakeReviewerClient([{
            "verdicts": _pass_all_verdicts(),
            "fixes": [
                # 1. Punt-phrase downgrade over real prose — rejected.
                {"doc_type": "executive", "location": "purpose",
                 "revised_text": "Unknown — requires business confirmation.",
                 "check_id": "T1"},
                # 2. Meta-commentary — rejected by apply_results.
                {"doc_type": "executive", "location": "business_value",
                 "revised_text": "Remove the duplicated entry as it is identical to "
                                 "glossary[15].plain_definition.",
                 "check_id": "T6"},
                # 3. Unknown location — ignored.
                {"doc_type": "executive", "location": "no_such_field",
                 "revised_text": "Anything.", "check_id": "T6"},
                # 4. Unknown doc type — ignored.
                {"doc_type": "wiki", "location": "purpose",
                 "revised_text": "Anything.", "check_id": "T6"},
            ],
            "gaps": [],
        }])
        before = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        report = run_review_loop(docs, self.model, client, warnings.append, _ai_context())
        after = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        self.assertEqual(before, after, "every guarded fix must be rejected")
        self.assertEqual(report.iterations, 0)
        self.assertTrue(any("punt-phrase downgrade" in w for w in warnings))

    def test_gaps_recorded_without_mutation_or_extra_cycles(self):
        docs = _make_docs(self.model)
        client = FakeReviewerClient([{
            "verdicts": _pass_all_verdicts(),
            "fixes": [],
            "gaps": [{"check_id": "C7", "doc_type": "technical",
                      "description": "Column descriptions require business input."}],
        }])
        before = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        report = run_review_loop(docs, self.model, client, None, _ai_context())
        after = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        self.assertEqual(before, after)
        self.assertEqual(client.reviewer_calls, 1)
        self.assertEqual(len(report.gaps), 1)
        self.assertEqual(report.gaps[0]["check_id"], "C7")

    def test_failed_judge_verdict_lands_in_unresolved(self):
        docs = _make_docs(self.model)
        client = FakeReviewerClient([{
            "verdicts": [{"check_id": "C13", "passed": False, "note": "name-echo descriptions"},
                         {"check_id": "C8", "passed": True, "note": "ok"}],
            "fixes": [],
            "gaps": [],
        }])
        report = run_review_loop(docs, self.model, client, None, _ai_context())
        self.assertIn("C13", report.unresolved)
        self.assertNotIn("C8", report.unresolved)

    def test_review_artifacts_never_reach_rendered_output(self):
        docs = _make_docs(self.model)
        docs["executive"].purpose = "This dashboard has issues.. It tracks spend."
        client = FakeReviewerClient([{
            "verdicts": _pass_all_verdicts(),
            "fixes": [{"doc_type": "executive", "location": "purpose",
                       "revised_text": "Finance managers compare actual spend against plan here.",
                       "check_id": "T6"}],
            "gaps": [],
        }])
        run_review_loop(docs, self.model, client, None, _ai_context())
        for dtype, doc in docs.items():
            for fmt in ("md", "html"):
                text = registry.RENDERERS[dtype][fmt](doc)
                self.assertNotIn("Senior Reviewer", text, f"{dtype}.{fmt}")
                self.assertNotIn("benchmark v", text.lower(), f"{dtype}.{fmt}")
                self.assertNotIn("check_id", text, f"{dtype}.{fmt}")

    def test_meta_commentary_embedded_mid_fix_never_reaches_document(self):
        # A fix that embeds a meta-commentary reference mid-paragraph is
        # rejected wholesale at the shared apply_results choke point — the
        # original (defective but honest) text stays rather than shipping a
        # contaminated replacement.
        docs = _make_docs(self.model)
        original = "This dashboard has issues.. It tracks spend."
        docs["executive"].purpose = original
        client = FakeReviewerClient([{
            "verdicts": _pass_all_verdicts(),
            "fixes": [{"doc_type": "executive", "location": "purpose",
                       "revised_text": "Finance managers compare spend against plan. Remove the "
                                       "duplicated entry as it is identical to "
                                       "glossary[15].plain_definition. Data refreshes daily.",
                       "check_id": "T6"}],
            "gaps": [],
        }])
        run_review_loop(docs, self.model, client, None, _ai_context())
        self.assertEqual(docs["executive"].purpose, original)
        self.assertNotIn("glossary[15]", docs["executive"].purpose)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
