"""Tests for the Requirements Traceability Matrix (Day 4): ``agents/traceability.py``'s
requirement parser, deterministic keyword-overlap matcher, LLM-routed verdict
pass, and end-to-end wiring into the technical/executive/audit documents."""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import AuditReportGenerator, ExecutiveSummaryGenerator
from pbicompass.agents.traceability import (
    RequirementCoverage,
    build_candidates,
    build_requirements_matrix,
    coverage_stat,
    match_candidates,
    parse_requirements,
)
from pbicompass.parsers import detect_and_parse

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"

# The 5-requirement fixture the "done when" bar names: 4 real requirements
# the SampleSales model can satisfy, and one deliberate gap (nothing in the
# fixture is about inventory/forecasting).
FIVE_REQUIREMENTS = """\
[Must] Show total revenue by region
[Must] Track order quantity trends over time
[Should] Support drill-through to customer detail
[Should] Calculate average order value
[Must] Forecast next quarter inventory needs"""


def _model():
    return detect_and_parse(FIXTURE)


class ParseRequirementsTest(unittest.TestCase):
    def test_priority_tags_are_extracted(self):
        parsed = parse_requirements("[Must] Show revenue by region\n[Should] Support drill-through")
        self.assertEqual(parsed, [("Must", "Show revenue by region"), ("Should", "Support drill-through")])

    def test_lowercase_priority_tag_is_normalized(self):
        parsed = parse_requirements("[must] Show revenue by region")
        self.assertEqual(parsed, [("Must", "Show revenue by region")])

    def test_missing_priority_tag_is_empty_string(self):
        parsed = parse_requirements("Show revenue by region")
        self.assertEqual(parsed, [("", "Show revenue by region")])

    def test_blank_lines_are_skipped(self):
        parsed = parse_requirements("[Must] A requirement\n\n\n[Should] Another one\n")
        self.assertEqual(len(parsed), 2)

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(parse_requirements(""), [])
        self.assertEqual(parse_requirements(None), [])


class MatchCandidatesTest(unittest.TestCase):
    def test_higher_overlap_ranks_first(self):
        candidates = [
            {"kind": "measure", "name": "A", "anchor": "measure-a", "text": "Total Revenue"},
            {"kind": "measure", "name": "B", "anchor": "measure-b", "text": "Total Revenue by Region"},
        ]
        matched = match_candidates("Show total revenue by region", candidates)
        self.assertEqual(matched[0]["anchor"], "measure-b")

    def test_no_overlap_returns_empty(self):
        candidates = [{"kind": "measure", "name": "A", "anchor": "measure-a", "text": "Total Revenue"}]
        matched = match_candidates("Forecast inventory needs", candidates)
        self.assertEqual(matched, [])

    def test_top_n_is_respected(self):
        candidates = [
            {"kind": "measure", "name": f"M{i}", "anchor": f"measure-m{i}", "text": "Revenue"}
            for i in range(10)
        ]
        matched = match_candidates("Show revenue", candidates, top_n=3)
        self.assertEqual(len(matched), 3)

    # -- P1: RTM matcher recall ------------------------------------------

    def test_plural_and_gerund_forms_still_match(self):
        # P1's own worked example: an "IT Spend Trend" page must match a
        # requirement phrased "spending trends" — different word forms of
        # the same concept, previously zero keyword overlap.
        candidates = [{"kind": "page", "name": "IT Spend Trend", "anchor": "page-it-spend-trend",
                       "text": "IT Spend Trend"}]
        matched = match_candidates("Track monthly and yearly spending trends", candidates)
        self.assertEqual(len(matched), 1)

    def test_used_candidate_is_boosted_over_unused(self):
        candidates = [
            {"kind": "column", "name": "Department[VP]", "anchor": "column-department-vp",
             "text": "Department VP", "used": False},
            {"kind": "column", "name": "Department[Department]", "anchor": "column-department-department",
             "text": "Department Department", "used": True},
        ]
        matched = match_candidates("Break spending down by Department", candidates)
        self.assertEqual(matched[0]["anchor"], "column-department-department")

    def test_self_named_dimension_column_is_boosted_over_lone_column(self):
        # P1's own worked example: prefer a dimension's own canonical
        # attribute column (Department[Department]) over an unrelated
        # column that just happens to share the table-name keyword
        # (Department[VP]).
        candidates = [
            {"kind": "column", "name": "Department[VP]", "anchor": "column-department-vp",
             "text": "Department VP", "used": True, "self_named": False},
            {"kind": "column", "name": "Department[Department]", "anchor": "column-department-department",
             "text": "Department Department", "used": True, "self_named": True},
        ]
        matched = match_candidates("Break spending down by Department", candidates)
        self.assertEqual(matched[0]["anchor"], "column-department-department")

    def test_candidates_without_used_or_self_named_keys_still_score(self):
        # Backward compatible: a caller that doesn't set the new P1 keys
        # (e.g. a hand-built candidate list, as most tests here use) must
        # not raise and must still score on keyword overlap alone.
        candidates = [{"kind": "measure", "name": "A", "anchor": "measure-a", "text": "Total Revenue"}]
        matched = match_candidates("Show total revenue", candidates)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["score"], 2)


