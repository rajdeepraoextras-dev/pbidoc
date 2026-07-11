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

    def test_star_schema_detail_never_doubles_the_phrase(self):
        # P2: "a star schema — a star schema centred on..." — the detail
        # text used to prefix "a star schema" onto a shape string that
        # already started with it.
        checks = {c.id: c for c in audit_rules.check_best_practices(_model())}
        detail = checks["star_schema"].detail
        self.assertEqual(detail.lower().count("a star schema"), 1)
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


class VertiPaqRulesTest(unittest.TestCase):
    """Day 7: threshold rules over measured ``cardinality``/``size_bytes``
    (pbixray ``--stats`` only) — every rule here must no-op when both stats
    are absent, since neither field is ever populated by the .pbip/TMDL/TMSL
    parsers (only a legacy .pbix parsed with ``--stats`` carries real
    values)."""

    def test_near_constant_dimension_flagged_on_measured_cardinality(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Region", columns=[
                Column(name="IsActive", data_type="string", cardinality=1),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        near_constant = [r for r in risks if r.kind == "near_constant_dimension"]
        self.assertEqual(len(near_constant), 1)
        self.assertEqual(near_constant[0].object_name, "IsActive")
        self.assertEqual(near_constant[0].table, "Region")
        self.assertEqual(near_constant[0].rule_id, "PBIC-PERF-010")

    def test_near_constant_dimension_not_flagged_when_hidden(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Region", columns=[
                Column(name="IsActive", data_type="string", cardinality=1, is_hidden=True),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "near_constant_dimension" for r in risks))

    def test_near_constant_dimension_no_op_without_stats(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Region", columns=[Column(name="IsActive", data_type="string")])],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "near_constant_dimension" for r in risks))

    def test_wide_text_dominates_size_flagged(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Products", columns=[
                Column(name="Comments", data_type="string", size_bytes=9_000_000),
                Column(name="ProductKey", data_type="int64", size_bytes=500_000),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        dominant = [r for r in risks if r.kind == "wide_text_dominates_size"]
        self.assertEqual(len(dominant), 1)
        self.assertEqual(dominant[0].object_name, "Comments")
        self.assertEqual(dominant[0].rule_id, "PBIC-PERF-011")

    def test_wide_text_dominates_size_not_flagged_below_dominance_threshold(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Products", columns=[
                Column(name="Comments", data_type="string", size_bytes=1_200_000),
                Column(name="ProductKey", data_type="int64", size_bytes=1_000_000),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "wide_text_dominates_size" for r in risks))

    def test_wide_text_dominates_size_no_op_below_min_table_size(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Products", columns=[
                Column(name="Comments", data_type="string", size_bytes=900),
                Column(name="ProductKey", data_type="int64", size_bytes=100),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "wide_text_dominates_size" for r in risks))

    def test_wide_text_dominates_size_no_op_without_stats(self):
        from pbicompass.schemas.model import Column, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Products", columns=[
                Column(name="Comments", data_type="string"),
                Column(name="ProductKey", data_type="int64"),
            ])],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "wide_text_dominates_size" for r in risks))

    def test_sample_sales_unaffected_without_stats(self):
        """Regression: SampleSales is parsed without ``--stats``, so neither
        new rule should ever fire on it, and the existing 'every risk
        discloses its heuristic nature' guarantee must still hold."""
        risks = audit_rules.find_performance_risks(_model())
        self.assertFalse(any(r.kind in ("near_constant_dimension", "wide_text_dominates_size")
                              for r in risks))


class AutoDateTimeDetectionTest(unittest.TestCase):
    """D5 root cause: Power BI's Auto Date/Time creates two hidden tables per
    date column, ``LocalDateTable_<GUID>`` and ``DateTableTemplate_<GUID>``.
    Only the former used to be matched (the latter needed an unrelated
    'TemplateId' substring no real table carries) — fixed as part of Day 7
    since the audit synthesizer needs this signal to fire reliably to
    cluster it with its dependent findings."""

    def test_date_table_template_alone_is_detected(self):
        from pbicompass.schemas.model import SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="DateTableTemplate_abc123", is_hidden=True)],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertTrue(any(r.kind == "auto_datetime" for r in risks))

    def test_local_date_table_alone_is_still_detected(self):
        from pbicompass.schemas.model import SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[Table(name="LocalDateTable_abc123", is_hidden=True)],
        )
        risks = audit_rules.find_performance_risks(model)
        self.assertTrue(any(r.kind == "auto_datetime" for r in risks))

    def test_no_auto_date_tables_not_flagged(self):
        from pbicompass.schemas.model import SemanticModel, Table
        model = SemanticModel(report_name="R", tables=[Table(name="Sales")])
        risks = audit_rules.find_performance_risks(model)
        self.assertFalse(any(r.kind == "auto_datetime" for r in risks))


