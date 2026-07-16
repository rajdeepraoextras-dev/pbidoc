"""Consolidated, end-to-end output-quality guards.

Days 1-4 fixed D1/D2/D3/D4/D6 and each added unit/wiring-level regression
tests near the fix itself (test_sanitize.py, test_critic.py, test_grounding.py,
test_report_facts.py, test_generators.py, test_agents.py::AntiPuntGuardTest).
This module is the holistic complement the roadmap's Sec 10.1 asks for: it
renders the full, real SampleSales document set the way a customer would
receive it (all 4 document types, md + html) and asserts the defect patterns
are absent from the *actual rendered output*, not just from a hand-built
fixture exercising one code path. It is the permanent CI lock-in for the
Sprint 1 fixes.
"""

from __future__ import annotations

import re
import unittest
from collections import Counter
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import (
    AuditReportGenerator,
    BusinessGuideGenerator,
    ExecutiveSummaryGenerator,
)
from pbicompass.parsers import detect_and_parse
from pbicompass.render import (
    render_audit_html,
    render_audit_markdown,
    render_executive_html,
    render_executive_markdown,
    render_html,
    render_markdown,
    render_user_guide_html,
    render_user_guide_markdown,
)
from pbicompass.schemas.model import SemanticModel

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"
CS_FIXTURE = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"

# D2 - unambiguous artifacts of LLM meta-commentary/editing-directives that
# leaked into shipped prose. These substrings have no legitimate occurrence
# anywhere in a rendered doc (unlike bare "Remove"/"Add a", which are also
# the deterministic audit engine's own imperative fix-recommendation prose).
_D2_ARTIFACT_PATTERNS = [
    re.compile(r"glossary\[\d+\]"),
    re.compile(r"plain_definition"),
    re.compile(r"the duplicated entry"),
]

# D1 - audit-speak / internal-completeness-nag vocabulary banned from the
# executive doc specifically (roadmap's own done-when list).
_D1_BANNED_PHRASES = ["governance finding", "best practice", "% complete", "fields still need"]

# D4 - a bare field-parameter token. Lowercase, whole-word: the codebase's
# own legitimate English usage always capitalizes "Select" (e.g. "Select
# 'View as'" in the RLS test checklist), so a case-sensitive lowercase match
# does not false-positive on normal prose. The negative lookbehind excludes
# the CSS property "user-select" (Day 6's pan/zoom styling) — \b treats the
# hyphen as a non-word boundary, so "user-select" would otherwise match
# "select" as a bare "word" even though it's not a leaked field-parameter
# token at all.
_D4_FIELD_SELECTOR_RE = re.compile(r"(?<!-)\bselect1?\b")

_PUNT_PHRASE = "requires business confirmation"

_COLUMN_ROW_RE = re.compile(
    r'<tr id="column-[^"]+"><td>([^<]*)</td><td>([^<]*)</td><td>[^<]*</td><td>([^<]*)</td>'
)


def _relationship_columns(model) -> set[tuple[str, str]]:
    """(table, column) pairs that participate in at least one relationship -
    the D6 fix's whole point is that these must never render the punt phrase."""
    pairs: set[tuple[str, str]] = set()
    for rel in model.relationships:
        pairs.add((rel.from_table, rel.from_column))
        pairs.add((rel.to_table, rel.to_column))
    return pairs


