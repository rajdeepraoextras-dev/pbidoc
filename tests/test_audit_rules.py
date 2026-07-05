"""Phase 1 tests: the deterministic audit rule engine.

Every function in ``pbicompass.agents.audit_rules`` is a pure function of a
parsed ``SemanticModel`` — these tests assert determinism (same model in,
same findings out, across repeated calls) and specific fixture-driven
findings we know the SampleSales model should produce.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents import audit_rules
from pbicompass.agents.usage import used_column_names, used_measure_names
from pbicompass.parsers import detect_and_parse

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _model():
    return detect_and_parse(FIXTURE)


class DeterminismTest(unittest.TestCase):
    """Running every rule twice against the same model must produce
    identical results — no randomness, no hidden state."""

    def test_health_score_is_deterministic(self):
        model = _model()
        measures = model.all_measures()
        dax = audit_rules.find_dax_findings(measures)
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        unused = audit_rules.find_unused_assets(model)

        first = audit_rules.compute_health_score(dax, practices, perf, gov, unused)
        second = audit_rules.compute_health_score(dax, practices, perf, gov, unused)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first.overall, 0)
        self.assertLessEqual(first.overall, 100)
        self.assertIn(first.band, ("Excellent", "Good", "Fair", "Poor"))

    def test_complexity_is_deterministic(self):
        model = _model()
        first = audit_rules.compute_complexity(model)
        second = audit_rules.compute_complexity(model)
        self.assertEqual(first, second)
        self.assertIn(first.level, ("Low", "Medium", "High"))


class ComplexityTest(unittest.TestCase):
    def test_sample_sales_is_low_complexity(self):
        c = audit_rules.compute_complexity(_model())
        self.assertEqual(c.level, "Low")
        self.assertEqual(c.table_count, 4)
        self.assertEqual(c.measure_count, 4)
        self.assertEqual(c.relationship_count, 3)
        self.assertEqual(c.calculated_column_count, 1)


class DaxFindingsTest(unittest.TestCase):
    def test_flags_missing_descriptions(self):
        model = _model()
        findings = audit_rules.find_dax_findings(model.all_measures())
        missing = [f for f in findings if f.kind == "missing_description"]
        # every SampleSales measure lacks a description
        self.assertEqual({f.measure for f in missing},
                         {"Avg Order Value", "Total Revenue", "Revenue YTD", "Orphan Margin"})

    def test_duplicate_logic_detected(self):
        from pbicompass.schemas.model import Measure
        measures = [
            Measure(name="A", expression="SUM(Sales[Amount])", table="Sales"),
            Measure(name="B", expression="sum ( Sales[Amount] )", table="Sales"),
            Measure(name="C", expression="SUM(Sales[Other])", table="Sales"),
        ]
        findings = audit_rules.find_dax_findings(measures)
        duplicates = {f.measure for f in findings if f.kind == "duplicate_logic"}
        self.assertEqual(duplicates, {"A", "B"})

    def test_very_long_expression_flagged(self):
        from pbicompass.schemas.model import Measure
        long_expr = "SUM(Sales[Amount])" + " + 0" * 200
        measures = [Measure(name="Long", expression=long_expr, table="Sales", description="has one")]
        findings = audit_rules.find_dax_findings(measures)
        kinds = {f.kind for f in findings if f.measure == "Long"}
        self.assertIn("very_long_expression", kinds)
        self.assertNotIn("missing_description", kinds)


class BestPracticesTest(unittest.TestCase):
    def test_sample_sales_checks(self):
        checks = {c.id: c for c in audit_rules.check_best_practices(_model())}
        self.assertTrue(checks["star_schema"].passed)
        self.assertTrue(checks["fact_dimension_separation"].passed)
        self.assertTrue(checks["date_table_present"].passed)
        # SampleSales has a bidirectional Sales<->Date relationship and an
        # inactive ShipDateKey relationship — both known best-practice fails.
        self.assertFalse(checks["bidirectional_filters"].passed)
        self.assertFalse(checks["inactive_relationships"].passed)
        # no description on any measure/column
        self.assertFalse(checks["description_coverage"].passed)

    def test_no_circular_dependency_false_positive_from_inactive_relationship(self):
        """A second (inactive) relationship between the same two tables — the
        common OrderDate/ShipDate pattern — must NOT be flagged as a circular
        dependency risk: only one relationship between a pair can be active
        at a time, so it poses no real filter-propagation ambiguity."""
        checks = {c.id: c for c in audit_rules.check_best_practices(_model())}
        self.assertTrue(checks["circular_dependency_risk"].passed)

    def test_actual_cycle_among_active_relationships_is_flagged(self):
        from pbicompass.schemas.model import Relationship, SemanticModel, Table

        model = SemanticModel(
            report_name="CycleTest",
            tables=[Table(name="A", kind="dimension"), Table(name="B", kind="dimension"),
                    Table(name="C", kind="dimension")],
            relationships=[
                Relationship(from_table="A", from_column="k", to_table="B", to_column="k"),
                Relationship(from_table="B", from_column="k", to_table="C", to_column="k"),
                Relationship(from_table="C", from_column="k", to_table="A", to_column="k"),
            ],
        )
        checks = {c.id: c for c in audit_rules.check_best_practices(model)}
        self.assertFalse(checks["circular_dependency_risk"].passed)


class PerformanceRisksTest(unittest.TestCase):
    def test_visible_id_like_columns_flagged(self):
        risks = audit_rules.find_performance_risks(_model())
        high_card = {r.object_name for r in risks if r.kind == "high_cardinality_signal"}
        self.assertEqual(high_card, {"CustomerKey", "OrderDateKey", "ShipDateKey"})

    def test_every_risk_discloses_heuristic_nature(self):
        """'Do not invent facts' — every performance risk must say it's a
        heuristic, since no row-level data is ever extracted."""
        risks = audit_rules.find_performance_risks(_model())
        self.assertTrue(risks, "expected at least one risk from the fixture")
        for r in risks:
            self.assertIn("heuristic", r.detail.lower())

    def test_heavy_dax_detects_nested_iterators(self):
        from pbicompass.schemas.model import Measure, SemanticModel, Table
        expr = "SUMX(FILTER(Sales, TRUE), CALCULATE(SUM(Sales[Amount])))"
        measures = [Measure(name="Heavy", expression=expr, table="Sales")]
        model = SemanticModel(report_name="R", tables=[Table(name="Sales", measures=measures)])
        risks = audit_rules.find_performance_risks(model)
        self.assertTrue(any(r.kind == "heavy_dax" and r.object_name == "Heavy" for r in risks))


class GovernanceTest(unittest.TestCase):
    def test_flags_missing_owner_and_classification(self):
        findings = audit_rules.check_governance(_model())
        self.assertTrue(any(f.area == "ownership" for f in findings))

    def test_owner_present_suppresses_ownership_finding(self):
        findings = audit_rules.check_governance(_model(), owner="Jane Doe")
        self.assertFalse(any(f.area == "ownership" for f in findings))

    def test_sensitive_column_names_flagged(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Customer", columns=[Column(name="Email", data_type="string")])],
        )
        findings = audit_rules.check_governance(model)
        self.assertTrue(any(f.area == "sensitive_columns" for f in findings))


class UnusedAssetsTest(unittest.TestCase):
    def test_matches_known_fixture_facts(self):
        model = _model()
        unused = audit_rules.find_unused_assets(model)
        self.assertEqual(set(unused.measures), {"Revenue YTD", "Orphan Margin"})
        self.assertIn({"table": "Sales", "column": "LineTotal"}, unused.calculated_columns)
        self.assertIn("Data Quality", unused.report_pages)
        self.assertEqual(unused.tables, [])

    def test_reuses_usage_module(self):
        """Sanity check that audit_rules and the shared usage helpers agree."""
        model = _model()
        used_m = used_measure_names(model)
        used_c = used_column_names(model)
        unused = audit_rules.find_unused_assets(model)
        for m in model.all_measures():
            self.assertEqual(m.name not in used_m, m.name in unused.measures)
        unused_col_names = {c["column"] for c in unused.columns}
        for t in model.tables:
            for c in t.columns:
                if not c.is_hidden and not c.is_calculated and c.name not in used_c:
                    self.assertIn(c.name, unused_col_names)


class RecommendationsTest(unittest.TestCase):
    def test_sorted_by_priority(self):
        model = _model()
        measures = model.all_measures()
        dax = audit_rules.find_dax_findings(measures)
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        unused = audit_rules.find_unused_assets(model)
        recs = audit_rules.build_recommendations(dax, practices, perf, gov, unused)
        self.assertTrue(recs)
        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        priorities = [order[r.priority] for r in recs]
        self.assertEqual(priorities, sorted(priorities))
        for r in recs:
            self.assertTrue(r.issue and r.why_it_matters and r.suggested_fix and r.expected_benefit)

    def test_recommendation_order_follows_first_seen_finding_order(self):
        # Regression: build_recommendations() used to dedupe finding kinds
        # via a bare {set comprehension}. A Python set's iteration order
        # depends on key hashes, not insertion order, so two DAX finding
        # lists carrying the same two kinds in opposite order used to
        # collapse to the *same* (hash-dependent, not necessarily
        # input-order) recommendation order — and that order could also
        # differ between separate process runs on an unchanged model
        # (string hashes are randomized per process), contradicting the
        # "reproducible, not an AI guess" claim these documents make.
        # dict.fromkeys() dedupes while preserving first-seen order, so
        # feeding the same two kinds in opposite order must yield opposite
        # output order.
        from pbicompass.schemas.audit_document import DaxFinding, UnusedAssets

        def make(order):
            return [DaxFinding(measure=k, table="T", kind=k, detail="d", severity="Low") for k in order]

        forward = audit_rules.build_recommendations(make(["missing_description", "naming_issue"]), [], [], [], UnusedAssets())
        reverse = audit_rules.build_recommendations(make(["naming_issue", "missing_description"]), [], [], [], UnusedAssets())

        self.assertEqual([r.issue for r in forward], list(reversed([r.issue for r in reverse])))

    def test_empty_findings_yield_no_recommendations(self):
        from pbicompass.schemas.audit_document import UnusedAssets
        recs = audit_rules.build_recommendations([], [], [], [], UnusedAssets())
        self.assertEqual(recs, [])

    def test_every_recommendation_carries_a_category(self):
        # 1.10: category lets callers (the executive doc) filter out the
        # DAX-jargon-heavy recommendations without re-deriving risk detection.
        model = _model()
        measures = model.all_measures()
        dax = audit_rules.find_dax_findings(measures)
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        unused = audit_rules.find_unused_assets(model)
        recs = audit_rules.build_recommendations(dax, practices, perf, gov, unused)
        seen_dax_kinds = {f.kind for f in dax}
        for r in recs:
            self.assertIn(r.category, {"dax", "modeling", "performance", "governance", "unused_assets"})
        if seen_dax_kinds:
            self.assertTrue(any(r.category == "dax" for r in recs))


class DevLeftoverAndDisconnectedTablesTest(unittest.TestCase):
    def test_dev_leftover_table_name_flagged(self):
        from pbicompass.schemas.model import Column, Table

        model = _model()
        model.tables.append(Table(name="test", columns=[Column(name="Col1"), Column(name="Col2")]))
        checks = {c.id: c for c in audit_rules.check_best_practices(model)}
        self.assertFalse(checks["dev_leftover_naming"].passed)
        self.assertIn("test", checks["dev_leftover_naming"].detail)

    def test_clean_model_passes_dev_leftover_check(self):
        checks = {c.id: c for c in audit_rules.check_best_practices(_model())}
        self.assertTrue(checks["dev_leftover_naming"].passed)

    def test_disconnected_fact_table_flagged(self):
        from pbicompass.schemas.model import SemanticModel, Table

        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Sales", kind="fact"), Table(name="Orphan", kind="fact")],
        )
        checks = {c.id: c for c in audit_rules.check_best_practices(model)}
        self.assertFalse(checks["disconnected_tables"].passed)
        self.assertIn("Orphan", checks["disconnected_tables"].detail)


class HardcodedYearAndPathFindingsTest(unittest.TestCase):
    def test_hardcoded_year_in_dax_is_critical(self):
        from pbicompass.schemas.model import Measure

        measures = [Measure(name="Sales 2020", expression="CALCULATE([Total], 'Date'[Year] = 2020)", table="Sales")]
        findings = audit_rules.find_dax_findings(measures)
        hardcoded = [f for f in findings if f.kind == "hardcoded_year"]
        self.assertEqual(len(hardcoded), 1)
        self.assertEqual(hardcoded[0].severity, "Critical")

    def test_hardcoded_local_path_is_governance_finding(self):
        from pbicompass.schemas.model import DataSource, SemanticModel

        model = SemanticModel(
            report_name="R",
            data_sources=[DataSource(type="Excel.Workbook", detail=r"C:\Users\faisal\Desktop\orders.xlsx")],
        )
        findings = audit_rules.check_governance(model, owner="Jane", classification="Internal")
        paths = [f for f in findings if f.area == "hardcoded_paths"]
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].severity, "High")

    def test_sample_sales_has_no_hardcoded_year_or_path(self):
        # Regression guard: SampleSales' fixture data should not accidentally
        # trip either new check (its data source is a real server address).
        model = _model()
        findings = audit_rules.find_dax_findings(model.all_measures())
        self.assertFalse(any(f.kind == "hardcoded_year" for f in findings))
        governance = audit_rules.check_governance(model, owner="Jane", classification="Internal")
        self.assertFalse(any(f.area == "hardcoded_paths" for f in governance))


if __name__ == "__main__":
    unittest.main(verbosity=2)