class AutoDateTimeClusterSignalsTest(unittest.TestCase):
    """D5/Day 2: Auto Date/Time's hidden tables used to inflate Unused
    Assets with their own auto-generated, otherwise-unused columns — a
    model with Auto Date/Time enabled but nothing else wrong would still
    show a pile of "unused calculated columns" a reviewer can't act on
    (they're deleted for free the moment Auto Date/Time is turned off).
    Rather than relying on the root-cause synthesizer to *notice* and
    cluster these two findings together after the fact, they're now
    deterministically rolled into the PBIC-PERF-007 finding itself: the
    auto-generated columns are excluded from Unused Assets (counted on
    ``auto_datetime_excluded`` instead) and the finding's own detail names
    the excluded count."""

    def test_auto_date_time_columns_excluded_from_unused_assets(self):
        from pbicompass.schemas.model import Column, Relationship, SemanticModel, Table
        model = SemanticModel(
            report_name="R",
            tables=[
                Table(name="Sales"),
                Table(name="LocalDateTable_abc123", is_hidden=True, columns=[
                    Column(name="Year", data_type="int64", is_hidden=True,
                           is_calculated=True, expression="YEAR([Date])"),
                ]),
            ],
            # Real Auto Date/Time always wires a many-to-one relationship
            # from the host table to its hidden shadow table — included so
            # this fixture doesn't *also* trip the (separate) unused-table
            # signal and conflate two different counts in one assertion.
            relationships=[Relationship(from_table="Sales", from_column="Date",
                                        to_table="LocalDateTable_abc123", to_column="Date")],
        )
        risks = audit_rules.find_performance_risks(model)
        auto_dt = next((r for r in risks if r.kind == "auto_datetime"), None)
        self.assertIsNotNone(auto_dt, "root-cause finding must fire")
        self.assertIn("excluded from the Unused Assets report", auto_dt.detail)

        unused = audit_rules.find_unused_assets(model)
        self.assertNotIn({"table": "LocalDateTable_abc123", "column": "Year"}, unused.calculated_columns)
        self.assertEqual(unused.auto_datetime_excluded, 1)


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


