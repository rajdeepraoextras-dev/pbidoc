"""Tests for ``agents/sanitize.py`` (AI-Native roadmap D2/D3/D6): the
deterministic meta-commentary, orphan-fragment, and punt-phrase guards."""

from __future__ import annotations

import unittest

from pbicompass.agents.sanitize import (
    enforce_score_consistency,
    is_low_content_fragment,
    is_meta_commentary,
    is_punt_phrase,
    sanitize,
    strip_punt_leak,
)


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


if __name__ == "__main__":
    unittest.main()
