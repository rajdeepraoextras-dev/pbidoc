"""Tests for the grounding & verification pass (Phase 3 of
``AI_NATIVE_PLAN.md``): ``agents/grounding.py``'s LLM-routed fact-check over
a document's own narrative fields, plus end-to-end wiring into the
technical-document generator."""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents.critic import apply_results
from pbicompass.agents.generators import TechnicalDocumentationGenerator
from pbicompass.agents.grounding import UNVERIFIABLE_TEXT, apply_grounding_pass
from pbicompass.parsers import detect_and_parse

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _model():
    return detect_and_parse(FIXTURE)


class FakeGroundingClient:
    """A minimal LLMClient that reports canned claims for whatever
    ``claims`` list is handed to it, ignoring the actual digest content."""

    def __init__(self, claims: list[dict]):
        self.claims = claims
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        return {"claims": self.claims}


class ApplyGroundingPassTest(unittest.TestCase):
    def test_offline_is_a_noop(self):
        results = apply_grounding_pass(
            [("a", "Some claim.")], None, model_digest="digest text",
        )
        self.assertEqual(results, {})

    def test_missing_digest_is_a_noop(self):
        client = FakeGroundingClient([])
        results = apply_grounding_pass([("a", "Some claim.")], client, model_digest=None)
        self.assertEqual(results, {})
        self.assertEqual(client.calls, 0)

    def test_failing_client_degrades_silently_with_a_warning(self):
        class _FailingClient:
            def complete_json(self, system, user, schema, *, effort=None):
                raise RuntimeError("boom")

        warnings: list[str] = []
        results = apply_grounding_pass(
            [("a", "Some claim.")], _FailingClient(), model_digest="digest", warn=warnings.append,
        )
        self.assertEqual(results, {})
        self.assertTrue(any("Grounding" in w for w in warnings))

    def test_contradicted_claim_is_corrected(self):
        client = FakeGroundingClient([
            {"location": "a", "quote": "12 tables", "verdict": "contradicted", "correction": "9 tables"},
        ])
        results = apply_grounding_pass(
            [("a", "This model has 12 tables.")], client, model_digest="digest",
        )
        self.assertEqual(results["a"], "This model has 9 tables.")

    def test_unverifiable_claim_is_downgraded(self):
        client = FakeGroundingClient([
            {"location": "a", "quote": "used by the finance team",
             "verdict": "unverifiable", "correction": ""},
        ])
        results = apply_grounding_pass(
            [("a", "This page is used by the finance team.")], client, model_digest="digest",
        )
        self.assertEqual(results["a"], f"This page is {UNVERIFIABLE_TEXT}.")

    def test_supported_claim_is_left_untouched(self):
        client = FakeGroundingClient([
            {"location": "a", "quote": "3 tables", "verdict": "supported", "correction": ""},
        ])
        results = apply_grounding_pass(
            [("a", "This model has 3 tables.")], client, model_digest="digest",
        )
        self.assertEqual(results, {})

    def test_contradicted_claim_without_a_correction_is_ignored(self):
        # A contradicted verdict with no usable correction can't safely be
        # applied — better to leave the original text than insert an empty
        # string in its place.
        client = FakeGroundingClient([
            {"location": "a", "quote": "12 tables", "verdict": "contradicted", "correction": ""},
        ])
        results = apply_grounding_pass(
            [("a", "This model has 12 tables.")], client, model_digest="digest",
        )
        self.assertEqual(results, {})

    def test_quote_not_present_in_text_is_ignored(self):
        client = FakeGroundingClient([
            {"location": "a", "quote": "nonexistent phrase", "verdict": "contradicted", "correction": "x"},
        ])
        results = apply_grounding_pass([("a", "Some other text.")], client, model_digest="digest")
        self.assertEqual(results, {})

    def test_unknown_location_is_ignored(self):
        client = FakeGroundingClient([
            {"location": "nonexistent", "quote": "x", "verdict": "contradicted", "correction": "y"},
        ])
        results = apply_grounding_pass([("a", "Some text.")], client, model_digest="digest")
        self.assertEqual(results, {})

    def test_fenced_code_block_is_never_sent(self):
        client = FakeGroundingClient([])
        apply_grounding_pass(
            [("a", "Apply this fix:\n```\nDelete();\n```")], client, model_digest="digest",
        )
        self.assertEqual(client.calls, 0)

    def test_multiple_claims_apply_in_sequence_on_the_same_location(self):
        client = FakeGroundingClient([
            {"location": "a", "quote": "12 tables", "verdict": "contradicted", "correction": "9 tables"},
            {"location": "a", "quote": "used by finance", "verdict": "unverifiable", "correction": ""},
        ])
        results = apply_grounding_pass(
            [("a", "This model has 12 tables and is used by finance.")], client, model_digest="digest",
        )
        self.assertEqual(results["a"], f"This model has 9 tables and is {UNVERIFIABLE_TEXT}.")