class RulesEngineTest(unittest.TestCase):
    def test_load_rules_config_default_empty(self):
        config = audit_rules.load_rules_config()
        self.assertIsInstance(config, dict)

    def test_process_finding_severity_override(self):
        from pbicompass.schemas.audit_document import DaxFinding
        f = DaxFinding(measure="M", table="T", kind="hardcoded_year", detail="detail", severity="Critical")
        
        original_load = audit_rules.load_rules_config
        try:
            audit_rules.load_rules_config = lambda: {"rules": {"PBIC-DAX-006": {"severity": "High"}}}
            keep = audit_rules.process_finding(f, f.kind)
            self.assertTrue(keep)
            self.assertEqual(f.severity, "High")
            self.assertEqual(f.rule_id, "PBIC-DAX-006")
        finally:
            audit_rules.load_rules_config = original_load

    def test_process_finding_disabled(self):
        from pbicompass.schemas.audit_document import DaxFinding
        f = DaxFinding(measure="M", table="T", kind="hardcoded_year", detail="detail", severity="Critical")
        
        original_load = audit_rules.load_rules_config
        try:
            audit_rules.load_rules_config = lambda: {"rules": {"PBIC-DAX-006": {"enabled": False}}}
            audit_rules.reset_suppressed_rules()
            keep = audit_rules.process_finding(f, f.kind)
            self.assertFalse(keep)
            self.assertIn("PBIC-DAX-006", audit_rules.get_suppressed_rules())
        finally:
            audit_rules.load_rules_config = original_load

    def test_score_trend_stateful(self):
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            history_file = Path(td) / "history.json"
            os.environ["PBICOMPASS_SCORE_HISTORY"] = str(history_file)
            try:
                trend1 = audit_rules.get_and_update_score_history("TestReport", 80)
                self.assertIsNone(trend1)

                trend2 = audit_rules.get_and_update_score_history("TestReport", 85)
                self.assertIsNotNone(trend2)
                self.assertIn("80 → 85 (+5)", trend2)
            finally:
                del os.environ["PBICOMPASS_SCORE_HISTORY"]

    def test_score_trend_off_by_default(self):
        """The hosted service never sets PBICOMPASS_SCORE_HISTORY, so this
        must stay a no-op — the zero-retention guarantee for score trend."""
        self.assertIsNone(audit_rules.get_and_update_score_history("AnyReport", 80))

    def test_shared_score_trend_writes_only_once_per_job(self):
        # Day 5: audit/technical/executive all want a trend string in a
        # --document all job. Calling the raw function once per doc type
        # would append the same run 2-3 times and have later calls compare
        # against the earlier calls' own fresh entries from the same run.
        import os
        import tempfile
        from pathlib import Path

        from pbicompass.agents.context import JobAIContext

        with tempfile.TemporaryDirectory() as td:
            history_file = Path(td) / "history.json"
            os.environ["PBICOMPASS_SCORE_HISTORY"] = str(history_file)
            try:
                # Seed one prior run directly.
                audit_rules.get_and_update_score_history("TestReport", 80)

                ctx = JobAIContext()
                first = audit_rules.get_shared_score_trend(ctx, "TestReport", 85)
                second = audit_rules.get_shared_score_trend(ctx, "TestReport", 85)
                self.assertEqual(first, second)
                self.assertIn("80 → 85 (+5)", first)

                import json
                history = json.loads(history_file.read_text(encoding="utf-8"))
                # Exactly one new entry appended for this job, not two.
                self.assertEqual(len(history["TestReport"]), 2)
            finally:
                del os.environ["PBICOMPASS_SCORE_HISTORY"]

    def test_shared_score_trend_without_ai_context_calls_through_directly(self):
        # ai_context=None (offline / single-doc-type / direct generator
        # call, as most tests do) must behave exactly like the raw function.
        self.assertIsNone(audit_rules.get_shared_score_trend(None, "AnyReport", 80))

    def test_get_threshold_returns_default_when_unset(self):
        self.assertEqual(audit_rules.get_threshold("visual_density_limit", 12), 12)

    def test_get_threshold_reads_from_rules_toml(self):
        original_load = audit_rules.load_rules_config
        try:
            audit_rules.load_rules_config = lambda: {"thresholds": {"visual_density_limit": 5}}
            self.assertEqual(audit_rules.get_threshold("visual_density_limit", 12), 5)
        finally:
            audit_rules.load_rules_config = original_load

    def test_visual_density_threshold_is_configurable(self):
        from pbicompass.schemas.model import Page, SemanticModel, Visual

        model = SemanticModel(
            report_name="R",
            pages=[Page(id="p1", display_name="Overview",
                       visuals=[Visual(id=str(i), type="card") for i in range(6)])],
        )
        # default threshold (12) — 6 visuals shouldn't trip it
        self.assertFalse(any(r.kind == "visual_density" for r in audit_rules.find_performance_risks(model)))

        original_load = audit_rules.load_rules_config
        try:
            audit_rules.load_rules_config = lambda: {"thresholds": {"visual_density_limit": 5}}
            risks = audit_rules.find_performance_risks(model)
            self.assertTrue(any(r.kind == "visual_density" for r in risks))
        finally:
            audit_rules.load_rules_config = original_load

    def test_validate_rules_file_reports_missing_and_invalid(self):
        import tempfile
        from pathlib import Path

        self.assertIsNotNone(audit_rules.validate_rules_file("/no/such/file.toml"))
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.toml"
            bad.write_text("not [ valid", encoding="utf-8")
            self.assertIsNotNone(audit_rules.validate_rules_file(bad))

            good = Path(td) / "good.toml"
            good.write_text('[rules."PBIC-DAX-003"]\nenabled = false\n', encoding="utf-8")
            self.assertIsNone(audit_rules.validate_rules_file(good))

    def test_set_rules_config_path_is_used_by_load_rules_config(self):
        import tempfile
        from pathlib import Path

        try:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "custom.toml"
                path.write_text('[thresholds]\nvisual_density_limit = 3\n', encoding="utf-8")
                audit_rules.set_rules_config_path(path)
                self.assertEqual(audit_rules.get_threshold("visual_density_limit", 12), 3)
        finally:
            audit_rules.set_rules_config_path(None)


