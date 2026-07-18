"""Tests for ``pbicompass.render._wireframe`` — the page-wireframe SVG (3.1),
redesigned per J.C (also fixes I3's dead links).
"""

from __future__ import annotations

import re
import unittest

from pbicompass.render._wireframe import render_wireframe
from pbicompass.schemas.model import Page, Visual


def _page(visuals, *, width=1280, height=720) -> Page:
    return Page(id="p1", display_name="IT Spend Trend", width=width, height=height, visuals=visuals)


class FriendlyTypeNameTest(unittest.TestCase):
    """J.C item 2: never render a camelCase internal visualType name."""

    def test_combo_chart_type_gets_a_friendly_name(self):
        page = _page([Visual(id="v1", type="lineStackedColumnComboChart", title="Var Plan % by Country/Region",
                             x=0, y=0, z=0, width=300, height=200,
                             fields=["Sales.VarPlanPct", "Geo.Country"])])
        svg = render_wireframe(page)
        self.assertIn("Combo chart", svg)
        self.assertNotIn("lineStackedColumnComboChart", svg)

    def test_decomposition_tree_without_visual_suffix_gets_a_friendly_name(self):
        page = _page([Visual(id="v1", type="decompositionTree", title="Drivers",
                             x=0, y=0, z=0, width=300, height=200, fields=["Sales.Amount"])])
        svg = render_wireframe(page)
        self.assertIn("Decomposition tree", svg)
        self.assertNotIn("decompositionTree", svg)

    def test_stacked_area_chart_gets_a_friendly_name(self):
        page = _page([Visual(id="v1", type="stackedAreaChart", title="Trend",
                             x=0, y=0, z=0, width=300, height=200, fields=["Sales.Amount"])])
        svg = render_wireframe(page)
        self.assertIn("Area chart", svg)
        self.assertNotIn("stackedAreaChart", svg)

    def test_no_camelcase_type_name_ever_leaks_for_any_known_type(self):
        from pbicompass.agents.report_facts import FRIENDLY_VISUAL

        visuals = [
            Visual(id=f"v{i}", type=t, title=f"Visual {i}", x=(i % 4) * 100, y=(i // 4) * 100, z=0,
                  width=90, height=70, fields=["Sales.Amount"])
            for i, t in enumerate(FRIENDLY_VISUAL)
        ]
        svg = render_wireframe(_page(visuals, width=1600, height=900))
        camel_case_leak = re.search(r">[a-z][a-zA-Z]*[A-Z][a-zA-Z]*<", svg)
        self.assertIsNone(camel_case_leak, f"camelCase type name leaked into text: {camel_case_leak}")


class CleanMarkupTest(unittest.TestCase):
    """J.C item 8 / Day-12 done-when: no inline style=/onmouseover=/onmouseout=
    anywhere in the output — hover, focus, and the legend swatches all live in
    the shared shell's CSS (.wf-node / .swatch--* classes) instead."""

    def test_no_inline_style_or_event_handlers(self):
        page = _page([
            Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                  width=300, height=200, fields=["Sales.Revenue", "Date.Month"]),
            Visual(id="v2", type="slicer", is_slicer=True, x=310, y=0, z=0, width=100, height=80,
                  fields=["Date.Year"]),
        ])
        wrapper = render_wireframe(page)  # whole wrapper, legend included
        self.assertNotIn("onmouseover", wrapper)
        self.assertNotIn("onmouseout", wrapper)
        self.assertNotIn("style=", wrapper)          # incl. the legend chips
        self.assertIn('class="wf-node cat-data"', wrapper)
        self.assertIn('class="wf-chip-dot wf-chip-dot--data"', wrapper)


