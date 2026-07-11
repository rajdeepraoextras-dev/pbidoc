"""Tests for ``pbicompass.agents.report_facts`` — pure functions turning a
``SemanticModel`` into structured page/visual/slicer facts shared by every
document generator.
"""

from __future__ import annotations

import unittest

from pbicompass.agents.report_facts import (
    FIELD_SELECTOR_LABEL,
    business_plain_english,
    data_source_type_counts,
    declassify,
    field_parameter_table_names,
    first_sentence,
    is_field_selector,
    local_path_sources,
    report_pages,
    simplify_dax_prose,
    slicers,
)
from pbicompass.render._shared import anchor_slug
from pbicompass.schemas.model import Column, DataSource, Measure, Page, SemanticModel, Table, Visual


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


class WireframeHrefResolutionTest(unittest.TestCase):
    """Day 13 / I3: report_pages() is the single place that groups 2+
    identical visuals into one "Label — Type ×N" row and resolves any
    remaining anchor-slug collision between different rows (dedupe_ids).
    The wireframe SVG it also produces must link into *that* resolved
    anchor — before this fix, ``render_wireframe`` recomputed its own raw,
    unresolved slug independently, so any page with duplicate or
    slug-colliding visuals (a common real shape — repeated KPI cards) got a
    dead wireframe link the moment 2+ visuals actually collapsed into one
    row."""

    def test_grouped_duplicate_visuals_href_resolves_to_the_relabeled_row(self):
        # Same 5-identical-cards shape as _model_with_duplicate_visuals(),
        # with layout coordinates added so a wireframe SVG actually renders.
        table = Table(name="Sales", measures=[Measure(name="Sale_Value", expression="SUM(Sales[Amount])", table="Sales")])
        page = Page(
            id="p1", display_name="Overview",
            visuals=[Visual(id=f"v{i}", type="card", fields=["Sales.Sale_Value"],
                            x=i * 100, y=0, z=0, width=90, height=70) for i in range(5)],
        )
        pages = report_pages(SemanticModel(report_name="R", tables=[table], pages=[page]))

        visuals = pages[0]["visuals"]
        self.assertEqual(visuals[0]["count"], 5)
        self.assertIn("×5", visuals[0]["label"])  # confirms grouping actually happened

        svg = pages[0]["wireframe_svg"]
        self.assertIsNotNone(svg)
        resolved_slug = anchor_slug(visuals[0]["label"])  # the "...×5" row's real id
        raw_slug = anchor_slug("Sale_Value")               # the old, pre-grouping target
        self.assertIn(f'href="#visual-overview-{resolved_slug}"', svg)
        self.assertNotIn(f'href="#visual-overview-{raw_slug}"', svg)
        # All 5 tiles share one group, so all 5 tiles' <a> should point at
        # the same single resolved anchor — never a per-instance guess.
        self.assertEqual(svg.count(f'href="#visual-overview-{resolved_slug}"'), 5)


class SlicersDedupeTest(unittest.TestCase):
    """1.7: two slicer visuals bound to the same field on the same page
    collapse into one row, noting the multiplicity via ``count``."""

    def test_same_field_same_page_collapses(self):
        rows = slicers(_model_with_duplicate_slicers())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["field"], "Sales.Type")


def _model_with_field_parameter() -> SemanticModel:
    """A disconnected, calculated 'select' table driving a chart's axis —
    the exact I4 shape: field parameters are calculated tables, never
    joined via a relationship, typically with a handful of columns."""
    sales = Table(name="Sales", kind="fact", measures=[
        Measure(name="Actual", expression="SUM(Sales[Amount])", table="Sales"),
    ])
    selector = Table(name="select", is_calculated=True, columns=[
        Column(name="select"), Column(name="select Order"), Column(name="select Fields"),
    ])
    page = Page(
        id="p1", display_name="Overview",
        visuals=[Visual(id="v1", type="columnChart", fields=["Sales.Actual", "select.select"])],
    )
    return SemanticModel(report_name="R", tables=[sales, selector], pages=[page])