def _stress_model():
    """A synthetic model that fires every High/Critical-severity rule at
    once — bidirectional-on-a-fact-table, direct many-to-many, a
    relationship cycle, a disconnected table, a sensitive column name, a
    hardcoded local file path, and a hardcoded year — so Part J's fix-
    snippet-coverage requirement (J.A.2) can be checked against more than
    one rule at a time."""
    from pbicompass.schemas.model import (
        Column, DataSource, Measure, Relationship, SemanticModel, Table,
    )

    return SemanticModel(
        report_name="StressTest",
        tables=[
            Table(name="Sales", kind="fact", columns=[
                Column(name="SalesID", is_key=True),
                Column(name="CustomerID"), Column(name="ProductID"),
            ]),
            Table(name="Customer", kind="dimension", columns=[
                Column(name="CustomerID", is_key=True), Column(name="SSN"),
            ]),
            Table(name="Product", kind="dimension", columns=[
                Column(name="ProductID", is_key=True), Column(name="CategoryID"),
            ]),
            Table(name="Category", kind="dimension", columns=[Column(name="CategoryID", is_key=True)]),
            Table(name="Orphan", kind="dimension", columns=[Column(name="X")]),
        ],
        relationships=[
            Relationship(from_table="Sales", from_column="CustomerID", to_table="Customer",
                        to_column="CustomerID", cross_filter="both"),
            Relationship(from_table="Sales", from_column="ProductID", to_table="Product",
                        to_column="ProductID", from_cardinality="many", to_cardinality="many"),
            Relationship(from_table="Product", from_column="CategoryID", to_table="Category",
                        to_column="CategoryID"),
            Relationship(from_table="Category", from_column="CategoryID", to_table="Sales",
                        to_column="ProductID"),
        ],
        data_sources=[DataSource(type="Excel.Workbook", detail=r"C:\Users\alice\Desktop\sales.xlsx")],
    )


class BridgeAndFactChecksTest(unittest.TestCase):
    """PBIC-MOD-014/015 (J.A.1b) were declared in FINDING_RULES/RULE_METADATA
    but no check ever produced them — meaning the audit's "checks run" count
    silently included two rules that could never fail. They must now be
    real, independently-triggerable checks."""

    def test_m2m_no_bridge_fires_for_direct_many_to_many(self):
        checks = audit_rules.check_best_practices(_stress_model())
        check = next(c for c in checks if c.id == "m2m_no_bridge")
        self.assertFalse(check.passed)
        self.assertEqual(check.rule_id, "PBIC-MOD-014")

    def test_m2m_no_bridge_passes_when_one_side_is_bridge_shaped(self):
        """A junction table (few key-only columns, no measures, unclassified
        kind) sitting on one side of the M:N relationship is exactly the
        "has a bridge" case — even without a bridge/junction/xref name."""
        from pbicompass.schemas.model import Column, Relationship, SemanticModel, Table

        model = SemanticModel(
            report_name="R",
            tables=[
                Table(name="Sales", kind="fact", columns=[Column(name="AssignmentID")]),
                Table(name="Assignment", columns=[Column(name="SalesID"), Column(name="ProductID")]),
            ],
            relationships=[
                Relationship(from_table="Sales", from_column="AssignmentID", to_table="Assignment",
                            to_column="SalesID", from_cardinality="many", to_cardinality="many"),
            ],
        )
        check = next(c for c in audit_rules.check_best_practices(model) if c.id == "m2m_no_bridge")
        self.assertTrue(check.passed)

    def test_m2m_no_bridge_passes_when_bridge_named_table_present(self):
        from pbicompass.schemas.model import Column, Relationship, SemanticModel, Table

        model = SemanticModel(
            report_name="R",
            tables=[
                Table(name="Sales", kind="fact", columns=[Column(name="BridgeID")]),
                Table(name="SalesProductBridge", kind="dimension",
                     columns=[Column(name="SalesID"), Column(name="ProductID"), Column(name="Qty")]),
            ],
            relationships=[
                Relationship(from_table="Sales", from_column="BridgeID", to_table="SalesProductBridge",
                            to_column="SalesID", from_cardinality="many", to_cardinality="many"),
            ],
        )
        check = next(c for c in audit_rules.check_best_practices(model) if c.id == "m2m_no_bridge")
        self.assertTrue(check.passed)

    def test_bidirectional_fact_fires_when_bidi_touches_a_fact_table(self):
        checks = audit_rules.check_best_practices(_stress_model())
        check = next(c for c in checks if c.id == "bidirectional_fact")
        self.assertFalse(check.passed)
        self.assertEqual(check.rule_id, "PBIC-MOD-015")

    def test_bidirectional_fact_passes_for_dimension_to_dimension_bidi(self):
        from pbicompass.schemas.model import Column, Relationship, SemanticModel, Table

        model = SemanticModel(
            report_name="R",
            tables=[
                Table(name="Product", kind="dimension", columns=[Column(name="CategoryID")]),
                Table(name="Category", kind="dimension", columns=[Column(name="CategoryID", is_key=True)]),
            ],
            relationships=[
                Relationship(from_table="Product", from_column="CategoryID", to_table="Category",
                            to_column="CategoryID", cross_filter="both"),
            ],
        )
        check = next(c for c in audit_rules.check_best_practices(model) if c.id == "bidirectional_fact")
        self.assertTrue(check.passed)


