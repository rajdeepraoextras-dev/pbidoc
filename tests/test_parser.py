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


if __name__ == "__main__":
    unittest.main(verbosity=2)