class TimeIntelligenceKeywordsTest(unittest.TestCase):
    """P1: time-intelligence DAX functions must surface trend/period
    vocabulary even when the measure's own name/description doesn't."""

    def test_totalytd_measure_matches_a_yearly_trend_requirement(self):
        from pbicompass.schemas.model import Column, Measure, ModelMeta, Page, SemanticModel, Table, Visual

        measure = Measure(name="Sale_YTD", table="Sales",
                          expression="TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])")
        table = Table(name="Sales", columns=[Column(name="Amount")], measures=[measure])
        model = SemanticModel(
            report_name="R", meta=ModelMeta(), tables=[table],
            pages=[Page(id="p1", display_name="Overview",
                        visuals=[Visual(id="v1", type="card", fields=["Sales.Sale_YTD"])])],
        )
        candidates = build_candidates(model)
        matched = match_candidates("Track yearly spending trends", candidates)
        self.assertTrue(any(c["name"] == "Sale_YTD" for c in matched))

    def test_measure_with_no_time_intelligence_is_unaffected(self):
        from pbicompass.schemas.model import Column, Measure, ModelMeta, SemanticModel, Table

        measure = Measure(name="Total", table="Sales", expression="SUM(Sales[Amount])")
        table = Table(name="Sales", columns=[Column(name="Amount")], measures=[measure])
        model = SemanticModel(report_name="R", meta=ModelMeta(), tables=[table])
        candidates = build_candidates(model)
        total = next(c for c in candidates if c["name"] == "Total")
        self.assertNotIn("yearly", total["text"])


class BuildCandidatesTest(unittest.TestCase):
    def test_candidates_cover_measures_columns_and_pages(self):
        model = _model()
        candidates = build_candidates(model)
        kinds = {c["kind"] for c in candidates}
        self.assertEqual(kinds, {"measure", "column", "page"})

    def test_measure_anchor_matches_render_convention(self):
        model = _model()
        candidates = build_candidates(model)
        measure_anchors = {c["anchor"] for c in candidates if c["kind"] == "measure"}
        self.assertIn("measure-total-revenue", measure_anchors)

    def test_hidden_pages_are_excluded(self):
        model = _model()
        candidates = build_candidates(model)
        page_names = {c["name"] for c in candidates if c["kind"] == "page"}
        hidden_names = {p.display_name for p in model.pages if p.is_hidden}
        self.assertFalse(page_names & hidden_names)

    def test_every_candidate_carries_a_used_flag(self):
        # P1: build_candidates() must always set "used" (used in a
        # relative later score boost) — never a KeyError further downstream.
        model = _model()
        candidates = build_candidates(model)
        for c in candidates:
            self.assertIn("used", c)

    def test_pages_are_always_used(self):
        model = _model()
        candidates = build_candidates(model)
        self.assertTrue(all(c["used"] for c in candidates if c["kind"] == "page"))