class ChecksLedgerTest(unittest.TestCase):
    """4.1 / J.A.1: 'Checks run' must count the full stable-ID rule
    registry, not just findings that fired — a rule that never produced a
    finding still has to show up as a passed check."""

    def test_ledger_totals_add_up_and_match_total_rule_count(self):
        model = _model()
        dax = audit_rules.find_dax_findings(model.all_measures())
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        ledger = audit_rules.compute_checks_ledger(dax, practices, perf, gov, [])

        self.assertEqual(ledger["run"], audit_rules.TOTAL_RULE_COUNT)
        self.assertEqual(ledger["run"], ledger["passed"] + ledger["failed"] + ledger["suppressed"])
        self.assertGreater(ledger["failed"], 0)  # SampleSales isn't a perfect model
        self.assertGreater(ledger["passed"], 0)

    def test_suppressed_rule_counted_as_suppressed_not_failed(self):
        model = _model()
        dax = audit_rules.find_dax_findings(model.all_measures())
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        ledger = audit_rules.compute_checks_ledger(dax, practices, perf, gov, ["PBIC-DAX-003"])

        self.assertEqual(ledger["suppressed"], 1)
        category = audit_rules.RULE_METADATA["PBIC-DAX-003"][0]
        self.assertEqual(ledger["by_category"][category]["suppressed"], 1)


class FixSnippetCoverageTest(unittest.TestCase):
    """J.A.2: every High/Critical recommendation must carry a fix snippet
    parameterized with the finding's actual objects, not generic advice."""

    def test_every_high_or_critical_recommendation_has_a_code_fix(self):
        model = _stress_model()
        from pbicompass.schemas.model import Measure

        for t in model.tables:
            if t.name == "Sales":
                t.measures.append(Measure(name="Revenue2020", expression="SUM(Sales[Amount]) + 2020",
                                          table="Sales", description="x"))

        dax = audit_rules.find_dax_findings(model.all_measures())
        practices = audit_rules.check_best_practices(model)
        perf = audit_rules.find_performance_risks(model)
        gov = audit_rules.check_governance(model)
        unused = audit_rules.find_unused_assets(model)
        recs = audit_rules.build_recommendations(dax, practices, perf, gov, unused, model=model)

        high_critical = [r for r in recs if r.priority in ("Critical", "High")]
        self.assertGreaterEqual(len(high_critical), 5)  # the fixture is built to trip several
        missing = [r for r in high_critical if "```" not in r.suggested_fix]
        self.assertEqual(missing, [], f"missing fix snippets for: {[r.rule_id for r in missing]}")
        # every fix snippet must name a real object from the fixture, not just generic prose
        real_objects = {"Sales", "Product", "Customer", "Category", "Orphan", "SSN", "Revenue2020",
                        "sales.xlsx"}
        for r in high_critical:
            self.assertTrue(any(obj in r.suggested_fix for obj in real_objects),
                           f"{r.rule_id} fix snippet doesn't name a real object: {r.suggested_fix}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