class FieldParameterRecognitionTest(unittest.TestCase):
    """I4: field parameters / disconnected helper tables must not leak into
    generated dimensions, business questions, or the glossary as if they
    were real report data."""

    def test_field_parameter_table_is_recognized(self):
        names = field_parameter_table_names(_model_with_field_parameter())
        self.assertIn("select", names)

    def test_related_calculated_table_is_not_flagged(self):
        # A calculated table that *is* joined to the model is real content,
        # not a field parameter — e.g. a calculated date table.
        date_tbl = Table(name="Calendar", is_calculated=True,
                         columns=[Column(name="Date"), Column(name="Year"), Column(name="Month")])
        sales = Table(name="Sales", kind="fact", columns=[Column(name="Date")])
        from pbicompass.schemas.model import Relationship
        model = SemanticModel(
            report_name="R", tables=[sales, date_tbl],
            relationships=[Relationship(from_table="Sales", from_column="Date",
                                        to_table="Calendar", to_column="Date")],
        )
        self.assertEqual(field_parameter_table_names(model), set())

    def test_field_parameter_excluded_from_report_pages_dimensions(self):
        pages = report_pages(_model_with_field_parameter())
        visuals = pages[0]["visuals"]
        all_dims = [d for v in visuals for d in v["dimensions"]]
        self.assertNotIn("select", all_dims)
        self.assertIn("Actual", [m for v in visuals for m in v["metrics"]])

    def test_field_parameter_excluded_from_business_questions(self):
        from pbicompass.agents.deterministic import business_analyst_deterministic

        summary = business_analyst_deterministic(_model_with_field_parameter())
        questions = summary.pages[0].business_questions
        self.assertFalse(any("select" in q for q in questions),
                         f"field parameter leaked into a question: {questions}")

    def test_field_parameter_excluded_from_glossary(self):
        # P2: a field parameter/system selector is UI mechanics, not
        # business vocabulary — it has no place in a *business* glossary at
        # all (previously labeled "A field selector that switches what the
        # chart displays.", which also left it exposed to a critic/
        # grounding pass overwriting that fixed, correct text with a
        # leaked editing instruction — see also the "Explain" directive
        # fixed in sanitize.py).
        from pbicompass.agents.generators.user_guide import BusinessGuideGenerator

        doc = BusinessGuideGenerator.generate(_model_with_field_parameter())
        self.assertIsNone(next((g for g in doc.glossary if g.term == "select"), None))


def _model_with_bare_field_parameter() -> SemanticModel:
    """D4 regression: Power BI's report layout sometimes emits a field-
    parameter projection as a bare, unqualified ``queryRef`` — just the
    parameter table's own name ("select"/"select1"), with no
    ``Entity.Property`` qualification at all (see
    ``parsers/pbir.py::_extract_fields``'s ``queryRef`` fallback).
    Reproduces the exact production shape: the parameter table doesn't even
    appear in ``model.tables`` (dropped somewhere upstream of the report
    layout), so there is no dot to strip a table name from and no table
    object for ``field_parameter_table_names()`` to have recognized in the
    first place — only the bare token's own name gives it away."""
    sales = Table(name="Sales", kind="fact", measures=[
        Measure(name="Actual", expression="SUM(Sales[Amount])", table="Sales"),
    ])
    page = Page(
        id="p1", display_name="Overview",
        visuals=[
            Visual(id="v1", type="lineStackedColumnComboChart",
                   fields=["select", "select1", "Sales.Actual"]),
            Visual(id="s1", type="slicer", is_slicer=True, fields=["select1"]),
        ],
    )
    return SemanticModel(report_name="R", tables=[sales], pages=[page])


