"""End-to-end parser tests for the SampleSales fixture.

Run with either:
    PYTHONPATH=src python -m unittest discover -s tests
    PYTHONPATH=src python -m pytest tests
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.parsers import detect_and_parse
from pbicompass.parsers.tmsl import parse_semantic_model_tmsl
from pbicompass.schemas.model import SemanticModel

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _table(model: SemanticModel, name: str):
    return next(t for t in model.tables if t.name == name)


def _measure(model: SemanticModel, name: str):
    return next(m for m in model.all_measures() if m.name == name)


class TmdlPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)

    def test_counts(self):
        c = self.model.meta.counts
        self.assertEqual(c["tables"], 4)
        self.assertEqual(c["columns"], 18)
        self.assertEqual(c["measures"], 4)
        self.assertEqual(c["relationships"], 3)
        self.assertEqual(c["roles"], 2)
        self.assertEqual(c["pages"], 3)
        self.assertEqual(c["visuals"], 5)

    def test_no_warnings(self):
        self.assertEqual(self.model.meta.warnings, [])

    def test_identity(self):
        self.assertEqual(self.model.report_name, "SampleSales")
        self.assertEqual(self.model.model_name, "SampleSales")
        self.assertEqual(self.model.meta.source_format, "pbip-tmdl")

    def test_multiline_measure_expression(self):
        m = _measure(self.model, "Total Revenue")
        self.assertIn("SUMX", m.expression)
        self.assertIn('Sales[Status] <> "Canceled"', m.expression)
        # properties must NOT leak into the expression body
        self.assertNotIn("formatString", m.expression)
        self.assertNotIn("displayFolder", m.expression)
        self.assertEqual(m.format_string, r"\$#,0")
        self.assertEqual(m.display_folder, "Revenue")

    def test_measure_home_table(self):
        self.assertEqual(_measure(self.model, "Total Revenue").table, "Sales")
        self.assertEqual(_measure(self.model, "Avg Order Value").table, "Key Measures")

    def test_calculated_column(self):
        line_total = next(c for c in _table(self.model, "Sales").columns if c.name == "LineTotal")
        self.assertTrue(line_total.is_calculated)
        self.assertEqual(line_total.expression, "Sales[Quantity] * Sales[UnitPrice]")

    def test_relationships(self):
        rels = {r.name: r for r in self.model.relationships}
        self.assertEqual(rels["sales-date-order"].cross_filter, "both")
        self.assertFalse(rels["sales-date-ship"].is_active)
        self.assertEqual(rels["sales-customer"].from_cardinality, "many")
        self.assertEqual(rels["sales-customer"].to_cardinality, "one")

    def test_rls_roles(self):
        roles = {r.name: r for r in self.model.roles}
        rm = roles["Regional Manager"]
        self.assertEqual(rm.model_permission, "read")
        self.assertEqual(rm.table_permissions[0].table, "Customer")
        self.assertIn('Region] = "West"', rm.table_permissions[0].filter_expression)
        self.assertIn("rep1@contoso.com", roles["Sales Rep"].members)

    def test_parameter_and_expression(self):
        exprs = {e.name: e for e in self.model.expressions}
        self.assertEqual(exprs["ServerName"].kind, "parameter")
        self.assertNotIn("meta [", exprs["ServerName"].expression)
        self.assertEqual(exprs["Sales DW"].kind, "expression")

    def test_data_source_inference(self):
        self.assertEqual(len(self.model.data_sources), 1)
        ds = self.model.data_sources[0]
        self.assertEqual(ds.type, "Sql.Database")
        self.assertEqual(ds.server, "prod-sql.contoso.com")
        self.assertEqual(ds.database, "SalesDW")

    def test_table_kinds(self):
        self.assertEqual(_table(self.model, "Sales").kind, "fact")
        self.assertEqual(_table(self.model, "Customer").kind, "dimension")
        self.assertEqual(_table(self.model, "Date").kind, "dimension")
        self.assertEqual(_table(self.model, "Key Measures").kind, "calculation")

    def test_report_layout(self):
        pages = {p.display_name: p for p in self.model.pages}
        self.assertTrue(pages["Region Detail"].is_drillthrough)
        self.assertTrue(pages["Data Quality"].is_hidden)
        slicers = [v for v in pages["Sales Overview"].visuals if v.is_slicer]
        self.assertEqual(len(slicers), 1)
        tree = next(v for v in pages["Sales Overview"].visuals
                    if v.type == "decompositionTreeVisual")
        self.assertIn("Customer.Region", tree.fields)


class TmslPipelineTest(unittest.TestCase):
    """Smoke-test the JSON model.bim path independently of the fixture."""

    def test_minimal_tmsl(self):
        bim = {
            "name": "Mini",
            "model": {
                "tables": [{
                    "name": "Sales",
                    "columns": [{"name": "Amt", "dataType": "double", "summarizeBy": "sum"}],
                    "measures": [{"name": "Total", "expression": ["SUM(", "Sales[Amt]", ")"]}],
                    "partitions": [{"name": "p", "source": {"type": "m", "expression": "let Source = 1 in Source"}}],
                }],
                "relationships": [{
                    "fromTable": "Sales", "fromColumn": "K",
                    "toTable": "Dim", "toColumn": "K",
                    "crossFilteringBehavior": "bothDirections", "isActive": False,
                }],
                "roles": [{
                    "name": "R", "modelPermission": "read",
                    "tablePermissions": [{"name": "Sales", "filterExpression": "Sales[Amt] > 0"}],
                }],
            },
        }
        agg = parse_semantic_model_tmsl(bim, [])
        self.assertEqual(agg["tables"][0].measures[0].expression, "SUM(\nSales[Amt]\n)")
        self.assertEqual(agg["relationships"][0].cross_filter, "both")
        self.assertFalse(agg["relationships"][0].is_active)
        self.assertEqual(agg["roles"][0].table_permissions[0].filter_expression, "Sales[Amt] > 0")


class TmdlFenceTest(unittest.TestCase):
    def test_strips_triple_backtick_fences(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [], "model_name": None}
        text = "table T\n\tmeasure M = ```\n\t\tRANKX ( ALL ( x ), [y] )\n\t\t```\n"
        parse_tmdl_text(text, agg, [])
        expr = agg["tables"][0].measures[0].expression
        self.assertNotIn("```", expr)
        self.assertIn("RANKX", expr)


class CalcGroupAndHierarchyTest(unittest.TestCase):
    """Track B1: calculation-group items and user-defined hierarchies were
    previously dropped (calc groups only set the host table's ``kind``;
    hierarchies weren't parsed at all). Both now flow through TMDL and TMSL."""

    def test_tmdl_calculation_group_items(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        text = (
            "table 'Time Intelligence'\n"
            "\tcalculationGroup\n"
            "\t\tprecedence: 10\n\n"
            "\t\tcalculationItem Current = SELECTEDMEASURE()\n\n"
            "\t\tcalculationItem YTD = ```\n"
            "\t\t\t\tCALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))\n"
            "\t\t\t\t```\n"
            "\t\t\tformatStringDefinition = \"#,##0\"\n"
            "\t\t\tordinal: 1\n"
        )
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [], "model_name": None}
        parse_tmdl_text(text, agg, [])
        t = agg["tables"][0]
        self.assertEqual(t.kind, "calculation-group")
        self.assertEqual(t.calculation_group_precedence, 10)
        self.assertEqual([ci.name for ci in t.calculation_items], ["Current", "YTD"])
        ytd = t.calculation_items[1]
        self.assertIn("DATESYTD", ytd.expression)
        self.assertNotIn("```", ytd.expression)
        self.assertEqual(ytd.ordinal, 1)
        self.assertEqual(ytd.format_string_expression, '"#,##0"')

    def test_tmdl_hierarchy_levels(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        text = (
            "table 'Date'\n"
            "\tcolumn Year\n\t\tdataType: int64\n"
            "\thierarchy 'Calendar'\n"
            "\t\tlevel Year\n\t\t\tcolumn: Year\n"
            "\t\tlevel Quarter\n\t\t\tcolumn: Quarter\n"
        )
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [], "model_name": None}
        parse_tmdl_text(text, agg, [])
        h = agg["tables"][0].hierarchies[0]
        self.assertEqual(h.name, "Calendar")
        self.assertEqual([(lv.name, lv.column, lv.ordinal) for lv in h.levels],
                         [("Year", "Year", 0), ("Quarter", "Quarter", 1)])

    def test_tmsl_calc_group_and_hierarchy(self):
        bim = {"model": {"tables": [
            {"name": "TI", "calculationGroup": {"precedence": 5, "calculationItems": [
                {"name": "MTD", "expression": ["TOTALMTD(", "SELECTEDMEASURE())"], "ordinal": 0,
                 "formatStringDefinition": {"expression": "\"0.0%\""}},
            ]}},
            {"name": "Date", "hierarchies": [
                {"name": "Cal", "levels": [
                    {"name": "Q", "column": "Quarter", "ordinal": 1},
                    {"name": "Y", "column": "Year", "ordinal": 0},
                ]},
            ]},
        ]}}
        agg = parse_semantic_model_tmsl(bim, [])
        ti = agg["tables"][0]
        self.assertEqual(ti.kind, "calculation-group")
        self.assertEqual(ti.calculation_group_precedence, 5)
        self.assertEqual(ti.calculation_items[0].expression, "TOTALMTD(\nSELECTEDMEASURE())")
        self.assertEqual(ti.calculation_items[0].format_string_expression, '"0.0%"')
        # levels arrive out of order in TMSL; ordinal is authoritative
        self.assertEqual([lv.name for lv in agg["tables"][1].hierarchies[0].levels], ["Y", "Q"])

    def test_counts_include_calc_items_and_hierarchies(self):
        bim = {"model": {"tables": [
            {"name": "TI", "calculationGroup": {"calculationItems": [
                {"name": "A", "expression": "SELECTEDMEASURE()"},
                {"name": "B", "expression": "SELECTEDMEASURE()"}]}},
            {"name": "Date", "hierarchies": [{"name": "Cal", "levels": [{"name": "Y", "column": "Year"}]}]},
        ]}}
        agg = parse_semantic_model_tmsl(bim, [])
        model = SemanticModel(report_name="x", tables=agg["tables"])
        model.compute_counts()
        self.assertEqual(model.meta.counts["calculation_items"], 2)
        self.assertEqual(model.meta.counts["hierarchies"], 1)

    def test_round_trip_preserves_calc_items_and_hierarchies(self):
        from pbicompass.schemas.model import (
            Table, CalculationItem, Hierarchy, HierarchyLevel,
        )
        model = SemanticModel(report_name="x", tables=[
            Table(name="TI", kind="calculation-group", calculation_group_precedence=3,
                  calculation_items=[CalculationItem(name="A", expression="SELECTEDMEASURE()",
                                                     ordinal=0, format_string_expression='"0"')]),
            Table(name="Date", hierarchies=[Hierarchy(name="Cal", levels=[
                HierarchyLevel(name="Y", column="Year", ordinal=0)])]),
        ])
        reloaded = SemanticModel.from_json(model.to_json())
        self.assertEqual(reloaded.to_dict(), model.to_dict())


class KpiAndRefreshPolicyTest(unittest.TestCase):
    """Track B3/B4: measure KPIs (target/status/trend) and table incremental-
    refresh policies now parse through TMDL and TMSL."""

    def test_tmdl_measure_kpi(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        text = (
            "table Sales\n"
            "\tmeasure 'Sales KPI' = [Total]\n"
            "\t\tkpi\n"
            "\t\t\ttargetExpression = [Target]\n"
            "\t\t\tstatusGraphic: \"Traffic Light - Single\"\n"
            "\t\t\tstatusExpression = ```\n"
            "\t\t\t\t\tDIVIDE([Total], [Target])\n"
            "\t\t\t\t\t```\n"
            "\tmeasure Plain = SUM(Sales[Amt])\n"
        )
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [], "model_name": None}
        parse_tmdl_text(text, agg, [])
        measures = {m.name: m for m in agg["tables"][0].measures}
        self.assertIsNone(measures["Plain"].kpi)
        kpi = measures["Sales KPI"].kpi
        self.assertIsNotNone(kpi)
        self.assertEqual(kpi.target_expression, "[Target]")
        self.assertEqual(kpi.status_graphic, "Traffic Light - Single")
        self.assertIn("DIVIDE", kpi.status_expression)
        self.assertNotIn("```", kpi.status_expression)

    def test_tmdl_refresh_policy(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        text = (
            "table Sales\n"
            "\trefreshPolicy: basic\n"
            "\t\tmode: import\n"
            "\t\trollingWindowGranularity: month\n"
            "\t\trollingWindowPeriods: 3\n"
            "\t\tincrementalGranularity: day\n"
            "\t\tincrementalPeriods: 10\n"
            "\t\tsourceExpression = let Source = 1 in Source\n"
        )
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [], "model_name": None}
        parse_tmdl_text(text, agg, [])
        rp = agg["tables"][0].refresh_policy
        self.assertEqual(rp.policy_type, "basic")
        self.assertEqual((rp.rolling_window_periods, rp.rolling_window_granularity), (3, "month"))
        self.assertEqual((rp.incremental_periods, rp.incremental_granularity), (10, "day"))
        self.assertIn("Source", rp.source_expression)

    def test_tmsl_kpi_and_refresh_policy(self):
        bim = {"model": {"tables": [{
            "name": "Sales",
            "measures": [{"name": "K", "expression": "[Total]", "kpi": {
                "targetExpression": "[Target]", "statusGraphic": "Shapes",
                "statusExpression": ["DIVIDE(", "[Total],[Target])"]}}],
            "refreshPolicy": {"policyType": "basic", "rollingWindowPeriods": 2,
                              "rollingWindowGranularity": "year", "incrementalPeriods": 5,
                              "incrementalGranularity": "month"},
        }]}}
        agg = parse_semantic_model_tmsl(bim, [])
        t = agg["tables"][0]
        self.assertEqual(t.measures[0].kpi.target_expression, "[Target]")
        self.assertEqual(t.measures[0].kpi.status_expression, "DIVIDE(\n[Total],[Target])")
        self.assertEqual(t.refresh_policy.rolling_window_periods, 2)
        self.assertEqual(t.refresh_policy.incremental_granularity, "month")

    def test_round_trip_preserves_kpi_and_refresh_policy(self):
        from pbicompass.schemas.model import Table, Measure, MeasureKPI, RefreshPolicy
        model = SemanticModel(report_name="x", tables=[
            Table(name="Sales",
                  measures=[Measure(name="K", expression="[T]",
                                    kpi=MeasureKPI(target_expression="[Tgt]", status_graphic="TL"))],
                  refresh_policy=RefreshPolicy(policy_type="basic", rolling_window_periods=3,
                                               rolling_window_granularity="month")),
        ])
        self.assertEqual(SemanticModel.from_json(model.to_json()).to_dict(), model.to_dict())


class FieldParamPerspectiveCultureTest(unittest.TestCase):
    """Track B5/B6: field parameters (first-class), perspectives, translation
    cultures, and measure dynamic format strings."""

    def test_tmdl_perspective_culture_and_dynamic_format(self):
        from pbicompass.parsers.tmdl import parse_tmdl_text
        text = "\n".join([
            "perspective 'Exec View'",
            "\tperspectiveTable Sales",
            "\t\tperspectiveMeasure 'Total Sales'",
            "\tperspectiveTable Date",
            "culture fr-FR",
            "\ttranslations",
            "\t\ttranslatedCaption: Ventes",
            "\t\ttranslatedCaption: Date",
            "table Sales",
            "\tmeasure 'Total Sales' = SUM(Sales[Amt])",
            "\t\tformatStringDefinition = ```",
            "\t\t\t\tIF([Total Sales] > 1000, \"#,0,K\", \"#,0\")",
            "\t\t\t\t```",
        ])
        agg = {"tables": [], "relationships": [], "roles": [], "expressions": [],
               "perspectives": [], "cultures": [], "model_name": None}
        parse_tmdl_text(text, agg, [])
        self.assertEqual(agg["perspectives"][0].tables, ["Sales", "Date"])
        self.assertEqual(agg["perspectives"][0].measures, ["Total Sales"])
        self.assertEqual(agg["cultures"][0].name, "fr-FR")
        self.assertEqual(agg["cultures"][0].translated_object_count, 2)
        fmt = agg["tables"][0].measures[0].format_string_expression
        self.assertIn("IF(", fmt)
        self.assertNotIn("```", fmt)

    def test_tmsl_perspective_culture_and_dynamic_format(self):
        bim = {"model": {
            "tables": [{"name": "Sales", "measures": [{"name": "T", "expression": "1",
                        "formatStringDefinition": {"expression": "\"#,0\""}}]}],
            "perspectives": [{"name": "P", "tables": [
                {"name": "Sales", "measures": [{"name": "T"}]}, {"name": "Date"}]}],
            "cultures": [{"name": "es-ES", "translations": {"model": {"tables": [
                {"name": "Sales", "translatedCaption": "Ventas",
                 "columns": [{"name": "Amt", "translatedCaption": "Monto"}]}]}}}],
        }}
        agg = parse_semantic_model_tmsl(bim, [])
        self.assertEqual(agg["tables"][0].measures[0].format_string_expression, '"#,0"')
        self.assertEqual(agg["perspectives"][0].tables, ["Sales", "Date"])
        self.assertEqual(agg["cultures"][0].translated_object_count, 2)

    def test_field_parameter_extraction(self):
        from pbicompass.schemas.model import Table, Column, Partition, Relationship
        from pbicompass.agents.report_facts import extract_field_parameters
        fp_dax = ('{("Sales Amount", NAMEOF(\'Sales\'[Amt]), 0), '
                  '("Quantity", NAMEOF(\'Sales\'[Qty]), 1)}')
        model = SemanticModel(report_name="R", tables=[
            Table(name="Sales", columns=[Column(name="Amt")]),
            Table(name="Date", columns=[Column(name="D")]),
            Table(name="Field Parameter", is_calculated=True,
                  partitions=[Partition(name="p", source_kind="calculated", expression=fp_dax)]),
        ], relationships=[Relationship(from_table="Sales", from_column="D",
                                       to_table="Date", to_column="D")])
        fps = extract_field_parameters(model)
        self.assertEqual(len(fps), 1)
        self.assertEqual(fps[0].fields, ["Sales[Amt]", "Sales[Qty]"])
        self.assertEqual(fps[0].display_names, ["Sales Amount", "Quantity"])

    def test_round_trip_preserves_new_features(self):
        from pbicompass.schemas.model import (
            Table, Measure, FieldParameter, Perspective, Culture,
        )
        model = SemanticModel(report_name="x",
            tables=[Table(name="Sales", measures=[
                Measure(name="T", format_string_expression='IF(1,"a","b")')])],
            field_parameters=[FieldParameter(table="FP", fields=["Sales[Amt]"],
                                             display_names=["Amount"])],
            perspectives=[Perspective(name="P", tables=["Sales"], measures=["T"])],
            cultures=[Culture(name="fr-FR", translated_object_count=3)])
        self.assertEqual(SemanticModel.from_json(model.to_json()).to_dict(), model.to_dict())


class SemanticModelRoundTripTest(unittest.TestCase):
    """``SemanticModel.from_dict``/``from_json`` (Day 7): lets an
    already-parsed ``model.json`` be reloaded directly as a fixture,
    without re-parsing the original .pbip/.pbix — the basis for the
    Corporate Spend regression fixture, whose real source project is not
    in this repo, only its previously-generated ``model.json``."""

    def test_round_trip_is_stable_for_a_parsed_fixture(self):
        model = detect_and_parse(FIXTURE)
        reloaded = SemanticModel.from_dict(model.to_dict())
        self.assertEqual(reloaded.to_dict(), model.to_dict())

    def test_from_json_round_trips_through_the_wire_format(self):
        model = detect_and_parse(FIXTURE)
        reloaded = SemanticModel.from_json(model.to_json())
        self.assertEqual(reloaded.report_name, model.report_name)
        self.assertEqual(len(reloaded.tables), len(model.tables))
        self.assertEqual(reloaded.to_dict(), model.to_dict())

    def test_corporate_spend_real_fixture_loads(self):
        fixture_path = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"
        model = SemanticModel.from_json(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(model.report_name, "Corporate Spend")
        self.assertEqual(len(model.tables), 11)
        # A round trip of the real fixture must also be stable, not just
        # a freshly-parsed one — this is the shape every downstream test
        # actually exercises.
        self.assertEqual(SemanticModel.from_dict(model.to_dict()).to_dict(), model.to_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
