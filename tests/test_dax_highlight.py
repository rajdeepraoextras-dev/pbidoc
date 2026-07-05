"""Tests for ``pbicompass.render._dax_highlight`` — DAX syntax highlighting
for measure-catalog code blocks (2.3)."""

from __future__ import annotations

import unittest

from pbicompass.render._dax_highlight import highlight_dax


class HighlightDaxTest(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(highlight_dax(""), "")
        self.assertEqual(highlight_dax(None), "")

    def test_keywords_are_wrapped(self):
        html = highlight_dax("CALCULATE ( SUM ( Sales[Amount] ) )")
        self.assertIn('<span class="tok-keyword">CALCULATE</span>', html)
        self.assertIn('<span class="tok-keyword">SUM</span>', html)
        self.assertIn('<span class="tok-ref">Sales[Amount]</span>', html)

    def test_string_and_number_literals(self):
        html = highlight_dax('IF ( Sales[Year] = 2020, "Current", "Past" )')
        self.assertIn('<span class="tok-number">2020</span>', html)
        self.assertIn('<span class="tok-string">&quot;Current&quot;</span>', html)

    def test_bare_measure_reference(self):
        html = highlight_dax("DIVIDE ( [Revenue], [Orders] )")
        self.assertIn('<span class="tok-ref">[Revenue]</span>', html)
        self.assertIn('<span class="tok-ref">[Orders]</span>', html)

    def test_lowercase_word_before_paren_is_not_a_keyword(self):
        # only ALL-CAPS-style DAX function names get the keyword treatment
        html = highlight_dax("total(Sales[Amount])")
        self.assertNotIn("tok-keyword", html)

    def test_xss_payload_is_fully_escaped_never_wrapped_raw(self):
        html = highlight_dax('<script>alert(1)</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_html_in_string_literal_is_escaped(self):
        html = highlight_dax('IF(TRUE(), "<img onerror=1>", "safe")')
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
