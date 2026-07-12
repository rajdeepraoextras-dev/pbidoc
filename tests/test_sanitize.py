"""Tests for ``agents/sanitize.py`` (AI-Native roadmap D2/D3/D6): the
deterministic meta-commentary, orphan-fragment, and punt-phrase guards."""

from __future__ import annotations

import unittest

from pathlib import Path

from pbicompass.agents.sanitize import (
    enforce_score_consistency,
    is_low_content_fragment,
    is_meta_commentary,
    is_punt_phrase,
    sanitize,
    sanitize_narratives,
    strip_meta_commentary_leak,
    strip_punt_leak,
)

CS_FIXTURE = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"


class IsMetaCommentaryTest(unittest.TestCase):
    def test_verify_directive_is_flagged(self):
        self.assertTrue(is_meta_commentary(
            "Verify existence of 'Plan, LE1, LE2, and LE3' in the model."
        ))

    def test_consider_directive_is_flagged(self):
        self.assertTrue(is_meta_commentary(
            "Consider providing a more specific description of how 'select' is used."
        ))

    def test_remove_directive_referencing_array_index_is_flagged(self):
        self.assertTrue(is_meta_commentary(
            "Remove the duplicated entry as it is identical to glossary[15].plain_definition."
        ))

    def test_ensure_and_provide_and_add_a_are_flagged(self):
        self.assertTrue(is_meta_commentary("Ensure the field name matches the source column."))
        self.assertTrue(is_meta_commentary("Provide a more specific description here."))
        self.assertTrue(is_meta_commentary("Add a caveat about refresh timing."))

    def test_explain_directive_is_flagged(self):
        # P2: observed production leak — a glossary definition for a field
        # parameter ("select") replaced with an editing instruction instead
        # of a definition: "Explain how or why the field selector changes
        # the chart..." — previously missed because "Explain" wasn't in the
        # banned-directive list.
        self.assertTrue(is_meta_commentary(
            "Explain how or why the field selector changes the chart, such as specifying a metric."
        ))

    def test_normal_prose_is_not_flagged(self):
        self.assertFalse(is_meta_commentary("Total invoiced revenue for the period in view."))
        self.assertFalse(is_meta_commentary("A field selector that switches what the chart displays."))

    def test_empty_text_is_not_flagged(self):
        self.assertFalse(is_meta_commentary(""))
        self.assertFalse(is_meta_commentary(None))

    def test_leaked_column_describer_guardrail_fragment_is_flagged(self):
        # The exact trailing clause of io.py's column-describer system
        # prompt (see io.py:354): "Only write exactly \"Unknown — requires
        # business confirmation.\" when no such structural fact is
        # available either." — reproduced verbatim, as it would appear if
        # the model echoed its own instructions into a description field.
        self.assertTrue(is_meta_commentary(
            'Only write exactly "Unknown — requires business confirmation." '
            'when no such structural fact is available either.'
        ))

    def test_orphaned_guardrail_clause_is_flagged(self):
        # The same guardrail, stranded as a dependent clause by
        # sentence-granular grounding replacement (D3) — no longer attached
        # to the sentence it was cut out of.
        self.assertTrue(is_meta_commentary("when no such structural fact is available either."))
        self.assertTrue(is_meta_commentary("when no structural fact is available either."))


class IsLowContentFragmentTest(unittest.TestCase):
    def test_short_lowercase_clause_is_flagged(self):
        self.assertTrue(is_low_content_fragment("when no such fact is available either."))

    def test_short_uppercase_sentence_is_not_flagged(self):
        # The deterministic fallback wording (D6) — short, but a real,
        # grammatically complete sentence, not stranded clause debris.
        self.assertFalse(is_low_content_fragment("No description set."))

    def test_normal_length_lowercase_fragment_is_not_flagged(self):
        self.assertFalse(is_low_content_fragment(
            "when the underlying source system records a corrected amount retroactively."
        ))

    def test_empty_text_is_not_flagged(self):
        self.assertFalse(is_low_content_fragment(""))
        self.assertFalse(is_low_content_fragment(None))