class BareFieldSelectorRegressionTest(unittest.TestCase):
    """D4: a field-parameter reference shaped as a bare, unqualified token
    (no table object in ``model.tables``, no ``.`` to split) must be
    recognized just like the qualified ``Table.Column`` shape — everywhere
    ``report_facts``'s I4 filtering already applied to the qualified case."""

    def test_is_field_selector_recognizes_bare_telltale_names(self):
        self.assertTrue(is_field_selector("select", set()))
        self.assertTrue(is_field_selector("select1", set()))
        self.assertTrue(is_field_selector("Range", set()))
        self.assertTrue(is_field_selector("Field Parameter", set()))
        self.assertFalse(is_field_selector("Business Area", set()))
        self.assertFalse(is_field_selector("Sales.Actual", set()))

    def test_bare_field_parameter_excluded_from_visual_title_and_dimensions(self):
        pages = report_pages(_model_with_bare_field_parameter())
        visuals = pages[0]["visuals"]
        self.assertEqual(len(visuals), 1)  # the slicer visual is never a "visual" row (I3)
        combo = visuals[0]
        self.assertNotIn("select", combo["dimensions"])
        self.assertNotIn("select1", combo["dimensions"])
        self.assertEqual(combo["label"], "Actual")

    def test_bare_field_parameter_excluded_from_business_questions(self):
        from pbicompass.agents.deterministic import business_analyst_deterministic

        summary = business_analyst_deterministic(_model_with_bare_field_parameter())
        questions = summary.pages[0].business_questions
        self.assertFalse(any("select" in q for q in questions),
                         f"bare field parameter leaked into a question: {questions}")

    def test_bare_field_parameter_excluded_from_page_theme(self):
        from pbicompass.agents.deterministic import business_analyst_deterministic

        summary = business_analyst_deterministic(_model_with_bare_field_parameter())
        self.assertNotIn("select", summary.pages[0].summary)

    def test_bare_field_parameter_slicer_relabeled(self):
        rows = slicers(_model_with_bare_field_parameter())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field"], FIELD_SELECTOR_LABEL)

    def test_bare_field_parameter_nav_guide_relabeled(self):
        from pbicompass.agents.deterministic import business_analyst_deterministic

        summary = business_analyst_deterministic(_model_with_bare_field_parameter())
        nav_text = " ".join(summary.navigation_guide)
        self.assertNotIn("select1", nav_text)
        self.assertIn(FIELD_SELECTOR_LABEL, nav_text)

    def test_bare_field_parameter_excluded_from_technical_glossary(self):
        from pbicompass.agents.generators.technical import TechnicalDocumentationGenerator

        doc = TechnicalDocumentationGenerator.generate(_model_with_bare_field_parameter())
        terms = {e["term"] for e in doc.glossary_entries}
        self.assertNotIn("select", terms)
        self.assertNotIn("select1", terms)

    def test_bare_field_parameter_excluded_from_user_guide_glossary(self):
        # P2: now consistent with the technical doc's glossary (above) —
        # a field parameter is UI mechanics, not business vocabulary, so
        # it's excluded from the *business* glossary entirely rather than
        # merely relabeled.
        from pbicompass.agents.generators.user_guide import BusinessGuideGenerator

        doc = BusinessGuideGenerator.generate(_model_with_bare_field_parameter())
        terms = {g.term for g in doc.glossary}
        self.assertNotIn("select", terms)
        self.assertNotIn("select1", terms)


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


