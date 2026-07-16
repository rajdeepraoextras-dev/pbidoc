"""Tests for ``pbicompass.agents.benchmark`` — the machine-readable v3.0
benchmark spec and its deterministic scorer. All offline (``client=None``):
the scorer itself never calls an LLM.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from pbicompass.agents.benchmark import (
    BENCHMARK_CHECKS,
    CHECKS_BY_ID,
    _eval_c13,
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


def _c13_docs(*measures):
    tech = SimpleNamespace(measure_catalog=SimpleNamespace(measures=[
        SimpleNamespace(name=n, plain_english=pe, calculation_logic=cl)
        for (n, pe, cl) in measures
    ]))
    return {"technical": tech}


class C13RationaleStemTest(unittest.TestCase):
    """C13 flags measures whose plain-English lacks business rationale.

    It false-flagged real, well-written prose twice. First a live "Var Plan %"
    ("…indicator for comparing cost centers…") because the rationale whitelist
    spelled ``compare``/``use`` and so missed "comparing"/"using". Widening the
    word list then failed again on "…core metric for budget performance and
    corrective action" and "…critical trigger for budget review and
    re-forecasting" — both plainly explain the why, neither uses a listed word.

    A whitelist cannot enumerate how people write, so the check now measures
    information *added* beyond the measure's name and its restated mechanics.
    These tests keep both real regressions pinned.
    """

    def test_the_second_real_false_flag_passes(self):
        """Verbatim from the final live run's measures[9] and [13]."""
        from pbicompass.agents.benchmark import _adds_meaning
        self.assertTrue(_adds_meaning(
            "Calculates the variance between Actual spend and Plan; core metric for "
            "budget performance and corrective action.",
            "Var Plan", "Subtracts [Plan] from [Actual]."))
        self.assertTrue(_adds_meaning(
            "Calculates the percent variance of Actual spend against Plan; critical "
            "trigger for budget review and re-forecasting.",
            "Var Plan %", "Divides [Var Plan] by [Plan], returning BLANK when [Plan] is blank."))

    def test_an_echo_of_the_name_still_fails(self):
        from pbicompass.agents.benchmark import _adds_meaning
        self.assertFalse(_adds_meaning("The total spend.", "Total Spend",
                                       "Sums the amount column."))
        self.assertFalse(_adds_meaning("Total sales value.", "Total Sales",
                                       "Sums Sales[Amount]."))

    ai = SimpleNamespace(translations={"x": {}})  # C13 only applies when AI ran

    def test_var_plan_pct_prose_passes(self):
        # Verbatim from the live openai/gpt-5.5 run's measures[13].
        docs = _c13_docs((
            "Var Plan %",
            "Percentage variance of actual year-to-date spend versus planned spend. "
            "This is the main relative over-plan or under-plan indicator for comparing "
            "cost centers, vendors, or business areas when selecting next-quarter spend controls.",
            "Divides Var Plan by Plan. Using Plan as the denominator shows the "
            "actual-versus-plan variance relative to the planned spend baseline.",
        ))
        passed, detail, weak = _eval_c13(docs, {}, None, self.ai)
        self.assertTrue(passed, f"expected pass, got {detail} {weak}")

    def test_comparing_and_using_inflections_match(self):
        for verb in ("comparing", "comparison", "compares", "using", "used", "identifies", "monitors"):
            docs = _c13_docs(("M", f"A rich enough measure {verb} things across the model set.", "sums things"))
            passed, _, _ = _eval_c13(docs, {}, None, self.ai)
            self.assertTrue(passed, f"{verb!r} should count as rationale")

    def test_bare_name_echo_still_fails(self):
        # Guard the check still bites: mechanics-only, no rationale.
        docs = _c13_docs(("Total Spend", "The total spend.", "Sums the amount column."))
        passed, _, weak = _eval_c13(docs, {}, None, self.ai)
        self.assertFalse(passed)
        self.assertTrue(weak)


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

    def test_judge_checks_not_scored_by_scorer(self):
        # C13 is judge-only, so the scorer leaves it for the reviewer.
        by_id = {r.check_id: r for r in self.report.results}
        self.assertIsNone(by_id["C13"].passed)

    def test_c8_is_answered_deterministically_not_by_the_judge(self):
        """C8 used to defer to the Senior Reviewer once a schedule string
        existed. Two live runs then had the judge report "no refresh schedule is
        present in any document" about bundles whose technical section 11 plainly
        read "Refresh schedule: Daily 06:00 UTC via on-premises gateway". Every
        part of C8 is a checkable fact, so it is now scored from the artifacts —
        a judge must not be asked a question the scorer can answer."""
        by_id = {r.check_id: r for r in self.report.results}
        self.assertTrue(by_id["C8"].passed)
        self.assertIn("documented", by_id["C8"].detail)

    def test_c8_still_fails_honestly_when_refresh_is_undocumented(self):
        docs = {dtype: gen.generate(self.model, None, owner="BI Team")   # no refresh intake
                for dtype, gen in DOCUMENT_TYPES.items()}
        c8 = next(r for r in run_benchmark(docs, model=self.model).results if r.check_id == "C8")
        self.assertFalse(c8.passed)

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