class BuildRequirementsMatrixDeterministicTest(unittest.TestCase):
    """The offline (no client) path — a real, reproducible verdict from
    keyword overlap alone."""

    def test_five_requirement_fixture_flags_exactly_one_gap(self):
        model = _model()
        matrix = build_requirements_matrix(model, FIVE_REQUIREMENTS)
        self.assertEqual(len(matrix), 5)
        gaps = [r for r in matrix if r.status == "Gap"]
        self.assertEqual(len(gaps), 1)
        self.assertIn("inventory", gaps[0].text.lower())

    def test_covered_requirements_have_working_evidence_anchors(self):
        model = _model()
        matrix = build_requirements_matrix(model, FIVE_REQUIREMENTS)
        candidates = build_candidates(model)
        real_anchors = {c["anchor"] for c in candidates}
        non_gap = [r for r in matrix if r.status != "Gap"]
        self.assertTrue(non_gap, "at least one requirement should be Covered/Partial")
        for r in non_gap:
            self.assertTrue(r.evidence, f"{r.text!r} has a non-Gap status but no evidence")
            for e in r.evidence:
                self.assertIn(e.anchor, real_anchors)

    def test_gap_requirement_has_no_evidence(self):
        model = _model()
        matrix = build_requirements_matrix(model, FIVE_REQUIREMENTS)
        gap = next(r for r in matrix if r.status == "Gap")
        self.assertEqual(gap.evidence, [])

    def test_priority_tags_survive_into_the_matrix(self):
        model = _model()
        matrix = build_requirements_matrix(model, FIVE_REQUIREMENTS)
        self.assertEqual([r.priority for r in matrix], ["Must", "Must", "Should", "Should", "Must"])

    def test_no_requirements_returns_empty_matrix(self):
        model = _model()
        self.assertEqual(build_requirements_matrix(model, None), [])
        self.assertEqual(build_requirements_matrix(model, ""), [])

    def test_coverage_stat_format(self):
        model = _model()
        matrix = build_requirements_matrix(model, FIVE_REQUIREMENTS)
        self.assertEqual(coverage_stat(matrix), "4/5")

    def test_coverage_stat_empty_matrix(self):
        self.assertEqual(coverage_stat([]), "")


class FakeTraceabilityClient:
    """A minimal LLMClient that reports canned verdicts for whatever
    ``verdicts`` list is handed to it, ignoring the actual candidate payload."""

    def __init__(self, verdicts: list[dict]):
        self.verdicts = verdicts
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        return {"requirements": self.verdicts}


class ApplyLlmPassGroundingTest(unittest.TestCase):
    """The LLM pass may only cite anchors it was actually offered as a
    candidate for that specific requirement — never invent one."""

    def test_invented_anchor_is_rejected_keeps_deterministic_fallback(self):
        model = _model()
        client = FakeTraceabilityClient([
            {"requirement": "Show total revenue by region", "status": "Covered",
             "evidence": ["measure-does-not-exist"], "rationale": "Fabricated."},
        ])
        matrix = build_requirements_matrix(model, "[Must] Show total revenue by region", client)
        self.assertEqual(len(matrix), 1)
        # The invented anchor is not among the requirement's real candidates,
        # so the LLM's verdict is discarded and the deterministic one stands.
        for e in matrix[0].evidence:
            self.assertNotEqual(e.anchor, "measure-does-not-exist")

    def test_legitimate_anchor_from_candidates_is_accepted(self):
        model = _model()
        candidates = build_candidates(model)
        real_measure_anchor = next(c["anchor"] for c in candidates if c["kind"] == "measure")
        real_measure_name = next(c["name"] for c in candidates if c["kind"] == "measure")
        client = FakeTraceabilityClient([
            {"requirement": "Some requirement mentioning it", "status": "Covered",
             "evidence": [real_measure_anchor], "rationale": "Directly matches."},
        ])
        matrix = build_requirements_matrix(
            model, f"[Must] Some requirement mentioning {real_measure_name}", client,
        )
        self.assertEqual(matrix[0].status, "Covered")
        self.assertTrue(any(e.anchor == real_measure_anchor for e in matrix[0].evidence))

    def test_offline_client_none_uses_deterministic_only(self):
        model = _model()
        matrix = build_requirements_matrix(model, "[Must] Show total revenue by region", None)
        self.assertEqual(len(matrix), 1)

    def test_failing_client_degrades_to_deterministic_with_a_warning(self):
        class _FailingClient:
            def complete_json(self, system, user, schema, *, effort=None):
                raise RuntimeError("boom")

        model = _model()
        warnings: list[str] = []
        matrix = build_requirements_matrix(
            model, "[Must] Show total revenue by region", _FailingClient(), warnings.append,
        )
        self.assertEqual(len(matrix), 1)
        self.assertTrue(any("Requirements Traceability" in w for w in warnings))


