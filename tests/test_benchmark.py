"""Tests for ``pbicompass.agents.benchmark`` — the machine-readable v3.0
benchmark spec and its deterministic scorer. All offline (``client=None``):
the scorer itself never calls an LLM.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents.benchmark import (
    BENCHMARK_CHECKS,
    CHECKS_BY_ID,
    run_benchmark,
)
from pbicompass.agents.generators import DOCUMENT_TYPES
from pbicompass.schemas.model import SemanticModel

CS_FIXTURE = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"

_INTAKE = dict(
    owner="Jane Doe",
    refresh="Daily 06:00 UTC",
    assumptions="Spend data excludes intercompany transfers.",
)


def _make_docs(model: SemanticModel) -> dict:
    return {dtype: gen.generate(model, None, **_INTAKE)
            for dtype, gen in DOCUMENT_TYPES.items()}


class BenchmarkSpecTest(unittest.TestCase):
    def test_points_sum_to_100_and_pillars_match_rubric(self):
        self.assertEqual(sum(c.points for c in BENCHMARK_CHECKS), 100)
        pillar_totals = {}
        for c in BENCHMARK_CHECKS:
            pillar_totals[c.pillar] = pillar_totals.get(c.pillar, 0) + c.points
        self.assertEqual(pillar_totals, {1: 30, 2: 25, 3: 20, 4: 15, 5: 10})

    def test_ids_unique(self):
        ids = [c.id for c in BENCHMARK_CHECKS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_methods_valid(self):
        for c in BENCHMARK_CHECKS:
            self.assertIn(c.method, ("auto", "judge", "render", "manual"), c.id)


class BenchmarkScorerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))
        cls.docs = _make_docs(cls.model)
        cls.report = run_benchmark(cls.docs, model=cls.model)

    def test_every_spec_id_gets_a_result(self):
        result_ids = {r.check_id for r in self.report.results}
        self.assertEqual(result_ids, set(CHECKS_BY_ID))

    def test_render_and_manual_checks_not_evaluated(self):
        for r in self.report.results:
            if CHECKS_BY_ID[r.check_id].method in ("render", "manual"):
                self.assertIsNone(r.passed, f"{r.check_id} must not be scored pre-render")

    def test_judge_checks_not_scored_by_scorer_when_floor_met(self):
        # C8's deterministic floor is met (refresh schedule supplied) so it
        # stays unevaluated for the reviewer; C13 is judge-only.
        by_id = {r.check_id: r for r in self.report.results}
        self.assertIsNone(by_id["C8"].passed)
        self.assertIsNone(by_id["C13"].passed)

    def test_clean_offline_bundle_triggers_no_gates(self):
        self.assertEqual(self.report.gates_triggered, [])

    def test_trust_checks_pass_on_clean_bundle(self):
        by_id = {r.check_id: r for r in self.report.results}
        for cid in ("T1", "T2", "T3", "T4", "T6"):
            self.assertTrue(by_id[cid].passed, f"{cid} failed: {by_id[cid].detail}")

    def test_completeness_checks_reflect_supplied_intake(self):
        by_id = {r.check_id: r for r in self.report.results}
        self.assertTrue(by_id["C11"].passed, by_id["C11"].detail)  # owner supplied
        self.assertTrue(by_id["C12"].passed, by_id["C12"].detail)  # assumptions supplied

    def test_score_bounded_by_evaluated_points(self):
        self.assertLessEqual(self.report.score, self.report.max_evaluated_points)
        self.assertGreater(self.report.score, 0)

    def test_scorer_never_mutates_documents(self):
        docs = _make_docs(self.model)
        before = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        run_benchmark(docs, model=self.model)
        after = {dtype: doc.to_dict() for dtype, doc in docs.items()}
        self.assertEqual(before, after)

    def test_to_dict_round_trips(self):
        d = self.report.to_dict()
        self.assertEqual(d["benchmark_version"], "3.0")
        self.assertEqual(len(d["results"]), len(BENCHMARK_CHECKS))


class BenchmarkFailureDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))

    def test_injected_punt_phrase_fails_t1_and_caps_via_g1(self):
        docs = _make_docs(self.model)
        docs["audit"].narrative_overview = (
            (docs["audit"].narrative_overview or "The model is reviewed below.")
            + " Unknown — requires business confirmation."
        )
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["T1"].passed, by_id["T1"].detail)
        self.assertIn("audit:narrative_overview", by_id["T1"].locations)
        self.assertIn("G1", report.gates_triggered)
        self.assertLessEqual(report.score, 75)

    def test_punt_phrase_whitelisted_in_measure_description_cells(self):
        docs = _make_docs(self.model)
        measures = docs["technical"].measure_catalog.measures
        self.assertTrue(measures, "fixture must have measures")
        measures[0].plain_english = "Unknown — requires business confirmation."
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertTrue(by_id["T1"].passed,
                        "punt in an unexplained-description cell must not fail T1")

    def test_health_score_mismatch_fails_t2_and_caps_via_g3(self):
        docs = _make_docs(self.model)
        self.assertIsNotNone(docs["executive"].health)
        docs["executive"].health.overall = docs["audit"].health.overall + 1
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["T2"].passed, by_id["T2"].detail)
        self.assertIn("G3", report.gates_triggered)
        self.assertLessEqual(report.score, 80)

    def test_prose_score_mention_mismatch_fails_t2(self):
        docs = _make_docs(self.model)
        actual = docs["audit"].health.overall
        wrong = actual + 3
        docs["audit"].narrative_overview = (
            f"The overall health score is {wrong}, which needs attention."
        )
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["T2"].passed)
        self.assertIn("audit:narrative_overview", by_id["T2"].locations)

    def test_doubled_punctuation_fails_t6(self):
        docs = _make_docs(self.model)
        docs["audit"].narrative_overview = "The model has issues.. It needs review."
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["T6"].passed)
        self.assertIn("audit:narrative_overview", by_id["T6"].locations)

    def test_junk_glossary_term_fails_c6(self):
        docs = _make_docs(self.model)
        from pbicompass.schemas.user_guide_document import GlossaryTerm
        docs["user-guide"].glossary.append(GlossaryTerm(term="select1", plain_definition="x"))
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["C6"].passed, by_id["C6"].detail)

    def test_missing_owner_fails_c11(self):
        docs = {dtype: gen.generate(self.model, None) for dtype, gen in DOCUMENT_TYPES.items()}
        report = run_benchmark(docs, model=self.model)
        by_id = {r.check_id: r for r in report.results}
        self.assertFalse(by_id["C11"].passed)
        self.assertFalse(by_id["C12"].passed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