class IsPuntPhraseTest(unittest.TestCase):
    def test_column_punt_phrase_is_flagged(self):
        self.assertTrue(is_punt_phrase("Unknown — requires business confirmation."))

    def test_measure_punt_phrase_is_flagged(self):
        self.assertTrue(is_punt_phrase(
            "Business meaning could not be inferred automatically; requires business confirmation."
        ))

    def test_empty_or_none_counts_as_a_punt(self):
        self.assertTrue(is_punt_phrase(""))
        self.assertTrue(is_punt_phrase(None))

    def test_real_description_is_not_a_punt(self):
        self.assertFalse(is_punt_phrase("Key identifier; used to join Orders to related tables."))


class SanitizeTest(unittest.TestCase):
    def test_meta_commentary_falls_back(self):
        self.assertEqual(
            sanitize("Consider providing a more specific description.", "fallback text"),
            "fallback text",
        )

    def test_clean_text_passes_through(self):
        self.assertEqual(sanitize("A clean sentence.", "fallback text"), "A clean sentence.")

    def test_empty_text_falls_back(self):
        self.assertEqual(sanitize("", "fallback text"), "fallback text")
        self.assertEqual(sanitize(None, "fallback text"), "fallback text")


class StripPuntLeakTest(unittest.TestCase):
    def test_removes_the_whole_sentence_not_just_the_phrase(self):
        # A substring removal would strand "Address the ." — the fix drops
        # the whole sentence instead.
        text = ("Address the Unknown — requires business confirmation. Its resolution will "
                "both eliminate unused calculated columns and Unknown — requires business "
                "confirmation. Unknown — requires business confirmation.")
        self.assertEqual(strip_punt_leak(text, "FALLBACK"), "FALLBACK")

    def test_clean_sentences_around_the_leak_survive(self):
        text = ("The overall health score is 79, classified as 'Good'. The governance and "
                "unused assets components are the primary areas limiting a higher score, "
                "Unknown — requires business confirmation. Immediate attention should be "
                "directed towards those.")
        result = strip_punt_leak(text, "FALLBACK")
        self.assertNotIn("requires business confirmation", result)
        self.assertIn("The overall health score is 79", result)
        self.assertIn("Immediate attention should be directed towards those.", result)

    def test_tolerates_hyphen_and_extra_whitespace(self):
        text = "Root cause: Unknown  -  requires  business confirmation"
        self.assertEqual(strip_punt_leak(text, "FALLBACK"), "FALLBACK")

    def test_falls_back_when_nothing_content_bearing_survives(self):
        self.assertEqual(
            strip_punt_leak("The Unknown — requires business confirmation. Unknown — requires business confirmation.",
                             "FALLBACK"),
            "FALLBACK",
        )

    def test_clean_text_is_untouched(self):
        clean = "Auto Date/Time creates hidden tables that inflate the unused-asset count."
        self.assertEqual(strip_punt_leak(clean, "FALLBACK"), clean)

    def test_empty_text_falls_back(self):
        self.assertEqual(strip_punt_leak("", "FALLBACK"), "FALLBACK")
        self.assertEqual(strip_punt_leak(None, "FALLBACK"), "FALLBACK")

    def test_never_appears_in_a_realistic_narrative_field(self):
        # P0's own acceptance check: the phrase must never survive in a
        # narrative field after this pass, regardless of how many times
        # (or where) it was leaked into the text.
        text = ("The Unknown — requires business confirmation. Unknown — requires business "
                "confirmation. Address the Unknown — requires business confirmation.")
        result = strip_punt_leak(text, "This model scores 79/100 overall (Good).")
        self.assertNotIn("requires business confirmation", result.lower())