class TechnicalGeneratorWiringTest(unittest.TestCase):
    """Day 4 "done when": the 5-requirement fixture, run through the real
    ``generate_document`` pipeline, renders a matrix with exactly one gap
    flagged and every evidence link resolving to a real anchor in the same
    rendered document."""

    def test_matrix_renders_with_one_gap_and_working_anchor_links(self):
        from pbicompass.render import render_html

        model = _model()
        doc = generate_document(model, requirements=FIVE_REQUIREMENTS)

        self.assertEqual(len(doc.requirements_matrix), 5)
        gaps = [r for r in doc.requirements_matrix if r["status"] == "Gap"]
        self.assertEqual(len(gaps), 1)

        html = render_html(doc)
        self.assertIn('id="sec3"', html)
        self.assertIn("Requirements Traceability Matrix", html)
        self.assertIn('<span class="pill fail">Gap</span>', html)
        self.assertIn('<span class="pill pass">Covered</span>', html)

        # Every evidence anchor cited in the rendered table must resolve to
        # a real id somewhere else in the same document (a working link,
        # not a dead one).
        anchors_cited = [e["anchor"] for r in doc.requirements_matrix for e in r["evidence"]]
        self.assertTrue(anchors_cited, "fixture should produce at least one evidence anchor")
        for anchor in anchors_cited:
            self.assertIn(f'id="{anchor}"', html, f"evidence anchor {anchor!r} has no matching id in the document")

    def test_pre_supplied_matrix_is_not_recomputed(self):
        model = _model()
        supplied = [RequirementCoverage(text="A requirement", status="Covered")]
        doc = generate_document(model, requirements=FIVE_REQUIREMENTS, requirements_matrix=supplied)
        self.assertEqual(len(doc.requirements_matrix), 1)
        self.assertEqual(doc.requirements_matrix[0]["text"], "A requirement")


class ExecutiveGeneratorWiringTest(unittest.TestCase):
    def test_coverage_stat_appears_in_rendered_executive_doc(self):
        from pbicompass.render import render_executive_html

        model = _model()
        doc = ExecutiveSummaryGenerator.generate(model, requirements=FIVE_REQUIREMENTS)
        self.assertEqual(doc.requirements_coverage, "4/5")
        html = render_executive_html(doc)
        self.assertIn("Requirements coverage:", html)
        self.assertIn("4/5", html)


class AuditGeneratorWiringTest(unittest.TestCase):
    def test_gap_requirement_surfaces_as_an_audit_finding(self):
        from pbicompass.render import render_audit_html

        model = _model()
        doc = AuditReportGenerator.generate(model, requirements=FIVE_REQUIREMENTS)
        self.assertEqual(len(doc.requirements_gaps), 1)
        self.assertIn("inventory", doc.requirements_gaps[0]["text"].lower())
        html = render_audit_html(doc)
        self.assertIn("Requirements gaps", html)
        self.assertIn("inventory", html.lower())

    def test_fully_covered_requirements_produce_no_gap_finding(self):
        model = _model()
        doc = AuditReportGenerator.generate(model, requirements="[Must] Calculate average order value")
        self.assertEqual(doc.requirements_gaps, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