class ApplyResultsIntegrationTest(unittest.TestCase):
    def test_setters_receive_grounding_corrections(self):
        sink = {}
        triples = [("a", "This model has 12 tables.", lambda v: sink.__setitem__("a", v))]
        client = FakeGroundingClient([
            {"location": "a", "quote": "12 tables", "verdict": "contradicted", "correction": "9 tables"},
        ])
        results = apply_grounding_pass(
            [(loc, text) for loc, text, _ in triples], client, model_digest="digest",
        )
        apply_results(triples, results)
        self.assertEqual(sink["a"], "This model has 9 tables.")


class GroundingGeneratorWiringTest(unittest.TestCase):
    """Phase 3 end-to-end: a contradicted factual claim seeded into the
    technical document's core purpose must be corrected in the final
    Document object, after the critic pass has already run."""

    class _ContradictingClient:
        """Routes like test_agents.FakeLLMClient, but the grounding branch
        reports the Business Analyst's core_purpose claim as contradicted,
        so the generator's post-critic grounding pass has something real to
        fix."""

        def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
            if "fact-checker" in system:
                import json
                payload = json.loads(user)
                if "executive_summary.core_purpose" in payload.get("fields", {}):
                    return {"claims": [
                        {"location": "executive_summary.core_purpose", "quote": "12 tables",
                         "verdict": "contradicted", "correction": "a handful of tables"},
                    ]}
                return {"claims": []}
            if "Report Intelligence" in system:
                return {
                    "business_domain": "FAKE_DOMAIN",
                    "report_purpose": {"statement": "FAKE_REPORT_PURPOSE", "confidence": "High"},
                    "audience_hypotheses": [], "entity_definitions": [], "page_workflows": [],
                    "kpi_relationships": [], "cross_cutting_observations": [], "data_quality_notes": [],
                }
            if "Business Analyst" in system or "BI consultant" in system:
                return {
                    "core_purpose": "This report covers 12 tables of sales data.",
                    "pages": [], "navigation_guide": [], "complex_visual_explainers": [],
                }
            if "senior DAX developer" in system or "DAX measures" in system:
                import json
                payload = json.loads(user)
                return {"translations": [
                    {"name": m["name"], "plain_english": "A measure.",
                     "calculation_logic": "calc", "caveats": "", "category": "Other",
                     "confidence": "High"}
                    for m in payload["measures"]
                ]}
            if "data-modeling" in system:
                return {"summary": "A model.", "risks": []}
            if "description for every column" in system or "Column Describer" in system:
                import json
                payload = json.loads(user)
                return {"columns": [
                    {"table": c["table"], "column": c["column"], "description": "d"}
                    for c in payload["columns"]
                ]}
            if "expert technical editor" in system:
                return {"violations": []}
            raise AssertionError(f"unexpected system prompt: {system[:60]}")

    def test_contradicted_claim_corrected_after_critic(self):
        doc = TechnicalDocumentationGenerator.generate(_model(), self._ContradictingClient())
        self.assertIn("a handful of tables", doc.executive_summary.core_purpose)
        self.assertNotIn("12 tables", doc.executive_summary.core_purpose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