class StripMetaCommentaryLeakTest(unittest.TestCase):
    """D2: the same sentence-preserving removal StripPuntLeakTest exercises
    for the punt phrase, for the broader meta-commentary class — closes the
    gap where a field's *initial* AI draft (never routed through
    ``critic.apply_results``) could ship a self-instruction leak raw."""

    # The exact production shape (executive summary core_purpose, 2026-07):
    # a self-verification instruction ("Check the model to ensure that all
    # the described functionalities ... are supported by actual measures,
    # tables, or data sources.") echoed verbatim and spliced mid-sentence,
    # with a stray period immediately followed by a comma — the ".," splice
    # artifact this codebase already treats as a known leak shape.
    _REAL_LEAK = (
        "The report focuses on corporate financial management by analyzing IT and departmental "
        "spending against planned and estimated budgets. It tracks key KPIs such as Actual, Plan, "
        "and variance measures like Var Plan %, providing insights into spending trends and budget "
        "utilization. The primary users include Finance Managers, the Procurement Team, CFOs, and "
        "Check the model to ensure that all the described functionalities, such as monitoring "
        "corporate spending and identifying cost-saving opportunities, are supported by actual "
        "measures, tables, or data sources., analyzing vendor performance, and making strategic "
        "purchasing decisions across business units."
    )

    def test_removes_the_leaked_sentence_and_keeps_the_clean_ones(self):
        result = strip_meta_commentary_leak(self._REAL_LEAK, "FALLBACK")
        self.assertFalse(is_meta_commentary(result))
        self.assertIn("The report focuses on corporate financial management", result)
        self.assertIn("It tracks key KPIs such as Actual, Plan", result)
        self.assertNotIn("Check the model to ensure", result)
        self.assertNotIn("actual measures, tables, or data sources", result)

    def test_no_orphan_fragment_left_behind_by_the_dot_comma_splice(self):
        # The regression this guards: a naive sentence split that silently
        # drops the span between a ".," splice and its next real terminator
        # used to leave a comma-leading orphan fragment glued onto the
        # prior sentence ("...budget utilization. , analyzing vendor
        # performance...") even after the leak phrase itself was removed.
        result = strip_meta_commentary_leak(self._REAL_LEAK, "FALLBACK")
        self.assertNotIn(" , analyzing vendor performance", result)
        self.assertNotIn(", analyzing vendor performance", result)

    def test_clean_text_is_untouched(self):
        clean = "Auto Date/Time creates hidden tables that inflate the unused-asset count."
        self.assertEqual(strip_meta_commentary_leak(clean, "FALLBACK"), clean)

    def test_falls_back_when_nothing_content_bearing_survives(self):
        self.assertEqual(
            strip_meta_commentary_leak(
                "Remove the duplicated entry as it is identical to glossary[15].plain_definition.", "FALLBACK"
            ),
            "FALLBACK",
        )

    def test_does_not_strip_a_legitimate_recommendation_sentence(self):
        # The false-positive this must never reintroduce: real,
        # deterministic audit-recommendation prose legitimately opens with
        # an imperative verb ("Remove unused assets, or confirm they are
        # needed...") — only _META_REFERENCE's high-specificity fragments
        # (near-verbatim LLM/system-prompt wording, never legitimate
        # recommendation phrasing) should ever strip a sentence here, not
        # the bare imperative-verb start _STARTS_WITH_DIRECTIVE also flags.
        text = ("The model scores 79/100 overall. Remove unused assets, or confirm they are "
                "needed for future use. Governance is otherwise clean.")
        self.assertEqual(strip_meta_commentary_leak(text, "FALLBACK"), text)

    def test_empty_text_falls_back(self):
        self.assertEqual(strip_meta_commentary_leak("", "FALLBACK"), "FALLBACK")
        self.assertEqual(strip_meta_commentary_leak(None, "FALLBACK"), "FALLBACK")


class SplitSentencesGapHandlingTest(unittest.TestCase):
    """A terminator immediately followed by a non-whitespace character
    (".," or ".word") can't satisfy _SENTENCE_RE's trailing boundary, so
    the scanner skips forward to its next successful match — this must
    glue the skipped span to what follows, never drop or misalign it."""

    def test_no_text_is_lost_across_a_dot_comma_splice(self):
        from pbicompass.agents.sanitize import _split_sentences

        text = "First sentence. Middle clause., trailing clause. Last sentence."
        self.assertEqual("".join(_split_sentences(text)), text)

    def test_reconstitutes_a_realistic_leak_splice_losslessly(self):
        from pbicompass.agents.sanitize import _split_sentences

        text = StripMetaCommentaryLeakTest._REAL_LEAK
        self.assertEqual("".join(_split_sentences(text)), text)


