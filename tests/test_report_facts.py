"""Tests for ``pbicompass.agents.report_facts`` — pure functions turning a
``SemanticModel`` into structured page/visual/slicer facts shared by every
document generator.
"""

from __future__ import annotations

import unittest

from pbicompass.agents.report_facts import first_sentence, local_path_sources, report_pages, slicers
from pbicompass.schemas.model import DataSource, Measure, Page, SemanticModel, Table, Visual


def _model_with_duplicate_visuals() -> SemanticModel:
    table = Table(name="Sales", measures=[Measure(name="Sale_Value", expression="SUM(Sales[Amount])", table="Sales")])
    page = Page(
        id="p1", display_name="Overview",
        visuals=[Visual(id=f"v{i}", type="card", fields=["Sales.Sale_Value"]) for i in range(5)],
    )
    return SemanticModel(report_name="R", tables=[table], pages=[page])


def _model_with_duplicate_slicers() -> SemanticModel:
    page = Page(
        id="p1", display_name="Overview",
        visuals=[
            Visual(id="s1", type="slicer", is_slicer=True, fields=["Sales.Type"]),
            Visual(id="s2", type="slicer", is_slicer=True, fields=["Sales.Type"]),
        ],
    )
    return SemanticModel(report_name="R", pages=[page])


class ReportPagesDedupeTest(unittest.TestCase):
    """1.2: identical visuals collapse into one row with a count, instead of
    one near-duplicate row per instance."""

    def test_identical_visuals_collapse_with_count(self):
        pages = report_pages(_model_with_duplicate_visuals())
        visuals = pages[0]["visuals"]
        self.assertEqual(len(visuals), 1)
        self.assertEqual(visuals[0]["count"], 5)
        self.assertIn("×5", visuals[0]["label"])

    def test_distinct_visuals_are_not_merged(self):
        table = Table(name="Sales", measures=[
            Measure(name="A", expression="SUM(Sales[X])", table="Sales"),
            Measure(name="B", expression="SUM(Sales[Y])", table="Sales"),
        ])
        page = Page(id="p1", display_name="Overview", visuals=[
            Visual(id="v1", type="card", fields=["Sales.A"]),
            Visual(id="v2", type="card", fields=["Sales.B"]),
        ])
        pages = report_pages(SemanticModel(report_name="R", tables=[table], pages=[page]))
        self.assertEqual(len(pages[0]["visuals"]), 2)
        self.assertTrue(all(v["count"] == 1 for v in pages[0]["visuals"]))


class SlicersDedupeTest(unittest.TestCase):
    """1.7: two slicer visuals bound to the same field on the same page
    collapse into one row, noting the multiplicity via ``count``."""

    def test_same_field_same_page_collapses(self):
        rows = slicers(_model_with_duplicate_slicers())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["field"], "Sales.Type")


class LocalPathSourcesTest(unittest.TestCase):
    def test_detects_drive_letter_and_user_profile_paths(self):
        model = SemanticModel(
            report_name="R",
            data_sources=[
                DataSource(type="Excel.Workbook", detail=r"C:\Users\faisal\Desktop\orders.xlsx"),
                DataSource(type="Sql.Database", server="prod-sql.contoso.com", database="SalesDW"),
            ],
        )
        paths = local_path_sources(model)
        self.assertEqual(len(paths), 1)
        self.assertIn("orders.xlsx", paths[0])


class FirstSentenceTest(unittest.TestCase):
    def test_returns_only_the_first_sentence(self):
        self.assertEqual(first_sentence("First one. Second one."), "First one.")

    def test_returns_whole_text_when_no_terminator(self):
        self.assertEqual(first_sentence("No terminator here"), "No terminator here")

    def test_empty_input(self):
        self.assertEqual(first_sentence(""), "")
        self.assertEqual(first_sentence(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
