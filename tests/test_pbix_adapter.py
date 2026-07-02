"""Tests for the .pbix path: the pure pbixray transform + the ZIP fallback.

These run without pbixray installed (the transform is pure; the integration
test exercises the graceful-degradation path when pbixray is absent).
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pbicompass.adapters import build_model_from_frames
from pbicompass.parsers import detect_and_parse
from pbicompass.parsers.pbip import _assemble


def _sample_frames() -> dict:
    return {
        "schema": [
            {"TableName": "Sales", "ColumnName": "Amount", "PandasDataType": "float64"},
            {"TableName": "Sales", "ColumnName": "Qty", "PandasDataType": "int64"},
            {"TableName": "Sales", "ColumnName": "CustKey", "PandasDataType": "int64"},
            {"TableName": "Customer", "ColumnName": "CustKey", "PandasDataType": "int64"},
            {"TableName": "Customer", "ColumnName": "Region", "PandasDataType": "object"},
        ],
        "dax_columns": [
            {"TableName": "Sales", "ColumnName": "Net", "Expression": "Sales[Amount] * 0.9"},
        ],
        "dax_measures": [
            {"TableName": "Sales", "Name": "Total Sales", "Expression": "SUM(Sales[Amount])",
             "DisplayFolder": "KPI", "FormatString": r"\$#,0", "IsHidden": False},
        ],
        "dax_tables": [
            {"TableName": "Calc", "Expression": 'ROW("x", 1)'},
        ],
        "relationships": [
            {"FromTableName": "Sales", "FromColumnName": "CustKey",
             "ToTableName": "Customer", "ToColumnName": "CustKey",
             "CrossFilteringBehavior": 2, "IsActive": False},
        ],
        "power_query": [
            {"TableName": "Sales", "Expression": 'let Source = Sql.Database("srv01", "DW") in Source'},
        ],
        "m_parameters": [
            {"ParameterName": "Env", "Expression": '"prod"'},
        ],
        "metadata": [
            {"Name": "ModelName", "Value": "MyModel"},
        ],
    }


class PbixrayTransformTest(unittest.TestCase):
    def setUp(self):
        self.warnings: list[str] = []
        self.agg = build_model_from_frames(_sample_frames(), self.warnings)

    def _table(self, name):
        return next(t for t in self.agg["tables"] if t.name == name)

    def test_columns_and_dtype_mapping(self):
        sales = self._table("Sales")
        amount = next(c for c in sales.columns if c.name == "Amount")
        self.assertEqual(amount.data_type, "double")
        qty = next(c for c in sales.columns if c.name == "Qty")
        self.assertEqual(qty.data_type, "int64")
        region = next(c for c in self._table("Customer").columns if c.name == "Region")
        self.assertEqual(region.data_type, "string")

    def test_calculated_column_overlay(self):
        net = next(c for c in self._table("Sales").columns if c.name == "Net")
        self.assertTrue(net.is_calculated)
        self.assertEqual(net.expression, "Sales[Amount] * 0.9")

    def test_measure(self):
        m = self._table("Sales").measures[0]
        self.assertEqual(m.name, "Total Sales")
        self.assertEqual(m.table, "Sales")
        self.assertEqual(m.display_folder, "KPI")
        self.assertEqual(m.format_string, r"\$#,0")

    def test_calculated_table(self):
        calc = self._table("Calc")
        self.assertTrue(calc.is_calculated)
        self.assertEqual(calc.kind, "calculation")
        self.assertEqual(calc.partitions[0].source_kind, "calculated")

    def test_m_partition(self):
        part = next(p for p in self._table("Sales").partitions if p.source_kind == "m")
        self.assertIn("Sql.Database", part.expression)

    def test_relationship_mapping(self):
        rel = self.agg["relationships"][0]
        self.assertEqual(rel.cross_filter, "both")
        self.assertFalse(rel.is_active)

    def test_parameter_and_model_name(self):
        self.assertEqual(self.agg["expressions"][0].name, "Env")
        self.assertEqual(self.agg["expressions"][0].kind, "parameter")
        self.assertEqual(self.agg["model_name"], "MyModel")

    def test_roles_unavailable_warning(self):
        self.assertEqual(self.agg["roles"], [])
        self.assertTrue(any("RLS roles are not extracted" in w for w in self.warnings))

    def test_assemble_infers_sources_and_kinds(self):
        model = _assemble(self.agg, [], "MyReport", "pbix", "x.pbix", self.warnings)
        self.assertEqual(len(model.data_sources), 1)
        self.assertEqual(model.data_sources[0].server, "srv01")
        self.assertEqual(model.data_sources[0].database, "DW")
        kinds = {t.name: t.kind for t in model.tables}
        self.assertEqual(kinds["Sales"], "fact")
        self.assertEqual(kinds["Customer"], "dimension")


class PbixZipFallbackTest(unittest.TestCase):
    """Build a .pbix-shaped ZIP with a legacy Report/Layout and parse it.

    Exercises the .pbix ZIP handling, the legacy report parser, and graceful
    degradation when pbixray is not installed.
    """

    def _make_pbix(self, tmpdir: Path) -> Path:
        config = json.dumps({
            "name": "vc1",
            "singleVisual": {
                "visualType": "barChart",
                "prototypeQuery": {
                    "Select": [{
                        "Column": {
                            "Expression": {"SourceRef": {"Entity": "Sales"}},
                            "Property": "Amount",
                        }
                    }]
                },
                "vcObjects": {
                    "title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'My Bar'"}}}}}]
                },
            },
        })
        layout = {"sections": [{
            "name": "s1", "displayName": "Page 1", "ordinal": 0,
            "width": 1280, "height": 720,
            "visualContainers": [{"x": 0, "y": 0, "z": 0, "width": 100, "height": 100, "config": config}],
        }]}
        raw = json.dumps(layout).encode("utf-16-le")
        pbix_path = tmpdir / "Legacy.pbix"
        with zipfile.ZipFile(pbix_path, "w") as zf:
            zf.writestr("Report/Layout", raw)
            zf.writestr("[Content_Types].xml", "<Types/>")
        return pbix_path

    def test_layout_only_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            model = detect_and_parse(self._make_pbix(Path(td)))
        self.assertEqual(model.meta.source_format, "pbix")
        self.assertEqual(len(model.pages), 1)
        page = model.pages[0]
        self.assertEqual(page.display_name, "Page 1")
        self.assertEqual(page.visuals[0].type, "barChart")
        self.assertEqual(page.visuals[0].title, "My Bar")
        self.assertIn("Sales.Amount", page.visuals[0].fields)
        # pbixray is not installed here -> graceful warning, empty model
        self.assertEqual(model.tables, [])
        self.assertTrue(any("pbixray not installed" in w for w in model.meta.warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