class EnforceScoreConsistencyTest(unittest.TestCase):
    def test_wrong_score_sentence_is_replaced(self):
        text = "The overall health score of this model is 78, categorized as 'Good'. More text follows."
        result = enforce_score_consistency(text, 79, "Good")
        self.assertIn("The overall health score is 79, classified as 'Good'.", result)
        self.assertNotIn("78", result)
        self.assertIn("More text follows.", result)

    def test_correct_score_sentence_is_left_untouched(self):
        text = "The overall health score of this model is 79, categorized as 'Good'."
        self.assertEqual(enforce_score_consistency(text, 79, "Good"), text)

    def test_text_with_no_score_mention_is_untouched(self):
        text = "This report tracks monthly spending trends across departments."
        self.assertEqual(enforce_score_consistency(text, 79, "Good"), text)

    def test_does_not_false_positive_on_unrelated_numbers(self):
        text = "38 findings were identified across 12 tables."
        self.assertEqual(enforce_score_consistency(text, 79, "Good"), text)

    def test_empty_text_is_untouched(self):
        self.assertEqual(enforce_score_consistency("", 79, "Good"), "")
        self.assertIsNone(enforce_score_consistency(None, 79, "Good"))


class SanitizeNarrativesTest(unittest.TestCase):
    """P0 (centralized fix): the one gate every generator's narrative
    triples must pass through — a leak that survived a per-generator
    opt-in (audit.py alone) is what motivated pulling this out into a
    single, mandatory, reusable pass."""

    def _obj(self, text):
        class _Box:
            value = text
        return _Box()

    def _triple(self, location, box):
        def setter(v):
            box.value = v
        return (location, box.value, setter)

    def test_strips_leak_across_multiple_fields(self):
        a = self._obj("Clean field, no leak here.")
        b = self._obj("Some prose. Unknown — requires business confirmation. More prose follows.")
        triples = [self._triple("a", a), self._triple("b", b)]
        sanitize_narratives(triples)
        self.assertEqual(a.value, "Clean field, no leak here.")
        self.assertNotIn("requires business confirmation", b.value)
        self.assertIn("Some prose.", b.value)
        self.assertIn("More prose follows.", b.value)

    def test_uses_supplied_fallback_when_field_would_go_empty(self):
        box = self._obj("Unknown — requires business confirmation.")
        triples = [self._triple("loc", box)]
        sanitize_narratives(triples, {"loc": "DETERMINISTIC FALLBACK"})
        self.assertEqual(box.value, "DETERMINISTIC FALLBACK")

    def test_without_a_fallback_keeps_original_rather_than_blanking(self):
        # Defensive default: no crash, no empty string — the field is left
        # exactly as it was (still bad, but never worse) when the caller
        # supplied no domain-specific replacement.
        box = self._obj("Unknown — requires business confirmation.")
        triples = [self._triple("loc", box)]
        sanitize_narratives(triples)
        self.assertEqual(box.value, "Unknown — requires business confirmation.")

    def test_fields_without_the_leak_are_never_touched(self):
        box = self._obj("A perfectly normal sentence.")
        called = []
        triples = [("loc", box.value, lambda v: called.append(v))]
        sanitize_narratives(triples)
        self.assertEqual(called, [])

    def test_empty_triples_list_is_a_noop(self):
        sanitize_narratives([])  # must not raise

    def test_strips_meta_commentary_leak_not_just_the_punt_phrase(self):
        # D2 extension: sanitize_narratives previously only checked the
        # punt phrase — a field's *initial* AI draft carrying a D2-class
        # leak (never routed through critic.apply_results, the only place
        # is_meta_commentary was previously checked) shipped untouched.
        box = self._obj(StripMetaCommentaryLeakTest._REAL_LEAK)
        triples = [self._triple("executive_summary.core_purpose", box)]
        sanitize_narratives(triples)
        self.assertNotIn("Check the model to ensure", box.value)
        self.assertIn("The report focuses on corporate financial management", box.value)

    def test_both_leak_classes_in_the_same_field_are_both_stripped(self):
        box = self._obj(
            "Real content here. Unknown — requires business confirmation. "
            "Remove the duplicated entry as it is identical to glossary[15].plain_definition. "
            "More real content."
        )
        triples = [self._triple("loc", box)]
        sanitize_narratives(triples)
        self.assertNotIn("requires business confirmation", box.value)
        self.assertNotIn("glossary[15]", box.value)
        self.assertIn("Real content here.", box.value)
        self.assertIn("More real content.", box.value)

    def test_does_not_strip_a_legitimate_recommendation_sentence(self):
        # The false-positive this must never reintroduce: a real, LLM-free
        # audit-recommendation sentence legitimately opens with an
        # imperative verb — production shape from audit.py's own
        # deterministic root-cause prose.
        box = self._obj(
            "The model scores 79/100 overall. Remove unused assets, or confirm they are "
            "needed for future use. Governance is otherwise clean."
        )
        triples = [self._triple("loc", box)]
        sanitize_narratives(triples)
        self.assertIn("Remove unused assets, or confirm they are needed for future use.", box.value)


