"""Tests for the critic agent pass (Phase 5.3): agents/critic.py's
deterministic pre-pass and LLM-routed style pass, plus end-to-end wiring
into the technical-document generator across all three rendered formats."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from pbicompass.agents.critic import apply_critic_pass, apply_results
from pbicompass.agents.generators import TechnicalDocumentationGenerator
from pbicompass.parsers import detect_and_parse
from pbicompass.render import render_docx, render_html, render_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _model():
    return detect_and_parse(FIXTURE)


class DeterministicPrePassTest(unittest.TestCase):
    def test_banned_word_is_stripped_offline(self):
        results = apply_critic_pass(
            [("loc", "This is a revolutionary new way to see your sales.")], client=None,
        )
        self.assertIn("loc", results)
        self.assertNotIn("revolutionary", results["loc"].lower())

    def test_duplicate_adjacent_sentences_collapse(self):
        results = apply_critic_pass(
            [("loc", "Total Sales calculates the total sales. Total Sales calculates the total sales.")],
            client=None,
        )
        self.assertIn("loc", results)
        self.assertEqual(results["loc"].count("Total Sales calculates the total sales."), 1)

    def test_clean_text_is_left_alone(self):
        results = apply_critic_pass([("loc", "A perfectly ordinary sentence.")], client=None)
        self.assertNotIn("loc", results)

    def test_unknown_bracketed_name_warns_but_does_not_alter_text(self):
        warnings = []
        results = apply_critic_pass(
            [("loc", "See [NotARealMeasure] for details.")], client=None,
            known_names={"Total Revenue"}, warn=warnings.append,
        )
        self.assertNotIn("loc", results)  # nothing to fix, just flag it
        self.assertTrue(any("NotARealMeasure" in w for w in warnings))

    def test_code_fence_is_never_touched(self):
        snippet = "Apply this fix:\n```\nModel.Tables[\"Sales\"].Measures[\"X\"].Delete();\n```"
        results = apply_critic_pass([("loc", snippet)], client=None)
        self.assertNotIn("loc", results)

    def test_offline_skips_llm_call_entirely(self):
        # apply_critic_pass itself only skips the LLM half when client is
        # None; generators gate the *whole* call on client is not None (see
        # CriticGeneratorWiringTest below) so offline runs are unaffected.
        results = apply_critic_pass([("loc", "clean text")], client=None)
        self.assertEqual(results, {})


class FakeCriticClient:
    """A minimal LLMClient that flags one exact quote as a violation."""

    def __init__(self, quote: str, fix: str, location: str):
        self.quote, self.fix, self.location = quote, fix, location
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls += 1
        return {"violations": [
            {"location": self.location, "quote": self.quote, "rule": "name-echo",
             "suggested_fix": self.fix},
        ]}


class LlmPassTest(unittest.TestCase):
    def test_llm_violation_is_applied_to_the_right_location_only(self):
        client = FakeCriticClient(
            quote="Total Sales calculates the total sales.",
            fix="The sum of all completed sales.",
            location="a",
        )
        results = apply_critic_pass(
            [("a", "Total Sales calculates the total sales."), ("b", "Unrelated text.")],
            client,
        )
        self.assertEqual(results.get("a"), "The sum of all completed sales.")
        self.assertNotIn("b", results)

    def test_violation_for_unknown_location_is_ignored(self):
        client = FakeCriticClient(quote="x", fix="y", location="nonexistent")
        results = apply_critic_pass([("a", "some text")], client)
        self.assertEqual(results, {})


class ApplyResultsTest(unittest.TestCase):
    def test_setters_are_called_only_for_changed_locations(self):
        sink = {}
        triples = [
            ("a", "orig-a", lambda v: sink.__setitem__("a", v)),
            ("b", "orig-b", lambda v: sink.__setitem__("b", v)),
        ]
        apply_results(triples, {"a": "new-a"})
        self.assertEqual(sink, {"a": "new-a"})


class CriticGeneratorWiringTest(unittest.TestCase):
    """5.3 end-to-end: a banned word seeded into the technical document's
    narrative must be stripped from the final Document object — and so must
    show up fixed in every one of HTML/Markdown/DOCX, not just one of them
    (the exact regression class flagged for this codebase)."""

    class _BannedWordClient:
        """Routes like test_agents.FakeLLMClient, but seeds a banned word
        into the Business Analyst's core_purpose so the critic has
        something real to strip."""

        def complete_json(self, system: str, user: str, schema: dict) -> dict:
            if "Business Analyst" in system or "BI consultant" in system:
                return {
                    "core_purpose": "This revolutionary report shows your sales.",
                    "pages": [],
                    "navigation_guide": [],
                    "complex_visual_explainers": [],
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

    def test_banned_word_stripped_in_all_three_formats(self):
        doc = TechnicalDocumentationGenerator.generate(_model(), self._BannedWordClient())
        self.assertNotIn("revolutionary", doc.executive_summary.core_purpose.lower())

        html = render_html(doc)
        md = render_markdown(doc)
        self.assertNotIn("revolutionary", html.lower())
        self.assertNotIn("revolutionary", md.lower())

        with tempfile.TemporaryDirectory() as td:
            out = render_docx(doc, Path(td) / "out.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertNotIn("revolutionary", document.lower())


if __name__ == "__main__":
    unittest.main()