class OutputQualityGuardsTest(unittest.TestCase):
    """One parse, one generate-per-type, every rendered surface scanned."""

    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)
        cls.technical_doc = generate_document(cls.model)
        cls.audit_doc = AuditReportGenerator.generate(cls.model)
        cls.executive_doc = ExecutiveSummaryGenerator.generate(cls.model)
        cls.user_guide_doc = BusinessGuideGenerator.generate(cls.model)

        cls.rendered = {
            "technical.html": render_html(cls.technical_doc),
            "technical.md": render_markdown(cls.technical_doc),
            "audit.html": render_audit_html(cls.audit_doc),
            "audit.md": render_audit_markdown(cls.audit_doc),
            "executive.html": render_executive_html(cls.executive_doc),
            "executive.md": render_executive_markdown(cls.executive_doc),
            "user_guide.html": render_user_guide_html(cls.user_guide_doc),
            "user_guide.md": render_user_guide_markdown(cls.user_guide_doc),
        }

    # ---- D1: exec doc reads for a business owner, not an auditor --------

    def test_d1_no_audit_speak_in_executive_doc(self):
        for name in ("executive.html", "executive.md"):
            text = self.rendered[name]
            for phrase in _D1_BANNED_PHRASES:
                self.assertNotIn(
                    phrase.lower(), text.lower(),
                    f"D1 regression: {phrase!r} found in {name}",
                )

    # ---- D2: no LLM meta-commentary/editing-directives in any doc -------

    def test_d2_no_meta_commentary_artifacts_in_any_doc(self):
        for name, text in self.rendered.items():
            for pattern in _D2_ARTIFACT_PATTERNS:
                match = pattern.search(text)
                self.assertIsNone(
                    match, f"D2 regression: {pattern.pattern!r} found in {name} ({match})"
                )

    # ---- D3: grounding never splices mid-sentence -------------------------

    def test_d3_no_mid_sentence_splice_artifacts_in_any_doc(self):
        for name, text in self.rendered.items():
            self.assertNotIn(".,", text, f"D3 regression: '.,' splice found in {name}")
            self.assertNotIn(
                f"{_PUNT_PHRASE}..", text,
                f"D3 regression: doubled terminal punctuation found in {name}",
            )

    # ---- D4: no bare select/select1 field-selector leaks -------------------

    def test_d4_no_bare_field_selector_tokens_in_any_doc(self):
        for name, text in self.rendered.items():
            match = _D4_FIELD_SELECTOR_RE.search(text)
            self.assertIsNone(match, f"D4 regression: bare field-selector token in {name} ({match})")

    # ---- D6: punt phrase bounded, never on a relationship-participating col

    def test_d6_punt_phrase_bounded_and_never_on_relationship_column(self):
        rel_columns = _relationship_columns(self.model)

        for name in ("technical.html",):
            text = self.rendered[name]
            total = text.count(_PUNT_PHRASE)
            self.assertLessEqual(
                total, 2,
                f"D6 regression: '{_PUNT_PHRASE}' count ({total}) is no longer bounded in {name} "
                "- the anti-punt merge policy may have regressed.",
            )

            for table, column, description in _COLUMN_ROW_RE.findall(text):
                if _PUNT_PHRASE not in description:
                    continue
                self.assertNotIn(
                    (table, column), rel_columns,
                    f"D6 regression: relationship-participating column {table}.{column} "
                    f"rendered the punt phrase instead of its deterministic join-key description.",
                )

    # ---- Sanity: the guards above aren't vacuously passing on empty docs --

    def test_rendered_docs_are_non_trivial(self):
        for name, text in self.rendered.items():
            self.assertGreater(len(text), 500, f"{name} rendered suspiciously small ({len(text)} chars)")

    def test_optional_intake_is_neutral_not_incomplete_work(self):
        for name, text in self.rendered.items():
            self.assertNotIn("fields awaiting input", text.lower(), name)
            self.assertNotIn("to complete", text.lower(), name)
            self.assertNotIn("complete missing", text.lower(), name)
        self.assertIn("optional context supplied", self.rendered["technical.html"].lower())
        self.assertIn("not provided during generation", self.rendered["technical.html"].lower())

    def test_live_svg_controls_are_scoped_per_diagram(self):
        for name in ("technical.html", "user_guide.html"):
            self.assertIn("diagram-${diagramIndex + 1}-${control.id}", self.rendered[name])

    # ---- V2 fix must not over-exclude a real, disconnected measure-home
    # table: SampleSales' "Key Measures" (1 calculated column, no
    # relationships) is exactly the shape field_parameter_table_names'
    # broader "<=3 columns" heuristic also matches — the model diagram's
    # V2 exclusion must use the stricter name-only signal instead, or a
    # genuine table silently disappears from the diagram a reader relies
    # on to see the model's real shape.

    def test_key_measures_table_still_renders_in_model_diagram(self):
        text = self.rendered["technical.html"]
        m = re.search(r'aria-labelledby="model-diagram-title".*?</svg>', text, re.S)
        self.assertIsNotNone(m, "fixture must still produce a model diagram")
        self.assertIn("Key Measures", m.group(0))


# ============================================================================
# Day 7: the same holistic discipline, plus P0-P2/I1-I6 closure, against the
# real Corporate Spend fixture (tests/fixtures/CorporateSpend/model.json) —
# an 11-table galaxy schema (2 tables classified "fact"), Auto Date/Time
# hidden tables, a hardcoded Dropbox path, a 20-visual "Plan Variance
# Analysis" page, and bare select/select1 field-parameter tokens: exactly
# the shape the 2026-07-05/07 real output review (and the ed24be3
# P0/P1/P2 commit) was
# reviewing when it found these defects — this fixture proves the fixes
# hold on the real data that motivated them, not just on hand-built
# snippets exercising one code path.
# ============================================================================