class DataSourceTypeCountsTest(unittest.TestCase):
    """Day 5: G.1's own worked example — "1 Excel workbook — Data.xlsx", not
    the raw connector name "1 File.Contents(s)"."""

    def test_file_contents_resolves_by_extension_and_shows_filename(self):
        model = SemanticModel(
            report_name="R",
            data_sources=[DataSource(type="File.Contents", detail=r"C:\Users\me\Data.xlsx")],
        )
        lines = data_source_type_counts(model)
        self.assertEqual(lines, ["1 Excel workbook — Data.xlsx"])

    def test_never_shows_the_raw_connector_name(self):
        model = SemanticModel(
            report_name="R",
            data_sources=[DataSource(type="File.Contents", detail=r"C:\data\report.unknownext")],
        )
        lines = data_source_type_counts(model)
        self.assertNotIn("File.Contents", lines[0])

    def test_never_shows_a_directory_path(self):
        model = SemanticModel(
            report_name="R",
            data_sources=[DataSource(type="Excel.Workbook", detail=r"C:\Users\me\Desktop\Data.xlsx")],
        )
        lines = data_source_type_counts(model)
        self.assertNotIn("Users", lines[0])
        self.assertNotIn("\\", lines[0])

    def test_pluralizes_correctly_at_count_one_and_many(self):
        one = SemanticModel(report_name="R", data_sources=[DataSource(type="Sql.Database", server="s", database="d")])
        many = SemanticModel(
            report_name="R",
            data_sources=[
                DataSource(type="Sql.Database", server="s1", database="d1"),
                DataSource(type="Sql.Database", server="s2", database="d2"),
            ],
        )
        self.assertEqual(data_source_type_counts(one), ["1 SQL database"])
        self.assertEqual(data_source_type_counts(many), ["2 SQL databases"])

    def test_multiple_sources_of_one_file_like_type_omit_filenames(self):
        model = SemanticModel(
            report_name="R",
            data_sources=[
                DataSource(type="Excel.Workbook", detail=r"C:\a\One.xlsx"),
                DataSource(type="Excel.Workbook", detail=r"C:\b\Two.xlsx"),
            ],
        )
        lines = data_source_type_counts(model)
        self.assertEqual(lines, ["2 Excel workbooks"])


class FirstSentenceTest(unittest.TestCase):
    def test_returns_only_the_first_sentence(self):
        self.assertEqual(first_sentence("First one. Second one."), "First one.")

    def test_returns_whole_text_when_no_terminator(self):
        self.assertEqual(first_sentence("No terminator here"), "No terminator here")

    def test_empty_input(self):
        self.assertEqual(first_sentence(""), "")
        self.assertEqual(first_sentence(None), "")


class SimplifyDaxProseTest(unittest.TestCase):
    """P3: a business-facing fallback must never leak raw DAX aggregation
    syntax, even nested inside another function's argument."""

    def test_distinctcount_becomes_plain_english(self):
        self.assertEqual(
            simplify_dax_prose("DISTINCTCOUNT ( Sales[SalesKey] )"),
            "the number of unique Sales[SalesKey] values",
        )

    def test_nested_inside_divide_is_also_simplified(self):
        text = "A ratio: Total Revenue divided by DISTINCTCOUNT ( Sales[SalesKey] )."
        simplified = simplify_dax_prose(text)
        self.assertNotIn("DISTINCTCOUNT", simplified)
        self.assertIn("the number of unique Sales[SalesKey] values", simplified)

    def test_countrows_and_sum_are_simplified(self):
        self.assertIn("the number of Sales rows", simplify_dax_prose("COUNTROWS ( Sales )"))
        self.assertIn("the total Sales[Amount]", simplify_dax_prose("SUM ( Sales[Amount] )"))

    def test_text_without_dax_calls_is_unchanged(self):
        self.assertEqual(simplify_dax_prose("A plain sentence."), "A plain sentence.")


class BusinessPlainEnglishTest(unittest.TestCase):
    def test_never_leaks_raw_function_syntax_or_brackets(self):
        # Regression (P3): "Avg Order Value" style measures used to render as
        # "A ratio: Total Revenue divided by DISTINCTCOUNT ( Sales[SalesKey] )."
        # in business-facing docs — should now read in plain English.
        text = business_plain_english(
            "Avg Order Value", "DIVIDE ( [Total Revenue], DISTINCTCOUNT ( Sales[SalesKey] ) )", None,
        )
        self.assertNotIn("DISTINCTCOUNT", text)
        self.assertNotIn("[", text)
        self.assertIn("number of unique", text)

    def test_declassify_still_strips_bracket_notation(self):
        self.assertEqual(declassify("Sales[Quantity] * Sales[UnitPrice]"), "Quantity * UnitPrice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