class OnCanvasLabelTest(unittest.TestCase):
    """Day-12: the on-canvas text is the visual's real title + friendly type,
    never the "WIP" placeholder the temporary hack left behind."""

    def test_large_visual_renders_real_title_and_type_not_wip(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue", "Date.Month"])])
        svg = render_wireframe(page)
        self.assertNotIn("WIP", svg)
        self.assertIn(">Revenue by Month</text>", svg)   # real-case title in the pill
        self.assertIn(">COLUMN CHART</text>", svg)        # friendly type, small-caps caption

    def test_long_title_is_truncated_on_canvas(self):
        page = _page([Visual(id="v1", type="columnChart",
                             title="Revenue by Month and Region and Product Line",
                             x=0, y=0, z=0, width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn("…</text>", svg)                    # truncated with an ellipsis
        self.assertNotIn("Product Line</text>", svg)      # tail dropped on-canvas

    def test_compact_visual_renders_its_title_not_wip(self):
        # A mid-size region (compact tier: too small for the pill+caption, big
        # enough for an icon badge + title) shows the visual's own title.
        page = _page([Visual(id="v1", type="lineChart", title="Trend", x=0, y=0, z=0,
                             width=120, height=55, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertNotIn("WIP", svg)
        self.assertIn(">Trend</text>", svg)


class RealCaseTextTest(unittest.TestCase):
    """v5 (2026-07-10): the wireframe no longer force-uppercases its on-canvas
    text — a global text-transform was shouting every visual title. Titles
    render in their real case (Poppins) now; only the legend keeps its own
    uppercase modifier (short category labels, not proper names)."""

    def test_on_canvas_text_is_not_force_uppercased(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue", "Date.Month"])])
        svg = render_wireframe(page)
        self.assertNotIn("text-transform", svg)             # no shout-everything rule
        self.assertIn('font-family: "Poppins"', svg)         # Poppins still enforced
        self.assertIn(">Revenue by Month</text>", svg)       # real-case title on canvas

    def test_legend_uses_the_uppercase_modifier(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn('class="legend legend--upper wf-legend"', svg)


class StudioDesignTest(unittest.TestCase):
    """v6 (2026-07-11) "Product mock" direction: every visual is a solid
    white card (hairline stroke, shadow via the shell's .wf-node class) with
    a gradient icon chip, real-case title + small-caps type caption, and a
    per-type skeleton chart ghosted inside; decorative/nav objects render as
    quieter dashed "ghost" cards. The sheet is the shared v6 gradient +
    dot-grid canvas from ``_diagram_theme``."""

    def test_data_card_is_solid_white_with_a_skeleton_chart(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn('class="wf-card-bg cat-data" fill="#ffffff" stroke="#e2e7f2"', svg)
        self.assertIn('url(#dg-chip-data-', svg)   # gradient icon chip
        self.assertIn('url(#dg-sk-data-', svg)     # skeleton bar fill

    def test_header_shows_real_case_title_with_a_white_icon(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn(">Revenue by Month</text>", svg)  # real case, in the header
        self.assertIn('stroke="#ffffff"', svg)           # icon knocked out white

    def test_big_card_gets_a_small_caps_type_caption(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn(">COLUMN CHART</text>", svg)

    def test_every_category_gets_its_own_gradient_chip(self):
        page = _page([
            Visual(id="v1", type="slicer", is_slicer=True, title="Region", x=0, y=0, z=0, width=150, height=100, fields=["Geo.Region"]),
            Visual(id="v2", type="actionButton", title="Go", x=160, y=0, z=0, width=150, height=90),
            Visual(id="v3", type="textbox", title="Note", x=320, y=0, z=0, width=150, height=90),
        ])
        svg = render_wireframe(page)
        self.assertIn('url(#dg-chip-slicer-', svg)
        self.assertIn('url(#dg-chip-nav-', svg)
        self.assertIn('url(#dg-chip-decorative-', svg)

    def test_every_category_gets_an_icon(self):
        # nav buttons and each decorative kind carry their own icon, not just
        # data visuals and slicers.
        page = _page([
            Visual(id="v1", type="actionButton", title="View Details", x=0, y=0, z=0, width=150, height=90),
            Visual(id="v2", type="image", title="Logo", x=160, y=0, z=0, width=150, height=150),
            Visual(id="v3", type="textbox", title="Note", x=320, y=0, z=0, width=150, height=90),
        ])
        svg = render_wireframe(page)
        self.assertIn("wf-i-button-", svg)
        self.assertIn("wf-i-image-", svg)
        self.assertIn("wf-i-textbox-", svg)

    def test_decorative_and_nav_cards_are_ghosts(self):
        page = _page([
            Visual(id="v1", type="actionButton", title="Go", x=0, y=0, z=0, width=150, height=90),
            Visual(id="v2", type="columnChart", title="Revenue", x=160, y=0, z=0,
                  width=300, height=200, fields=["Sales.Revenue"]),
        ])
        svg = render_wireframe(page)
        self.assertIn('fill-opacity=".65"', svg)          # quiet ghost surface
        self.assertIn('stroke-dasharray="4 3"', svg)      # dashed hairline
        # ...but the data card itself is solid (only one dashed card here).
        self.assertEqual(svg.count('stroke-dasharray="4 3"'), 1)

    def test_skeletons_differ_by_type_and_are_deterministic(self):
        line = _page([Visual(id="v1", type="lineChart", title="Trend", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        donut = _page([Visual(id="v1", type="donutChart", title="Mix", x=0, y=0, z=0,
                              width=300, height=200, fields=["Sales.Revenue"])])
        line_svg = render_wireframe(line)
        self.assertIn('url(#dg-area-data-', line_svg)      # line chart: area fill under the line
        self.assertIn("stroke-dasharray", render_wireframe(donut))  # donut: dashed ring arc
        # Same input twice -> byte-identical output (golden-file stability).
        self.assertEqual(line_svg, render_wireframe(line))

    def test_canvas_is_the_shared_gradient_dot_grid(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn('url(#dg-canvas-', svg)
        self.assertIn('url(#dg-dots-', svg)

    def test_no_legacy_swatch_modifier_classes_survive(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        self.assertNotIn("swatch--", render_wireframe(page))


class PageTabBarTest(unittest.TestCase):
    """v6: with ``sibling_pages`` supplied (report_pages() always does), the
    sheet gains a Power BI-style page-tab strip — the active page as a pill,
    every other page a linked ghost tab onto its ``#page-…`` anchor, plus
    the true page pixel size."""

    def test_tab_bar_lists_siblings_as_links_and_page_size(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page, sibling_pages=["IT Spend Trend", "Plan Variance Analysis", "Tooltip"])
        self.assertIn(">IT Spend Trend</text>", svg)                       # active tab
        self.assertIn('href="#page-plan-variance-analysis"', svg)          # sibling link
        self.assertIn('href="#page-tooltip"', svg)
        self.assertIn("1280 × 720", svg)                                   # true page size
        self.assertNotIn('href="#page-it-spend-trend"', svg)               # active tab isn't a self-link

    def test_no_sibling_pages_means_no_tab_bar(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertNotIn("wf-tab", svg)
        self.assertNotIn("1280 × 720", svg)


class VisualAnchorMapTest(unittest.TestCase):
    """Day 13 / I3: report_pages() relabels 2+ identical visuals into one
    "Label — Type ×N" table row and dedupe_ids() resolves any remaining
    slug collision between different rows — the wireframe's own <a href>
    must land on that *resolved* anchor, not an independently recomputed
    raw one, or it's a dead/wrong link the moment a page has duplicate or
    slug-colliding visuals (both real, common shapes: repeated KPI cards;
    two differently-worded titles that strip down to the same slug)."""

    def test_anchor_map_resolves_the_grouped_relabel(self):
        page = _page([Visual(id="v1", type="card", title="Sale Value", x=0, y=0, z=0,
                             width=90, height=70, fields=["Sales.Sale_Value"])])
        # Simulates what report_pages() computes once 2+ identical cards get
        # merged into one "Sale Value — Card ×2" row. Key order/shape must
        # match _wireframe.py's own: (title, friendly_type, metrics, dims).
        key = ("Sale Value", "Card", frozenset({"Sale_Value"}), frozenset())
        svg = render_wireframe(page, measure_names=frozenset({"Sale_Value"}),
                               visual_anchor_map={key: "sale-value-card-2"})
        self.assertIn('href="#visual-it-spend-trend-sale-value-card-2"', svg)
        self.assertNotIn('href="#visual-it-spend-trend-sale-value"', svg)

    def test_missing_map_entry_falls_back_to_the_raw_slug(self):
        # A caller with no map (or a map missing this particular visual)
        # degrades to the pre-existing raw-slug behavior rather than erroring.
        page = _page([Visual(id="v1", type="card", title="Sale Value", x=0, y=0, z=0,
                             width=90, height=70, fields=["Sales.Sale_Value"])])
        svg = render_wireframe(page, visual_anchor_map={})
        self.assertIn('href="#visual-it-spend-trend-sale-value"', svg)

    def test_no_map_argument_at_all_still_works(self):
        page = _page([Visual(id="v1", type="card", title="Sale Value", x=0, y=0, z=0,
                             width=90, height=70, fields=["Sales.Sale_Value"])])
        svg = render_wireframe(page)  # no visual_anchor_map kwarg
        self.assertIn('href="#visual-it-spend-trend-sale-value"', svg)


class LinkCategoryTest(unittest.TestCase):
    """I3: only data visuals link to their table row; slicers link to the
    page card (where the filter list lives); buttons/shapes/text/images
    render unlinked rather than pointing at a row that doesn't exist."""

    def test_data_visual_links_to_its_visual_anchor(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue", "Date.Month"])])
        svg = render_wireframe(page)
        self.assertIn('href="#visual-it-spend-trend-revenue-by-month"', svg)

    def test_slicer_links_to_the_page_anchor_not_a_visual_row(self):
        page = _page([Visual(id="v1", type="slicer", is_slicer=True, title="Year", x=0, y=0, z=0,
                             width=100, height=80, fields=["Date.Year"])])
        svg = render_wireframe(page)
        self.assertIn('href="#page-it-spend-trend"', svg)
        self.assertNotIn("href=\"#visual-", svg)

    def test_button_and_decorative_visuals_are_not_linked(self):
        page = _page([
            Visual(id="v1", type="actionButton", x=0, y=0, z=0, width=100, height=40),
            Visual(id="v2", type="basicShape", x=110, y=0, z=0, width=100, height=40),
            Visual(id="v3", type="textbox", x=220, y=0, z=0, width=100, height=40),
        ])
        svg = render_wireframe(page)
        self.assertNotIn("<a ", svg)


class TinyObjectAndOverflowTest(unittest.TestCase):
    """J.C item 6: sub-threshold objects collapse to a dot; 3+ decorative
    objects fold into a footer note instead of a wall of near-identical
    rectangles."""

    def test_tiny_object_renders_as_an_unlinked_dot(self):
        # 1280x720 page area = 921,600; a 5x5 visual is ~0.0027% of that.
        page = _page([Visual(id="v1", type="shape", x=0, y=0, z=0, width=5, height=5)])
        svg = render_wireframe(page)
        self.assertIn("<circle", svg)
        self.assertNotIn("<a ", svg)

    def test_three_or_more_decorative_shapes_collapse_with_a_footer_note(self):
        visuals = [Visual(id=f"v{i}", type="shape", x=i * 150, y=0, z=0, width=120, height=100)
                  for i in range(5)]
        svg = render_wireframe(_page(visuals))
        self.assertIn("wf-footer", svg)
        self.assertIn("decorative shape", svg)


class OcclusionTest(unittest.TestCase):
    """Day 6: a data visual mostly covered by a larger-or-equal one already
    placed on the canvas renders as a ghost outline + numbered chip
    instead of a full card, and is listed under the canvas — never simply
    invisible on a dense page (the 20-visual Plan Variance page this was
    built for)."""

    def test_small_visual_fully_behind_a_big_one_is_flagged_occluded(self):
        visuals = [
            Visual(id="big", type="clusteredColumnChart", title="Big Chart", x=0, y=0, z=0,
                  width=600, height=400, fields=["Sales.Actual"]),
            Visual(id="hidden", type="card", title="Hidden KPI", x=50, y=50, z=1,
                  width=100, height=80, fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        self.assertIn("wf-occluded", svg)
        self.assertIn("hidden behind another visual", svg)
        self.assertIn("wf-occluded-list", svg)
        self.assertIn("Hidden KPI", svg)

    def test_occluded_visual_is_still_a_working_link(self):
        visuals = [
            Visual(id="big", type="clusteredColumnChart", title="Big Chart", x=0, y=0, z=0,
                  width=600, height=400, fields=["Sales.Actual"]),
            Visual(id="hidden", type="card", title="Hidden KPI", x=50, y=50, z=1,
                  width=100, height=80, fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        # The numbered callout list must link to the same anchor the
        # (ghosted) canvas card itself links to — one real destination
        # for "Hidden KPI", referenced twice (canvas ghost + list entry).
        hidden_hrefs = re.findall(r'href="(#visual-[^"]*hidden[^"]*)"', svg)
        self.assertEqual(len(set(hidden_hrefs)), 1)
        self.assertEqual(len(hidden_hrefs), 2)

    def test_non_overlapping_visuals_are_never_flagged(self):
        visuals = [
            Visual(id="a", type="card", title="A", x=0, y=0, z=0, width=200, height=100,
                  fields=["Sales.Actual"]),
            Visual(id="b", type="card", title="B", x=400, y=0, z=0, width=200, height=100,
                  fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        self.assertNotIn("wf-occluded", svg)
        self.assertNotIn("wf-occluded-list", svg)

    def test_partial_overlap_below_threshold_is_not_flagged(self):
        # ~20% overlap — real report layouts routinely have visuals that
        # graze each other's edges; that's not the same as one being
        # hidden behind the other.
        visuals = [
            Visual(id="a", type="card", title="A", x=0, y=0, z=0, width=200, height=200,
                  fields=["Sales.Actual"]),
            Visual(id="b", type="card", title="B", x=160, y=0, z=1, width=200, height=200,
                  fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        self.assertNotIn("wf-occluded", svg)

    def test_draws_larger_area_first_regardless_of_z_order(self):
        # z-order alone (a small object with a high z drawn "on top") does
        # not prevent it from being flagged occluded if its footprint is
        # actually covered by a larger visual — Day 6 sorts by area, not z.
        visuals = [
            Visual(id="small_high_z", type="card", title="Small", x=50, y=50, z=99,
                  width=80, height=60, fields=["Sales.Actual"]),
            Visual(id="big_low_z", type="clusteredColumnChart", title="Big", x=0, y=0, z=0,
                  width=500, height=400, fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        self.assertIn("wf-occluded", svg)

    def test_decorative_shapes_never_cause_false_occlusion_of_data_visuals(self):
        # A full-page decorative background must not flag every data
        # visual on the page as "occluded" — only tracked against other
        # data visuals.
        visuals = [
            Visual(id="bg", type="shape", x=0, y=0, z=0, width=1280, height=720),
            Visual(id="v1", type="card", title="KPI", x=50, y=50, z=1, width=150, height=100,
                  fields=["Sales.Actual"]),
        ]
        svg = render_wireframe(_page(visuals), measure_names=frozenset({"Actual"}))
        self.assertNotIn("wf-occluded", svg)


class LegendAndTooltipTest(unittest.TestCase):
    def test_legend_present(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue"])])
        svg = render_wireframe(page)
        self.assertIn("Data visual", svg)
        self.assertIn("Slicer", svg)
        self.assertIn("Navigation", svg)
        self.assertIn("Decorative", svg)

    def test_data_visual_has_a_native_tooltip_naming_type_and_fields(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=300, height=200, fields=["Sales.Revenue", "Date.Month"])])
        svg = render_wireframe(page)
        self.assertIn("Revenue by Month — Column chart (Sales.Revenue, Date.Month)", svg)
        self.assertNotIn("no fields bound", svg)

    def test_large_data_visual_shows_bound_fields_on_the_card(self):
        page = _page([Visual(id="v1", type="columnChart", title="Revenue by Month", x=0, y=0, z=0,
                             width=500, height=300, fields=["Sales.Revenue", "Date.Month"])])
        svg = render_wireframe(page)
        self.assertIn(">Revenue, Month</text>", svg)

    def test_returns_empty_string_without_layout_coordinates(self):
        page = Page(id="p1", display_name="No Layout", visuals=[Visual(id="v1", type="card")])
        self.assertEqual(render_wireframe(page), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