_SCORE_MENTION_RE = re.compile(r"health\s+score[^.\d]{0,40}?(\d{1,3})", re.IGNORECASE)
_DOUBLED_DOT_RE = re.compile(r"(?<!\.)\.\.(?!\.)")
_ID_RE = re.compile(r'id="([^"]+)"')
# Real anchor ids/hrefs are always plain slugs (word chars + hyphens) —
# restricting the capture to that charset (rather than a greedy [^"]+)
# skips JS template-literal fragments like href="#${sections[index].id}"
# inside <script> blocks, which aren't real links at all.
_HREF_ANCHOR_RE = re.compile(r'href="#([a-zA-Z0-9_-]+)"')


class CorporateSpendOutputQualityGuardsTest(unittest.TestCase):
    """D1-D6 (same holistic guards as ``OutputQualityGuardsTest``) plus
    P0 (punt-leak centralization), P1 (health-score self-contradiction),
    P2 (doubled star-schema/caveats phrasing), and I2/I3 (anchor
    collisions / dead wireframe links) — all against the real fixture."""

    @classmethod
    def setUpClass(cls):
        cls.model = SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))
        cls.technical_doc = generate_document(cls.model)
        cls.audit_doc = AuditReportGenerator.generate(cls.model)
        cls.executive_doc = ExecutiveSummaryGenerator.generate(cls.model)
        cls.user_guide_doc = BusinessGuideGenerator.generate(cls.model)

        cls.rendered = {
            "technical.html": render_html(cls.technical_doc),
            "technical.md": render_markdown(cls.technical_doc),
            "audit.html": render_audit_html(cls.audit_doc),
            "audit.md": render_audit_markdown(cls.audit_doc),
            "executive.html": render_executive_html(cls.executive_doc),
            "executive.md": render_executive_markdown(cls.executive_doc),
            "user_guide.html": render_user_guide_html(cls.user_guide_doc),
            "user_guide.md": render_user_guide_markdown(cls.user_guide_doc),
        }

    # ---- D1-D6, same holistic guards as SampleSales ----------------------

    def test_d1_no_audit_speak_in_executive_doc(self):
        for name in ("executive.html", "executive.md"):
            text = self.rendered[name]
            for phrase in _D1_BANNED_PHRASES:
                self.assertNotIn(phrase.lower(), text.lower(), f"D1 regression: {phrase!r} found in {name}")

    def test_d2_no_meta_commentary_artifacts_in_any_doc(self):
        for name, text in self.rendered.items():
            for pattern in _D2_ARTIFACT_PATTERNS:
                match = pattern.search(text)
                self.assertIsNone(match, f"D2 regression: {pattern.pattern!r} found in {name} ({match})")

    def test_d3_no_mid_sentence_splice_artifacts_in_any_doc(self):
        for name, text in self.rendered.items():
            self.assertNotIn(".,", text, f"D3 regression: '.,' splice found in {name}")
            self.assertNotIn(f"{_PUNT_PHRASE}..", text, f"D3 regression: doubled terminal punctuation found in {name}")

    def test_d4_no_bare_field_selector_tokens_in_any_doc(self):
        # Corporate Spend's real "Plan Variance Analysis" page is the exact
        # production shape D4 fixed: bare 'select'/'select1' tokens in a
        # visual's field list, with no backing table object in model.tables.
        self.assertIn("select", {f for v in next(
            p for p in self.model.pages if p.display_name == "Plan Variance Analysis"
        ).visuals for f in v.fields}, "fixture must still contain the real bare-token regression shape")
        for name, text in self.rendered.items():
            match = _D4_FIELD_SELECTOR_RE.search(text)
            self.assertIsNone(match, f"D4 regression: bare field-selector token in {name} ({match})")

    def test_d6_punt_phrase_bounded_and_never_on_relationship_column(self):
        rel_columns = _relationship_columns(self.model)
        for name in ("technical.html",):
            text = self.rendered[name]
            for table, column, description in _COLUMN_ROW_RE.findall(text):
                if _PUNT_PHRASE not in description:
                    continue
                self.assertNotIn(
                    (table, column), rel_columns,
                    f"D6 regression: relationship-participating column {table}.{column} "
                    f"rendered the punt phrase instead of its deterministic join-key description.",
                )

    # ---- P0: the centralized sanitize_narratives gate ---------------------

    def test_p0_no_punt_leak_in_any_narrative_field(self):
        from pbicompass.agents.generators.audit import _narrative_triples as audit_triples
        from pbicompass.agents.generators.executive import _narrative_triples as exec_triples
        from pbicompass.agents.generators.technical import _narrative_triples as tech_triples
        from pbicompass.agents.generators.user_guide import _narrative_triples as ug_triples

        all_triples = (
            audit_triples(self.audit_doc) + exec_triples(self.executive_doc)
            + tech_triples(self.technical_doc) + ug_triples(self.user_guide_doc)
        )
        self.assertTrue(all_triples, "fixture should produce at least one narrative field")
        for location, text, _setter in all_triples:
            self.assertNotIn(
                _PUNT_PHRASE, text or "",
                f"P0 regression: punt-phrase leak survived in narrative field {location!r}",
            )

    # ---- P1: every "health score" mention agrees with the computed score --

    def test_p1_every_health_score_mention_agrees_with_the_computed_score(self):
        # Scanned over the actual narrative *prose* fields only (matching
        # what enforce_score_consistency operates on) rather than the full
        # rendered HTML, which also contains a "1. Overall Health Score"
        # section heading and a JSON search-index title carrying the same
        # words for unrelated reasons (section numbering, not a score claim).
        # The offline/deterministic path phrases this as "scores 77/100",
        # never literally "health score ... 77" (that phrasing is an LLM
        # narrator's, per enforce_score_consistency's own docstring) — so
        # this is a defensive check, not expected to find a match here.
        actual = self.audit_doc.health.overall
        for text in (self.audit_doc.narrative_overview, self.audit_doc.strategic_narrative):
            for m in _SCORE_MENTION_RE.finditer(text or ""):
                self.assertEqual(
                    int(m.group(1)), actual,
                    f"P1 regression: narrative claims a health score of {m.group(1)} but the "
                    f"document's own computed score is {actual} (self-contradiction).",
                )

    def test_p1_llm_corrupted_score_narrative_is_corrected_against_the_real_computed_score(self):
        # The exact production shape (a "79-vs-78" self-contradiction, per
        # ed24be3's commit message) reproduced against this fixture's own
        # real, independently-computed score rather than a synthetic one.
        from pbicompass.agents.sanitize import enforce_score_consistency

        actual, band = self.audit_doc.health.overall, self.audit_doc.health.band
        wrong = actual + 1
        corrupted = f"The overall health score of this model is {wrong}, categorized as '{band}'. More text follows."
        fixed = enforce_score_consistency(corrupted, actual, band)
        self.assertNotIn(str(wrong), fixed)
        self.assertIn(f"The overall health score is {actual}", fixed)
        self.assertIn("More text follows.", fixed)

    # ---- P2: no doubled star-schema phrase / no doubled terminal period ---

    def test_p2_no_doubled_star_schema_phrase(self):
        for name, text in self.rendered.items():
            lowered = text.lower()
            self.assertNotIn("a star schema — a star schema", lowered, f"P2 regression: doubled star-schema phrase in {name}")
            self.assertNotIn("a star schema a star schema", lowered, f"P2 regression: doubled star-schema phrase in {name}")

    def test_p2_no_doubled_terminal_period_anywhere(self):
        for name, text in self.rendered.items():
            match = _DOUBLED_DOT_RE.search(text)
            self.assertIsNone(match, f"P2 regression: doubled terminal period in {name} (context: {text[max(0, match.start()-40):match.start()+5] if match else ''!r})")

    # ---- I2: no duplicate id= anywhere (Var LE1 / Var LE1 % is the real,
    # exact production collision shape this fixture carries) --------------

    def test_i2_no_duplicate_ids_in_any_rendered_doc(self):
        for name in ("technical.html", "audit.html", "executive.html", "user_guide.html"):
            text = self.rendered[name]
            ids = _ID_RE.findall(text)
            dupes = [i for i, count in Counter(ids).items() if count > 1]
            self.assertEqual(dupes, [], f"I2 regression: duplicate id= attributes in {name}: {dupes}")

    def test_i2_var_le_measures_get_distinct_ids(self):
        # The exact regression shape: 4 name pairs that collapse to the same
        # slug once '%' is stripped (var-le1, var-le2, var-le3, var-plan).
        text = self.rendered["technical.html"]
        for base in ("var-le1", "var-le2", "var-le3", "var-plan"):
            self.assertIn(f'id="measure-{base}"', text)
            self.assertIn(f'id="measure-{base}-2"', text)

    # ---- I3: every intra-document #anchor href resolves to a real id ------

    def test_i3_every_intra_document_href_resolves(self):
        for name in ("technical.html", "user_guide.html", "executive.html"):
            text = self.rendered[name]
            ids = set(_ID_RE.findall(text))
            hrefs = set(_HREF_ANCHOR_RE.findall(text))
            dead = {h for h in hrefs if h not in ids}
            self.assertEqual(dead, set(), f"I3 regression: dead intra-document anchor link(s) in {name}: {dead}")

    # ---- I4: executive "Report at a glance" thumbnails reuse the full-size
    # wireframe SVG verbatim, internal <a href="#visual-..."> / "#page-..."
    # links and all — those anchors only exist in the technical doc/user
    # guide, never in the executive doc itself. In the real multi-doc bundle
    # (sibling_hrefs set) each thumbnail is *also* wrapped in its own outer
    # deep-link <a>, so the unstripped SVG produced invalid nested <a> tags
    # on top of the dead links (I3/G6: this fixture alone put >39% of the
    # executive doc's anchors dead — the real regression this guards).

    def test_i4_executive_thumbnails_have_no_dead_or_nested_links_in_bundle(self):
        from pbicompass.render import render_executive_html as _render_exec_html

        sibling_hrefs = {"technical": "technical.html", "user_guide": "user_guide.html", "audit": "audit.html"}
        text = _render_exec_html(self.executive_doc, sibling_hrefs=sibling_hrefs)
        self.assertIn("thumb-card", text, "fixture should produce at least one page thumbnail")

        ids = set(_ID_RE.findall(text))
        hrefs = set(_HREF_ANCHOR_RE.findall(text))
        dead = {h for h in hrefs if h not in ids}
        self.assertEqual(dead, set(), f"I4 regression: dead intra-document anchor link(s) in executive.html: {dead}")

        self.assertIsNone(
            re.search(r'<a\b[^>]*>[^<]*<a\b', text),
            "I4 regression: nested <a> inside <a> in executive.html (invalid HTML)",
        )

    # ---- V2: model diagram never draws Auto Date/Time internals or
    # disconnected field-parameter tables as nodes — this fixture's own
    # 'Range' table and its two real Auto Date/Time hidden tables
    # (DateTableTemplate_.../LocalDateTable_...) were rendering as full
    # visible diagram nodes (and listed in the diagram's own accessible
    # <title>), misrepresenting the model's actual star/galaxy shape.

    def test_v2_auto_datetime_and_parameter_tables_never_drawn_in_model_diagram(self):
        text = self.rendered["technical.html"]
        m = re.search(r'aria-labelledby="model-diagram-title".*?</svg>', text, re.S)
        self.assertIsNotNone(m, "fixture must still produce a model diagram")
        svg = m.group(0)
        for excluded in ("DateTableTemplate", "LocalDateTable", ">Range<", "Range&#x27;", "'Range'"):
            self.assertNotIn(excluded, svg, f"V2 regression: {excluded!r} drawn in the model diagram")
        # Belt-and-braces: none of these tables' own anchors exist as
        # clickable data-table nodes either.
        self.assertNotIn('data-table="Range"', svg)

    # ---- Rules that must fire on this real fixture (proving they still do)

    def test_rules_that_must_fire_on_real_corporate_spend_data(self):
        perf_kinds = {r.kind for r in self.audit_doc.performance_risks}
        gov_areas = {g.area for g in self.audit_doc.governance}
        self.assertIn("auto_datetime", perf_kinds, "PBIC-PERF-007 must fire: fixture has real Auto Date/Time tables")
        self.assertIn("visual_density", perf_kinds, "PBIC-PERF-004 must fire: 'Plan Variance Analysis' has 20 visuals")
        self.assertIn("hardcoded_paths", gov_areas, "PBIC-GOV-010 must fire: fixture's M source is a literal C:\\Users\\... Dropbox path")
        star_check = next(c for c in self.audit_doc.best_practices if c.id == "star_schema")
        self.assertFalse(star_check.passed, "a genuine 2-fact galaxy schema is correctly not a star schema")


if __name__ == "__main__":
    unittest.main(verbosity=2)