class CorporateSpendSanitizeWiringTest(unittest.TestCase):
    """Day 7: the P0 gate exercised against a real generated audit doc's
    own fallback text (the real Corporate Spend narrative_overview), not a
    hand-written 'FALLBACK' string — proves the mechanism the generators
    actually wire in (a real deterministic sentence, not a placeholder)
    produces clean prose when a leak is injected."""

    def test_leak_injected_into_the_real_narrative_is_cleaned_with_the_real_fallback(self):
        from pbicompass.schemas.model import SemanticModel
        from pbicompass.agents.generators import AuditReportGenerator
        from pbicompass.agents.generators.audit import _narrative_triples

        model = SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))
        doc = AuditReportGenerator.generate(model)
        clean_fallback = doc.narrative_overview
        self.assertNotIn("requires business confirmation", clean_fallback)

        corrupted = clean_fallback + " Unknown — requires business confirmation."
        box_value = {"v": corrupted}

        def setter(v):
            box_value["v"] = v

        sanitize_narratives([("narrative_overview", corrupted, setter)], {"narrative_overview": clean_fallback})
        self.assertNotIn("requires business confirmation", box_value["v"])
        self.assertIn("scores", box_value["v"].lower())

    def test_core_purpose_leak_is_cleaned_through_the_technical_docs_own_triples(self):
        # Reproduces the real production defect (2026-07): the executive
        # summary's core_purpose is an LLM-generated field whose *initial*
        # draft was assigned directly (technical.py's _executive_summary,
        # ``core_purpose, core_purpose_set = data["core_purpose"], True``)
        # with no sanitize() call at that point — only later critic
        # *replacements* went through is_meta_commentary. Proves the fix is
        # reachable through generate_document's real, final
        # _narrative_triples()/sanitize_narratives() call, not just the
        # sanitize.py helpers in isolation.
        from pbicompass.agents import generate_document
        from pbicompass.agents.generators.technical import _narrative_triples
        from pbicompass.schemas.model import SemanticModel

        model = SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))
        doc = generate_document(model)
        doc.executive_summary.core_purpose = StripMetaCommentaryLeakTest._REAL_LEAK

        sanitize_narratives(_narrative_triples(doc))

        self.assertNotIn("Check the model to ensure", doc.executive_summary.core_purpose)
        self.assertNotIn("actual measures, tables, or data sources", doc.executive_summary.core_purpose)
        self.assertIn("The report focuses on corporate financial management",
                       doc.executive_summary.core_purpose)


if __name__ == "__main__":
    unittest.main()
