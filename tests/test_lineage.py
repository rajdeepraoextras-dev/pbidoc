"""Tests for ``pbicompass.render._lineage`` — the data lineage graph SVG.

No coverage existed before Day 13's v4 redesign (flagged as a known gap in
the Day 12 addendum) — added alongside the redesign itself, since
``_lineage.py`` is still fully dormant in HTML output (``html.py``'s
lineage-section append remains commented out; Day 14 scope) but should not
land its own real code with zero test coverage.
"""

from __future__ import annotations

import unittest

from pbicompass.render._lineage import build_lineage_data
from pbicompass.schemas.model import DataSource, Measure, Page, Partition, SemanticModel, Table, Visual


def _model() -> SemanticModel:
    fact = Table(
        name="FactSales",
        partitions=[Partition(name="p", mode="import",
                              expression='Source = Sql.Database("sql-prod-01","SalesDW")')],
        measures=[Measure(name="Total Revenue", table="FactSales", expression="SUM(FactSales[Amount])")],
    )
    dim = Table(name="DimDate", partitions=[Partition(name="p", mode="import", expression="x")])
    return SemanticModel(
        report_name="Demo",
        data_sources=[DataSource(type="Sql.Database", server="sql-prod-01", database="SalesDW")],
        tables=[fact, dim],
        pages=[Page(id="p1", display_name="Executive Summary", visuals=[
            Visual(id="v1", type="card", title="Total Revenue", fields=["FactSales.Total Revenue"],
                  x=0, y=0, z=0, width=100, height=100),
        ])],
    )


class RealNodeTextTest(unittest.TestCase):
    """The identical "WIP" placeholder bug that was live in the wireframe
    (Day 12) was also present here (dormant, since this SVG's own append
    site is commented out) — fixed at the same time."""

    def test_no_wip_placeholder_and_real_node_names_render(self):
        edges, svg = build_lineage_data(_model())
        self.assertNotIn("WIP", svg)
        self.assertIn("FactSales", svg)
        self.assertIn("Total Revenue", svg)
        self.assertIn("Executive Summary", svg)
        self.assertTrue(edges)


class DesignSystemTest(unittest.TestCase):
    """Same design DNA as the page wireframe (v6 "Studio", 2026-07-11 —
    shared ``_diagram_theme``): lineage's four *layers*
    (source/table/measure/page) carry the same four accent colors the
    wireframe uses for its four *categories*, on the same card language."""

    def test_all_four_layers_present_with_their_accent(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn('cat-source', svg)
        self.assertIn('cat-table', svg)
        self.assertIn('cat-measure', svg)
        self.assertIn('cat-page', svg)
        self.assertIn('fill="#8b5cf6"', svg)  # source (purple, same as wireframe's decorative)
        self.assertIn('fill="#4f6ef7"', svg)  # table (blue, same as wireframe's data)
        self.assertIn('fill="#f59e0b"', svg)  # measure (amber, same as wireframe's slicer)
        self.assertIn('fill="#10b981"', svg)  # page (green, same as wireframe's nav)

    def test_card_structure_matches_the_wireframe_convention(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn('class="wf-node cat-', svg)
        self.assertIn('class="wf-card-bg"', svg)

    def test_icon_defs_present_per_layer(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn("wf-i-lin-source", svg)
        self.assertIn("wf-i-lin-table", svg)
        self.assertIn("wf-i-lin-measure", svg)
        self.assertIn("wf-i-lin-page", svg)

    def test_legend_chips_present_for_all_four_layers(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn('class="legend legend--upper wf-legend"', svg)
        self.assertIn("wf-chip-dot--source", svg)
        self.assertIn("wf-chip-dot--table", svg)
        self.assertIn("wf-chip-dot--measure", svg)
        self.assertIn("wf-chip-dot--page", svg)

    def test_no_inline_style_or_event_handlers(self):
        edges, svg = build_lineage_data(_model())
        self.assertNotIn("style=", svg)
        self.assertNotIn("onmouseover", svg)
        self.assertNotIn("onmouseout", svg)

    def test_edges_paint_before_node_cards(self):
        # Pass 2 (edges) must precede pass 3 (cards) in document order, so
        # the connecting curves render *underneath* the cards, not on top.
        edges, svg = build_lineage_data(_model())
        first_edge = svg.find("<path d=")
        first_card = svg.find('class="wf-node cat-')
        self.assertNotEqual(first_edge, -1)
        self.assertNotEqual(first_card, -1)
        self.assertLess(first_edge, first_card)


class InteractiveNodesTest(unittest.TestCase):
    """v6: every node is a deep link into the document (the user's ask:
    "if I click on a measure or anything it takes me to that section"), and
    nodes/edges carry the data attributes the shell's hover-connect script
    keys on."""

    def test_every_layer_links_to_its_section_anchor(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn('href="#source-sql-database-sql-prod-01-salesdw"', svg)
        self.assertIn('href="#table-factsales"', svg)
        self.assertIn('href="#measure-total-revenue"', svg)
        self.assertIn('href="#page-executive-summary"', svg)

    def test_nodes_and_edges_carry_hover_connect_attributes(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn('data-node="t-factsales"', svg)
        self.assertIn('data-node="m-total-revenue"', svg)
        self.assertIn('class="lg-edge" data-from="t-factsales" data-to="m-total-revenue"', svg)

    def test_edge_gradients_use_user_space_units(self):
        # objectBoundingBox gradients collapse on a perfectly horizontal
        # path (zero-height bbox) and the edge vanishes — every edge
        # gradient must pin its own endpoints in user space.
        edges, svg = build_lineage_data(_model())
        self.assertIn('gradientUnits="userSpaceOnUse"', svg)
        self.assertNotIn('stroke="#cdd4e2"', svg)  # no flat-gray v5 edges left

    def test_cards_carry_informative_sublabels(self):
        edges, svg = build_lineage_data(_model())
        self.assertIn(">in FactSales</text>", svg)   # measure: its home table
        self.assertIn(">1 visual</text>", svg)        # page: visual count
        self.assertIn(">SQL Server</text>", svg)      # source: friendly kind
        self.assertIn(">SalesDW</text>", svg)         # source title: db name, not the raw label


class OverflowNodeTest(unittest.TestCase):
    """8+ nodes in a layer collapse into one "+N more ..." card — a dashed
    ghost card (no accent bar or icon) that links to the layer's own
    document section instead of a single object."""

    def test_overflow_node_is_a_ghost_card_linking_to_its_section(self):
        # Partition expressions must actually match a data source (server
        # substring) for a Source-to-Table edge to form at all — a bare
        # disconnected table never enters the graph.
        tables = [
            Table(name=f"Dim{i}", partitions=[Partition(
                name="p", mode="import", expression='Source = Sql.Database("sql-prod-01","SalesDW")',
            )])
            for i in range(10)
        ]
        model = SemanticModel(
            report_name="R",
            data_sources=[DataSource(type="Sql.Database", server="sql-prod-01", database="SalesDW")],
            tables=tables,
        )
        edges, svg = build_lineage_data(model)
        self.assertIn("more tables", svg)
        # ghost treatment + a link to §6 (Data Model), not a bogus object row
        self.assertIn('stroke-dasharray="4 3"', svg)
        self.assertIn('href="#sec6"', svg)
        self.assertIn("view all in §6", svg)


class EmptyModelTest(unittest.TestCase):
    def test_empty_model_renders_a_diagram_with_no_edges(self):
        edges, svg = build_lineage_data(SemanticModel(report_name="Empty"))
        self.assertEqual(edges, [])
        self.assertIn('<div class="diagram">', svg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
