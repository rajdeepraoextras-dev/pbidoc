"""Phase 3 tests: HTML, DOCX, and the Pandoc PDF adapter.

The pure-Python renderers (HTML, DOCX) run fully here. The Pandoc adapter is
tested for whichever path this environment supports — real conversion if Pandoc
is installed, graceful ``PandocError`` if not.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.dom.minidom as minidom
import zipfile
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import (
    AuditReportGenerator,
    BusinessGuideGenerator,
    ExecutiveSummaryGenerator,
)
from pbicompass.parsers import detect_and_parse
from pbicompass.schemas.audit_document import FindingCluster
from pbicompass.render import (
    pandoc,
    render_audit_docx,
    render_audit_html,
    render_audit_markdown,
    render_docx,
    render_executive_docx,
    render_executive_html,
    render_executive_markdown,
    render_html,
    render_markdown,
    render_user_guide_docx,
    render_user_guide_html,
    render_user_guide_markdown,
)
from pbicompass.render import registry as render_registry

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"

_SECTION_TITLES = [
    "1. Document Control",
    "2. Executive Summary",
    "5. Data Sources",
    "6. Data Model",
    "7. Measures",
    "8. Report Pages",
    "10. Row-Level Security",
    "15. Known Issues",
    "16. Model Health",
    "18. Appendix",
]


def _doc():
    return generate_document(detect_and_parse(FIXTURE))


def _audit_doc():
    return AuditReportGenerator.generate(detect_and_parse(FIXTURE))


def _audit_doc_with_clusters():
    """A Day-7/8 root-cause cluster attached to a real generated audit doc —
    one rule_id that genuinely resolves to a finding already on the doc (so
    the deep link can be asserted to resolve), plus one that doesn't (so the
    unresolved fallback can be asserted too)."""
    doc = _audit_doc()
    real_rule_id = next((bp.rule_id for bp in doc.best_practices if bp.rule_id), "")
    assert real_rule_id, "fixture must produce at least one rule-ID-backed best-practice check"
    doc.clusters = [
        FindingCluster(
            root_cause="Auto Date/Time is enabled",
            rule_ids=[real_rule_id, "PBIC-DOES-NOT-EXIST"],
            narrative="Disabling Auto Date/Time clears this and its dependent findings.",
            confidence="High",
        )
    ]
    doc.strategic_narrative = "Fixing the top root cause clears the largest share of findings."
    return doc, real_rule_id


def _executive_doc():
    return ExecutiveSummaryGenerator.generate(detect_and_parse(FIXTURE))


def _user_guide_doc():
    return BusinessGuideGenerator.generate(detect_and_parse(FIXTURE))


class DiagramPayloadOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.technical = _doc()
        cls.executive = _executive_doc()
        cls.user_guide = _user_guide_doc()

    def test_json_omits_html_only_diagram_markup(self):
        technical = json.loads(self.technical.to_json())
        self.assertNotIn("lineage_svg", technical["lineage"])
        self.assertTrue(technical["lineage"]["lineage_edges"])
        self.assertTrue(technical["report_pages"])
        self.assertTrue(all("wireframe_svg" not in page for page in technical["report_pages"]))

        executive = json.loads(self.executive.to_json())
        self.assertTrue(executive["page_thumbnails"])
        self.assertTrue(all("svg" not in page for page in executive["page_thumbnails"]))
        self.assertTrue(all({"name", "anchor"} <= set(page) for page in executive["page_thumbnails"]))

        user_guide = json.loads(self.user_guide.to_json())
        self.assertTrue(user_guide["pages"])
        self.assertTrue(all("wireframe_svg" not in page for page in user_guide["pages"]))

    def test_markdown_contains_no_svg_diagram_markup(self):
        outputs = (
            render_markdown(self.technical),
            render_executive_markdown(self.executive),
            render_user_guide_markdown(self.user_guide),
        )
        for markdown in outputs:
            self.assertNotIn("<svg", markdown)
            self.assertNotIn("wireframe_svg", markdown)
            self.assertNotIn("lineage_svg", markdown)


class HtmlRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_html(_doc())

    def test_is_html_document(self):
        self.assertTrue(self.html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn("</body></html>", self.html)

    def test_all_sections_present_in_order(self):
        positions = [self.html.find(t) for t in _SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_escapes_dax_markup(self):
        # the 'Total Revenue' DAX contains <> and quotes; they must be escaped.
        # Scoped to the literal DAX fragment rather than a bare "<>" search —
        # the shared shell's own JS legitimately contains that 2-char
        # substring inside a regex character class (escapeHtml's [&<>"']).
        self.assertIn("&lt;&gt;", self.html)
        self.assertNotIn("Status <>", self.html)

    def test_no_external_network_calls(self):
        # 1.11: zero CDN calls anywhere in the generated HTML — the document
        # must render and look correct fully offline/air-gapped.
        for banned in ("fonts.googleapis.com", "fonts.gstatic.com"):
            self.assertNotIn(banned, self.html)

    def test_no_provenance_icons(self):
        # 5.6: provenance badges are plain text ("Extracted"/"AI-inferred"/
        # "Human-provided") — no glyph prefix.
        for icon in ("⚙", "✨", "\U0001F464"):  # gear, sparkles, bust
            self.assertNotIn(icon, self.html)

    def test_every_section_has_exactly_one_provenance_badge(self):
        # 5.6: all 19 H2 sections carry a badge, not just the first five.
        pills = re.findall(r'<h2 id="sec(\d+)">.*?<span class="pill ([\w-]+)">([\w-]+)</span></h2>', self.html)
        found = {int(n) for n, _, _ in pills}
        self.assertEqual(found, set(range(1, 20)))
        for _, cls, label in pills:
            self.assertIn(label, ("Extracted", "AI-inferred", "Human-provided"))
            self.assertEqual(cls, label.lower())

    def test_dark_mode_toggle_and_mobile_toc_present(self):
        # 2.5/2.9: every HTML doc ships a theme toggle and a mobile
        # hamburger — via the shared shell, so this covers all four
        # doc-type renderers, not just the technical one.
        self.assertIn('class="theme-toggle"', self.html)
        self.assertIn('class="mobile-toc-toggle"', self.html)
        self.assertIn("pbicompass-theme", self.html)

    def test_dax_highlighting_and_copy_button(self):
        # 2.3: measure DAX renders through the tokenizer with a copy button,
        # not as a raw unhighlighted <pre> block.
        self.assertIn('class="copy-btn"', self.html)
        self.assertIn("tok-keyword", self.html)

    def test_long_dax_measure_collapses_behind_details(self):
        # 2.4: a >10-line DAX expression collapses behind <details>, summary
        # = measure name.
        doc = generate_document(detect_and_parse(FIXTURE))
        long_measure = doc.measure_catalog.measures[0]
        long_measure.dax = "\n".join(f"VAR Step{i} = {i}" for i in range(12)) + "\nRETURN Step0"
        html = render_html(doc)
        self.assertIn(f'<details class="collapsible"><summary>{long_measure.name}', html)

    def test_short_dax_measure_is_not_collapsed(self):
        # every SampleSales measure is a one-liner — none should be wrapped
        # in a <details> disclosure.
        self.assertNotIn('lines (click to expand)', self.html)

    def test_interactive_diagram_nodes_and_edges(self):
        # 2.6: table nodes are clickable/hoverable, edges carry endpoints and
        # a join-column tooltip, and each table row in "Key tables" has a
        # stable anchor the diagram can jump to.
        self.assertIn('class="dm-node" data-table="Sales"', self.html)
        self.assertIn('class="dm-edge" data-from=', self.html)
        self.assertIn('id="table-sales"', self.html)
        self.assertIn("→", self.html)  # join-column tooltip text

    def test_lineage_and_wireframes_are_hidden_behind_view_controls(self):
        self.assertIn('<details class="diagram-reveal"><summary><span>View lineage</span></summary>', self.html)
        self.assertIn('<details class="diagram-reveal"><summary><span>View wireframe</span></summary>', self.html)
        self.assertLess(
            self.html.index("<span>View lineage</span>"),
            self.html.index('aria-labelledby="lineage-diagram-title"'),
        )
        self.assertLess(
            self.html.index("<span>View wireframe</span>"),
            self.html.index('aria-labelledby="wireframe-title-'),
        )

    def test_client_side_search_index_present(self):
        # 2.2: a JSON search index (sections + measures + tables) and the
        # search box markup, no CDN/lunr dependency.
        self.assertIn('id="search-index"', self.html)
        self.assertIn('class="search-input"', self.html)
        self.assertIn('"type": "measure"', self.html)
        self.assertIn('"type": "table"', self.html)
        self.assertIn("Total Revenue", self.html.split('id="search-index"')[1].split("</script>")[0])

    def test_print_cover_page_present(self):
        # 2.8: a print-only cover page (hidden on screen, shown via
        # @media print) carries title/version/status/owner.
        self.assertIn('class="print-cover"', self.html)
        self.assertIn("Not specified", self.html)  # no owner/version/status set on this doc

    def test_print_watermark_only_for_confidential_or_restricted(self):
        # 2.8: the diagonal watermark only appears for Confidential/
        # Restricted classifications, never for an unset or benign one.
        doc = generate_document(detect_and_parse(FIXTURE), classification="Confidential")
        self.assertIn('<div class="print-watermark">CONFIDENTIAL</div>', render_html(doc))

        doc_internal = generate_document(detect_and_parse(FIXTURE), classification="Internal")
        self.assertNotIn('class="print-watermark"', render_html(doc_internal))

        self.assertNotIn('class="print-watermark"', self.html)  # no classification set at all

    def test_accessibility_landmarks_present(self):
        # 2.10: skip link, semantic nav/main landmarks, and a labeled model
        # diagram SVG.
        self.assertIn('class="skip-link"', self.html)
        self.assertIn('<nav class="sidebar"', self.html)
        self.assertIn('id="main-content"', self.html)
        self.assertIn('role="img" aria-labelledby="model-diagram-title"', self.html)

    def test_no_microsecond_timestamp(self):
        # 1.8: the raw ISO generated_at (with microseconds) must never leak
        # into rendered output; the header/Document Control show the
        # human-readable form instead.
        self.assertNotRegex(self.html, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+")

    def test_contains_content(self):
        self.assertIn("SampleSales", self.html)
        self.assertIn("Orphan Margin", self.html)  # orphaned measure surfaced
        self.assertIn("decomposition tree", self.html)  # complex-visual explainer

    def test_custom_metadata_rendering(self):
        doc = generate_document(
            detect_and_parse(FIXTURE),
            version="v1.2.3",
            status="Approved_Draft",
            author="TestAuthorName",
            business_decision="TestDecisionDescription",
            assumptions="TestAssumptionDescription"
        )
        html = render_html(doc)
        md_txt = render_markdown(doc)
        
        self.assertIn("v1.2.3", html)
        self.assertIn("Approved_Draft", html)
        self.assertIn("TestAuthorName", html)
        self.assertIn("TestDecisionDescription", html)
        self.assertIn("TestAssumptionDescription", html)
        
        self.assertIn("v1.2.3", md_txt)
        self.assertIn("Approved_Draft", md_txt)
        self.assertIn("TestAuthorName", md_txt)
        self.assertIn("TestDecisionDescription", md_txt)
        self.assertIn("TestAssumptionDescription", md_txt)

    def test_advanced_audits_and_fixes(self):
        doc = generate_document(
            detect_and_parse(FIXTURE),
            owner="Jane Developer"
        )
        html = render_html(doc)
        md_txt = render_markdown(doc)
        
        # 1. Sign-off table pre-fill Developer with name and the generation date
        generated_date = doc.metadata.generated_at[:10]
        self.assertIn("Jane Developer", html)
        self.assertIn(generated_date, html)
        self.assertIn("Obtain sign-off before sharing with stakeholders", html)
        self.assertIn("Jane Developer", md_txt)
        self.assertIn(generated_date, md_txt)
        self.assertIn("Obtain sign-off before sharing with stakeholders", md_txt)
        
        # 2. G.3: no inferred-filler requirements table when none are captured
        # ("confirm with the business owner" filler rows) — a plain TODO line
        # instead.
        self.assertIn("Business requirements have not yet been captured", html)
        self.assertIn("Business requirements have not yet been captured", md_txt)
        self.assertNotIn("REQ-01", html)
        self.assertNotIn("REQ-01", md_txt)
        
        # 3. Refresh placeholder rows
        self.assertIn("Gateway Name", html)
        self.assertIn("Failure Alert Contact", html)
        self.assertIn("Gateway Name", md_txt)
        self.assertIn("Failure Alert Contact", md_txt)
        
        # 4. Glossary auto-populate
        self.assertTrue(len(doc.glossary_entries) > 0)
        self.assertIn("Glossary", html)


class PanZoomVendorTest(unittest.TestCase):
    """Day 6: svg-pan-zoom is vendored inline (never a <script src>/CDN) so
    the model diagram / lineage graph / page wireframes get real pan/zoom
    even when the generated HTML is opened offline with no web server."""

    @classmethod
    def setUpClass(cls):
        cls.html = render_html(_doc())

    def test_vendored_library_is_inlined_not_a_cdn_reference(self):
        from pbicompass.render._vendor_svg_pan_zoom import SVG_PAN_ZOOM_JS
        self.assertIn(SVG_PAN_ZOOM_JS, self.html)
        for banned in ("cdn.", "unpkg.com", "jsdelivr", "<script src="):
            self.assertNotIn(banned, self.html)

    def test_vendor_script_loads_before_the_init_script(self):
        # svgPanZoom(...) is called inside _SCRIPT's DOMContentLoaded
        # handler — the vendor <script> tag defining it must appear earlier
        # in the document, or the global would be undefined when called.
        vendor_pos = self.html.find("window.svgPanZoom = window.__spz_require(3);")
        init_pos = self.html.find("svgPanZoom(svg,")
        self.assertNotEqual(vendor_pos, -1)
        self.assertNotEqual(init_pos, -1)
        self.assertLess(vendor_pos, init_pos)

    def test_beforeprint_resets_every_instance(self):
        # G5 fix: print can render at a different width than the screen
        # (e.g. a narrower print column), so beforeprint must recompute
        # each diagram's height from its own viewBox aspect ratio (not
        # just call .reset(), which would re-fit against a stale cached
        # size) before resetting pan/zoom to the fitted, centered default.
        self.assertIn("window.addEventListener('beforeprint'", self.html)
        self.assertIn("panZoomInstances.forEach(({ instance, sizeToAspect }) => {", self.html)
        self.assertIn("instance.resize();", self.html)
        self.assertIn("instance.fit();", self.html)
        self.assertIn("instance.center();", self.html)

    def test_click_suppression_is_gated_on_a_real_mousedown(self):
        # P0-class regression (Day 6): beforePan also fires for the
        # programmatic .reset() used above — without gating "moved" on an
        # active user mousedown/touchstart, a print would permanently
        # suppress the next click on every diagram link.
        self.assertIn("let isDown = false, moved = false;", self.html)
        self.assertIn("beforePan: () => { if (isDown) moved = true; }", self.html)

    def test_vendored_js_source_module_has_no_script_close_tag_leak(self):
        from pbicompass.render._vendor_svg_pan_zoom import SVG_PAN_ZOOM_JS
        self.assertNotIn("</script>", SVG_PAN_ZOOM_JS)


class CalcGroupHierarchyRenderTest(unittest.TestCase):
    """Track B1: calc-group items and hierarchies must appear in the technical
    doc across md/html/docx. Model built in-code so no fixture/golden changes."""

    def _doc_with_features(self):
        from pbicompass.schemas.model import (
            SemanticModel, Table, Column, Measure,
            CalculationItem, Hierarchy, HierarchyLevel,
        )
        model = SemanticModel(report_name="Sales", tables=[
            Table(name="Time Intelligence", kind="calculation-group",
                  calculation_group_precedence=10,
                  calculation_items=[
                      CalculationItem(name="Current", expression="SELECTEDMEASURE()", ordinal=0),
                      CalculationItem(name="YTD",
                                      expression="CALCULATE(SELECTEDMEASURE(), DATESYTD('Date'[Date]))",
                                      ordinal=1, format_string_expression='"#,##0"'),
                  ]),
            Table(name="Date",
                  columns=[Column(name="Year", data_type="int64"),
                           Column(name="Quarter", data_type="string")],
                  hierarchies=[Hierarchy(name="Calendar", levels=[
                      HierarchyLevel(name="Year", column="Year"),
                      HierarchyLevel(name="Quarter", column="Quarter")])]),
            Table(name="Sales", kind="fact",
                  measures=[Measure(name="Total", expression="SUM(Sales[Amt])")]),
        ])
        return generate_document(model)

    def test_markdown_shows_calc_groups_and_hierarchies(self):
        md = render_markdown(self._doc_with_features())
        self.assertIn("Hierarchies", md)
        self.assertIn("Calculation groups", md)
        self.assertIn("Date[Calendar]", md)
        self.assertIn("DATESYTD", md)
        self.assertIn("precedence 10", md)

    def test_html_shows_calc_groups_and_hierarchies(self):
        html = render_html(self._doc_with_features())
        self.assertIn("<h3>Hierarchies</h3>", html)
        self.assertIn("<h3>Calculation groups</h3>", html)
        self.assertIn("SELECTEDMEASURE()", html)

    def test_docx_shows_calc_groups_and_hierarchies(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(self._doc_with_features(), Path(td) / "r.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Calculation groups", document)
            self.assertIn("Hierarchies", document)
            self.assertIn("DATESYTD", document)


class KpiAndRefreshPolicyRenderTest(unittest.TestCase):
    """Track B3/B4: measure KPIs appear in §7, refresh policies in §11, in all
    three renderers. Model built in-code so no fixture/golden changes."""

    def _doc(self):
        from pbicompass.schemas.model import (
            SemanticModel, Table, Measure, MeasureKPI, RefreshPolicy,
        )
        model = SemanticModel(report_name="Sales", tables=[
            Table(name="Sales", kind="fact",
                  measures=[Measure(name="Sales KPI", expression="[Total]",
                                    kpi=MeasureKPI(target_expression="[Target]",
                                                   status_expression="DIVIDE([Total],[Target])",
                                                   status_graphic="Traffic Light - Single"))],
                  refresh_policy=RefreshPolicy(policy_type="basic", rolling_window_periods=3,
                                               rolling_window_granularity="month",
                                               incremental_periods=10, incremental_granularity="day")),
        ])
        return generate_document(model)

    def test_markdown(self):
        md = render_markdown(self._doc())
        self.assertIn("KPI targets", md)
        self.assertIn("[Target]", md)
        self.assertIn("Incremental refresh policies", md)
        self.assertIn("stores the last 3 months", md)
        self.assertIn("refreshes the last 10 days", md)

    def test_html(self):
        html = render_html(self._doc())
        self.assertIn("<h3>KPI targets</h3>", html)
        self.assertIn("Incremental refresh policies", html)

    def test_docx(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(self._doc(), Path(td) / "r.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("KPI targets", document)
            self.assertIn("Incremental refresh", document)


class FieldParamPerspectiveCultureRenderTest(unittest.TestCase):
    """Track B5/B6: field parameters, perspectives, cultures, and measure
    dynamic format strings appear in the technical doc across md/html/docx."""

    def _doc(self):
        from pbicompass.schemas.model import (
            SemanticModel, Table, Column, Measure, FieldParameter, Perspective, Culture,
        )
        model = SemanticModel(report_name="Sales",
            tables=[Table(name="Sales", kind="fact",
                          columns=[Column(name="Amt")],
                          measures=[Measure(name="Total", expression="SUM(Sales[Amt])",
                                            format_string_expression='IF([Total]>0,"#,0","(#,0)")')])],
            field_parameters=[FieldParameter(table="Field Selector",
                                             fields=["Sales[Amt]"], display_names=["Amount"])],
            perspectives=[Perspective(name="Exec View", tables=["Sales"], measures=["Total"])],
            cultures=[Culture(name="fr-FR", translated_object_count=4)])
        return generate_document(model)

    def test_markdown(self):
        md = render_markdown(self._doc())
        self.assertIn("Field parameters", md)
        self.assertIn("Sales[Amt]", md)
        self.assertIn("Perspectives", md)
        self.assertIn("Exec View", md)
        self.assertIn("fr-FR", md)
        self.assertIn("Dynamic format strings", md)

    def test_default_single_culture_is_not_documented(self):
        """Power BI writes a default en-US cultureInfo (0 translations) into
        every model; documenting it on a single-language report is noise. Only
        a genuinely multi-language or translated model earns the section."""
        from pbicompass.schemas.model import SemanticModel, Table, Culture
        model = SemanticModel(report_name="R", tables=[Table(name="Sales")],
                              cultures=[Culture(name="en-US", translated_object_count=0)])
        self.assertNotIn("Translations / languages", render_markdown(generate_document(model)))

    def test_translated_culture_is_documented(self):
        from pbicompass.schemas.model import SemanticModel, Table, Culture
        model = SemanticModel(report_name="R", tables=[Table(name="Sales")],
                              cultures=[Culture(name="fr-FR", translated_object_count=12)])
        self.assertIn("fr-FR", render_markdown(generate_document(model)))

    def test_html(self):
        html = render_html(self._doc())
        self.assertIn("<h3>Field parameters</h3>", html)
        self.assertIn("<h3>Perspectives</h3>", html)
        self.assertIn("<h3>Dynamic format strings</h3>", html)

    def test_docx(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(self._doc(), Path(td) / "r.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Field parameters", document)
            self.assertIn("Perspectives", document)
            self.assertIn("Dynamic format", document)


class DocxRenderTest(unittest.TestCase):
    def test_valid_ooxml_package(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(_doc(), Path(td) / "report.docx")
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertEqual(
                    names,
                    {"[Content_Types].xml", "_rels/.rels",
                     "word/_rels/document.xml.rels", "word/styles.xml",
                     "word/document.xml"},
                )
                document = zf.read("word/document.xml").decode("utf-8")
                styles = zf.read("word/styles.xml").decode("utf-8")
            minidom.parseString(document)  # well-formed XML or raises
            minidom.parseString(styles)
            self.assertIn("SampleSales", document)
            self.assertIn("Orphan Margin", document)
            self.assertIn("SUMX", document)  # raw DAX preserved
            self.assertIn('w:pStyle w:val="Heading1"', document)  # navigable headings

    def test_no_provenance_icons(self):
        # 5.6: DOCX carries the same plain-text badges as HTML/Markdown.
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(_doc(), Path(td) / "report.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
        for icon in ("⚙", "✨", "\U0001F464"):
            self.assertNotIn(icon, document)
        for label in ("[Extracted]", "[Human-provided]"):
            self.assertIn(label, document)
        # ``_doc()`` runs the deterministic pipeline with no LLM, so nothing in
        # this document was AI-inferred and no badge may say otherwise. (This
        # assertion used to be reversed — it required an "[AI-inferred]" badge
        # in AI-free output, which is how the mislabel went unnoticed.)
        self.assertNotIn("[AI-inferred]", document)


class MarkdownRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = render_markdown(_doc())

    def test_all_sections_present_in_order(self):
        positions = [self.md.find(t) for t in _SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_no_provenance_icons(self):
        # 5.6: Markdown carries the same plain-text badges as HTML/DOCX.
        for icon in ("⚙", "✨", "\U0001F464"):
            self.assertNotIn(icon, self.md)

    def test_every_section_has_exactly_one_provenance_badge(self):
        pills = re.findall(r"## (\d+)\. .*? \[([\w-]+)\]", self.md)
        found = {int(n) for n, _ in pills}
        self.assertEqual(found, set(range(1, 20)))
        for _, label in pills:
            self.assertIn(label, ("Extracted", "AI-inferred", "Human-provided"))


class ModelDiagramClaimConsistencyTest(unittest.TestCase):
    """P2 (retuned Day 6, now that ``_model_diagram`` ships): §18's "The
    model diagram is in section 6" (and §6's own aside in the markdown
    renderer) must only ever claim the diagram exists in lockstep with
    whether it's actually rendered — both gated on the single
    ``_shared.MODEL_DIAGRAM_RENDERED`` flag so they can never drift out of
    sync with each other or with reality, in either direction."""

    def test_sentence_present_now_that_the_diagram_renders(self):
        import pbicompass.render._shared as shared
        self.assertTrue(shared.MODEL_DIAGRAM_RENDERED, "flip this test's expectations too if disabled again")

        doc = _doc()
        self.assertIn("The model diagram is in section 6", render_html(doc))
        self.assertIn("The model diagram is in section 6", render_markdown(doc))
        with tempfile.TemporaryDirectory() as td:
            path = render_docx(doc, Path(td) / "t.docx")
            with zipfile.ZipFile(path) as zf:
                text = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("model diagram", text.lower())

    def test_sentence_absent_when_the_flag_is_disabled(self):
        import pbicompass.render._shared as shared
        import pbicompass.render.html as html_mod
        import pbicompass.render.markdown as markdown_mod

        doc = _doc()
        original = shared.MODEL_DIAGRAM_RENDERED
        try:
            shared.MODEL_DIAGRAM_RENDERED = False
            html_mod.MODEL_DIAGRAM_RENDERED = False
            markdown_mod.MODEL_DIAGRAM_RENDERED = False
            self.assertNotIn("The model diagram is in section 6", render_html(doc))
            self.assertNotIn("The model diagram is in section 6", render_markdown(doc))
            self.assertNotIn("See the HTML version for the model diagram", render_markdown(doc))
        finally:
            shared.MODEL_DIAGRAM_RENDERED = original
            html_mod.MODEL_DIAGRAM_RENDERED = original
            markdown_mod.MODEL_DIAGRAM_RENDERED = original


class TechnicalTopClusterTest(unittest.TestCase):
    """Day 8: the technical doc's §16 surfaces the sibling Audit document's
    broadest-impact root-cause cluster when the caller (cli.py/worker.py)
    passes one; omitted entirely when it doesn't (deterministic fallback,
    including every offline/no-client run)."""

    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)
        cls.cluster = FindingCluster(
            root_cause="Auto Date/Time is enabled",
            rule_ids=["PBIC-PERF-007", "PBIC-MOD-003"],
            narrative="Disabling Auto Date/Time clears this and its dependent findings.",
            confidence="High",
        )
        cls.doc_with = generate_document(cls.model, top_cluster=cls.cluster)
        cls.doc_without = generate_document(cls.model)

    def test_top_cluster_populated_on_the_document(self):
        self.assertEqual(self.doc_with.top_cluster["root_cause"], "Auto Date/Time is enabled")
        self.assertEqual(self.doc_with.top_cluster["rule_ids"], ["PBIC-PERF-007", "PBIC-MOD-003"])
        self.assertIsNone(self.doc_without.top_cluster)

    def test_markdown_callout(self):
        md = render_markdown(self.doc_with)
        self.assertIn("Root cause: Auto Date/Time is enabled", md)
        self.assertIn("Disabling Auto Date/Time clears this and its dependent findings.", md)
        self.assertIn("PBIC-PERF-007", md)
        self.assertNotIn("Root cause:", render_markdown(self.doc_without))

    def test_html_callout(self):
        html = render_html(self.doc_with)
        self.assertIn("Root cause: Auto Date/Time is enabled", html)
        self.assertIn("PBIC-PERF-007", html)
        self.assertNotIn("Root cause:", render_html(self.doc_without))

    def test_docx_callout(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(self.doc_with, Path(td) / "technical.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Root cause: Auto Date/Time is enabled", document)
            self.assertIn("PBIC-PERF-007", document)


class PandocAdapterTest(unittest.TestCase):
    def test_detection_returns_sane_types(self):
        self.assertIsInstance(pandoc.pandoc_available(), bool)
        self.assertIsInstance(pandoc.weasyprint_available(), bool)
        engine = pandoc.find_pdf_engine()
        self.assertTrue(engine is None or isinstance(engine, str))

    def test_pdf_path_matches_environment(self):
        md = render_markdown(_doc())
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.pdf"
            if pandoc.pandoc_available() and pandoc.find_pdf_engine():
                pandoc.to_pdf(md, out)
                self.assertTrue(out.exists() and out.stat().st_size > 0)
            else:
                with self.assertRaises(pandoc.PandocError):
                    pandoc.to_pdf(md, out)

    def test_docx_via_pandoc_requires_pandoc(self):
        if not pandoc.pandoc_available():
            with self.assertRaises(pandoc.PandocError):
                pandoc.to_docx("# Hi", "x.docx")

    def test_title_block_renders_yaml_metadata(self):
        # 2.8: title/author/date become a Pandoc YAML metadata block, which
        # LaTeX-family engines render as a \\maketitle cover page.
        block = pandoc._title_block("My Report", "Jane Doe", "4 July 2026")
        self.assertTrue(block.startswith("---\n"))
        self.assertIn('title: "My Report"', block)
        self.assertIn('author: "Jane Doe"', block)
        self.assertIn('date: "4 July 2026"', block)

    def test_title_block_escapes_quotes(self):
        block = pandoc._title_block('Report "v2"', None, None)
        self.assertIn('title: "Report \\"v2\\""', block)
        self.assertNotIn("author:", block)

    def test_title_block_empty_without_title(self):
        self.assertEqual(pandoc._title_block(None, "Jane", "today"), "")


class EditableHtmlTest(unittest.TestCase):
    def test_all_document_types_include_editor_and_save_support(self):
        documents = (
            render_html(_doc()),
            render_audit_html(_audit_doc()),
            render_executive_html(_executive_doc()),
            render_user_guide_html(_user_guide_doc()),
        )
        for html in documents:
            self.assertIn('id="document-content" contenteditable="false"', html)
            self.assertIn('id="edit-document"', html)
            self.assertIn('class="edit-icon edit-icon--start"', html)
            self.assertIn('class="edit-icon edit-icon--done"', html)
            self.assertNotIn('<path d="M12 20h9"', html)
            self.assertIn('id="save-document"', html)
            self.assertIn("document.documentElement.outerHTML", html)


_AUDIT_SECTION_TITLES = [
    "1. Overall Health Score",
    "2. Model Complexity",
    "3. DAX Review",
    "4. Model Best Practices",
    "5. Performance Risks",
    "6. Governance",
    "7. Unused Assets",
    "8. Recommendations",
]


class AuditMarkdownRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = render_audit_markdown(_audit_doc())

    def test_all_sections_present_in_order(self):
        positions = [self.md.find(t) for t in _AUDIT_SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_contains_health_score_and_recommendations(self):
        self.assertIn("SampleSales", self.md)
        self.assertIn("/ 100", self.md)
        self.assertIn("Why it matters", self.md)

    def test_no_root_cause_section_when_no_clusters(self):
        # Day 8: deterministic fallback is that the section is simply
        # absent, never an empty placeholder.
        self.assertNotIn("Root-Cause Analysis", self.md)


class AuditHtmlRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_audit_html(_audit_doc())

    def test_is_html_document(self):
        self.assertTrue(self.html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn("</body></html>", self.html)

    def test_all_sections_present_in_order(self):
        positions = [self.html.find(t) for t in _AUDIT_SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_no_external_network_calls(self):
        # 1.11: the shared _html_shell must not pull the Google Fonts CDN.
        for banned in ("fonts.googleapis.com", "fonts.gstatic.com"):
            self.assertNotIn(banned, self.html)

    def test_uses_shared_page_shell(self):
        # the same sidebar/TOC scaffold as the technical renderer
        self.assertIn('class="sidebar"', self.html)
        self.assertIn('class="toc-link"', self.html)

    def test_search_index_covers_findings_checks_and_recommendations(self):
        # P2: the audit doc's search index isn't sections-only — a typo in a
        # measure name (the "gaint" acceptance scenario) must be findable via
        # both the measure's own DAX finding and, when it fails a best-
        # practice check, that check too.
        self.assertIn('"type": "finding"', self.html)
        self.assertIn('"type": "recommendation"', self.html)

    def test_no_root_cause_section_when_no_clusters(self):
        self.assertNotIn("Root-Cause Analysis", self.html)
        self.assertNotIn('id="sec9"', self.html)

    def test_searching_a_measure_typo_finds_the_naming_finding(self):
        from pbicompass.schemas.model import Measure, SemanticModel, Table

        model = SemanticModel(
            report_name="R",
            tables=[Table(name="Sales", measures=[
                Measure(name="GaintCustomers", expression="COUNTROWS(Sales)", table="Sales"),
            ])],
        )
        doc = AuditReportGenerator.generate(model)
        html = render_audit_html(doc)
        index_json = html.split('id="search-index">', 1)[1].split("</script>", 1)[0]
        self.assertIn("GaintCustomers", index_json)
        naming_finding = next(f for f in doc.dax_findings if f.kind == "naming_issue")
        self.assertIn(naming_finding.measure, index_json)


class AuditDocxRenderTest(unittest.TestCase):
    def test_valid_ooxml_package(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_audit_docx(_audit_doc(), Path(td) / "audit.docx")
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertEqual(
                    names,
                    {"[Content_Types].xml", "_rels/.rels",
                     "word/_rels/document.xml.rels", "word/styles.xml",
                     "word/document.xml"},
                )
                document = zf.read("word/document.xml").decode("utf-8")
            minidom.parseString(document)  # well-formed XML or raises
            self.assertIn("SampleSales", document)
            self.assertIn("Overall Health Score", document)


class AuditRootCauseSectionTest(unittest.TestCase):
    """Day 8: renders the Root-Cause Analysis section from the Day-7 Audit
    Synthesizer clusters, deep-linking each cluster's rule_ids to the
    finding anchor that carries that rule ID."""

    @classmethod
    def setUpClass(cls):
        cls.doc, cls.real_rule_id = _audit_doc_with_clusters()
        cls.md = render_audit_markdown(cls.doc)
        cls.html = render_audit_html(cls.doc)

    def test_markdown_contains_the_section_and_narrative(self):
        self.assertIn("9. Root-Cause Analysis", self.md)
        self.assertIn("Fixing the top root cause clears the largest share of findings.", self.md)
        self.assertIn("Auto Date/Time is enabled", self.md)
        self.assertIn("Disabling Auto Date/Time clears this and its dependent findings.", self.md)
        self.assertIn(self.real_rule_id, self.md)
        self.assertIn("PBIC-DOES-NOT-EXIST", self.md)

    def test_html_section_and_toc_present(self):
        self.assertIn('<h2 id="sec9">9. Root-Cause Analysis</h2>', self.html)
        self.assertIn(">Root-Cause Analysis<", self.html)  # sidebar TOC entry
        self.assertIn("Fixing the top root cause clears the largest share of findings.", self.html)

    def test_html_resolved_rule_id_becomes_a_working_anchor_link(self):
        # the real rule ID must resolve to an <a href="#..."> pointing at
        # the finding/check that actually carries it — not just be printed.
        self.assertRegex(self.html, rf'<a href="#[\w-]+">{re.escape(self.real_rule_id)} — ')

    def test_html_unresolved_rule_id_falls_back_to_plain_text(self):
        # a cluster rule_id with no matching finding anywhere on the
        # document must never render as a dead link.
        self.assertIn("<code>PBIC-DOES-NOT-EXIST</code>", self.html)
        self.assertNotIn('href="#PBIC-DOES-NOT-EXIST"', self.html)

    def test_docx_contains_the_section(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_audit_docx(self.doc, Path(td) / "audit.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Root-Cause Analysis", document)
            self.assertIn("Auto Date/Time is enabled", document)
            self.assertIn(self.real_rule_id, document)


class TopClusterSelectionTest(unittest.TestCase):
    """The helper cli.py/worker.py use (Day 8) to pick which cluster gets
    surfaced onto the technical doc's §16 — the broadest-impact one (most
    related findings), not just ``clusters[0]``."""

    def test_picks_the_cluster_with_the_most_rule_ids(self):
        from pbicompass.render.audit import _top_cluster

        doc, _ = _audit_doc_with_clusters()
        doc.clusters = [
            FindingCluster(root_cause="Narrow cause", rule_ids=["A"], confidence="Low"),
            FindingCluster(root_cause="Broad cause", rule_ids=["A", "B", "C"], confidence="Medium"),
        ]
        top = _top_cluster(doc)
        self.assertEqual(top.root_cause, "Broad cause")

    def test_none_when_no_clusters(self):
        from pbicompass.render.audit import _top_cluster

        doc, _ = _audit_doc_with_clusters()
        doc.clusters = []
        self.assertIsNone(_top_cluster(doc))


class RendererRegistryCompatibilityTest(unittest.TestCase):
    """Every document type's renderer set must expose the same md/html/docx
    callable shape, and the technical entry must be the exact same function
    objects exported at the top level (no behavioral drift via indirection)."""

    def test_registry_shape(self):
        for doc_type, renderers in render_registry.RENDERERS.items():
            with self.subTest(doc_type=doc_type):
                self.assertEqual(set(renderers), {"md", "html", "docx"})

    def test_technical_entry_matches_top_level_exports(self):
        self.assertIs(render_registry.RENDERERS["technical"]["md"], render_markdown)
        self.assertIs(render_registry.RENDERERS["technical"]["html"], render_html)
        self.assertIs(render_registry.RENDERERS["technical"]["docx"], render_docx)

    def test_markdown_text_helper_works_for_every_type(self):
        doc_by_type = {
            "technical": _doc(), "audit": _audit_doc(), "executive": _executive_doc(),
            "user-guide": _user_guide_doc(),
        }
        for doc_type, doc in doc_by_type.items():
            with self.subTest(doc_type=doc_type):
                text = render_registry.markdown_text(doc_type, doc)
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip())


_EXECUTIVE_SECTION_TITLES = [
    "1. Purpose & Value",
    "2. Key KPIs",
    "3. Top Risks & Recommended Actions",
    "4. Data & Refresh at a Glance",
    "5. Ownership & Accountability",
    "6. What's Next",
]


class ExecutiveMarkdownRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = render_executive_markdown(_executive_doc())

    def test_all_sections_present_in_order(self):
        positions = [self.md.find(t) for t in _EXECUTIVE_SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_no_technical_jargon(self):
        for banned in ("DAX", "semantic model", "CROSSFILTER", "USERELATIONSHIP"):
            self.assertNotIn(banned, self.md)

    def test_no_raw_file_paths(self):
        self.assertNotRegex(self.md, r"[A-Za-z]:[\\/]")

    def test_contains_content(self):
        self.assertIn("SampleSales", self.md)

    def test_unset_steward_and_classification_rows_are_omitted(self):
        # D1: empty "Steward: not specified" / "Classification: not
        # specified" rows read as noise nobody asked for — omit the row
        # entirely rather than showing a placeholder, when unset.
        doc = _executive_doc()
        self.assertIsNone(doc.steward)
        self.assertIsNone(doc.classification)
        md = render_executive_markdown(doc)
        self.assertNotIn("| Steward |", md)
        self.assertNotIn("| Classification |", md)
        self.assertIn("| Owner |", md)


class ExecutiveHtmlRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_executive_html(_executive_doc())

    def test_is_html_document(self):
        self.assertTrue(self.html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn("</body></html>", self.html)

    def test_all_sections_present_in_order(self):
        # check by anchor id, not literal title text — titles containing "&"
        # are HTML-escaped to "&amp;" in the rendered output, so a literal
        # substring search would spuriously fail on those sections.
        anchor_ids = [f'id="sec{i}"' for i in range(1, len(_EXECUTIVE_SECTION_TITLES) + 1)]
        positions = [self.html.find(a) for a in anchor_ids]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_uses_shared_page_shell(self):
        self.assertIn('class="sidebar"', self.html)
        self.assertIn('class="toc-link"', self.html)

    def test_risk_deep_links_to_specific_audit_finding(self):
        # I5: a risk with a rule_id must link to that exact recommendation
        # anchor in the audit doc, not a generic section-level link.
        doc = _executive_doc()
        self.assertTrue(doc.top_risks and doc.top_risks[0].rule_id)
        html = render_executive_html(doc, sibling_hrefs={"audit": "audit.html"})
        self.assertIn(f'audit.html#rec-{doc.top_risks[0].rule_id}', html)

    def test_unset_steward_and_classification_rows_are_omitted(self):
        doc = _executive_doc()
        html = render_executive_html(doc)
        self.assertNotIn("<strong>Steward:</strong>", html)
        self.assertNotIn("<strong>Classification:</strong>", html)
        self.assertIn("<strong>Owner:</strong>", html)


class ExecutiveDocxRenderTest(unittest.TestCase):
    def test_valid_ooxml_package(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_executive_docx(_executive_doc(), Path(td) / "executive.docx")
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertEqual(
                    names,
                    {"[Content_Types].xml", "_rels/.rels",
                     "word/_rels/document.xml.rels", "word/styles.xml",
                     "word/document.xml"},
                )
                document = zf.read("word/document.xml").decode("utf-8")
            minidom.parseString(document)  # well-formed XML or raises
            self.assertIn("SampleSales", document)
            self.assertIn("Purpose", document)


_USER_GUIDE_SECTION_TITLES = [
    "1. Introduction",
    "2. Getting Started",
    "3. Report Pages",
    "4. Glossary of Business Terms",
]


class UserGuideMarkdownRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = render_user_guide_markdown(_user_guide_doc())

    def test_all_sections_present_in_order(self):
        positions = [self.md.find(t) for t in _USER_GUIDE_SECTION_TITLES]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_contains_content(self):
        self.assertIn("SampleSales", self.md)
        self.assertIn("Sales Overview", self.md)
        self.assertNotIn("Data Quality", self.md)  # hidden page excluded

    def test_bookmarks_and_tooltips_subsections_omitted_when_empty(self):
        # today's model.json never populates bookmarks/tooltips — the
        # renderer must omit the subsection, not print a misleading "None"
        self.assertNotIn("Saved views", self.md)
        self.assertNotIn("Hover for more detail", self.md)


class UserGuideHtmlRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = render_user_guide_html(_user_guide_doc())

    def test_is_html_document(self):
        self.assertTrue(self.html.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn("</body></html>", self.html)

    def test_all_sections_present_in_order(self):
        anchor_ids = [f'id="sec{i}"' for i in range(1, len(_USER_GUIDE_SECTION_TITLES) + 1)]
        positions = [self.html.find(a) for a in anchor_ids]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))

    def test_uses_shared_page_shell(self):
        self.assertIn('class="sidebar"', self.html)
        self.assertIn('class="toc-link"', self.html)


class UserGuideDocxRenderTest(unittest.TestCase):
    def test_valid_ooxml_package(self):
        with tempfile.TemporaryDirectory() as td:
            out = render_user_guide_docx(_user_guide_doc(), Path(td) / "guide.docx")
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertEqual(
                    names,
                    {"[Content_Types].xml", "_rels/.rels",
                     "word/_rels/document.xml.rels", "word/styles.xml",
                     "word/document.xml"},
                )
                document = zf.read("word/document.xml").decode("utf-8")
            minidom.parseString(document)  # well-formed XML or raises
            self.assertIn("SampleSales", document)
            self.assertIn("Sales Overview", document)


class IdUniquenessTest(unittest.TestCase):
    """I2: two distinct object names (e.g. glossary terms "Var LE1" and
    "Var LE1 %") can collapse to the same anchor slug once symbols are
    stripped — a duplicate ``id="..."`` breaks in-page links and search.
    Every id attribute in every rendered HTML document must be unique."""

    _ID_RE = None

    @classmethod
    def setUpClass(cls):
        import re
        cls._ID_RE = re.compile(r'\bid="([^"]*)"')

    def _assert_unique_ids(self, html: str, label: str) -> None:
        ids = self._ID_RE.findall(html)
        seen = set()
        dupes = set()
        for i in ids:
            (dupes if i in seen else seen).add(i)
        self.assertEqual(dupes, set(), f"{label}: duplicate id attribute(s): {sorted(dupes)}")

    def test_technical_html_has_unique_ids(self):
        self._assert_unique_ids(render_html(_doc()), "technical")

    def test_audit_html_has_unique_ids(self):
        self._assert_unique_ids(render_audit_html(_audit_doc()), "audit")

    def test_executive_html_has_unique_ids(self):
        self._assert_unique_ids(render_executive_html(_executive_doc()), "executive")

    def test_user_guide_html_has_unique_ids(self):
        self._assert_unique_ids(render_user_guide_html(_user_guide_doc()), "user_guide")

    def test_glossary_terms_that_collapse_to_the_same_slug_get_unique_ids(self):
        """The concrete I2 bug report: 'Var LE1' and 'Var LE1 %' both slug to
        'var-le1' once the slugifier drops '%'."""
        from pbicompass.schemas.user_guide_document import GlossaryTerm, UserGuideDocument
        from pbicompass.render import render_user_guide_html as _render

        base = _user_guide_doc()
        doc = UserGuideDocument(
            metadata=base.metadata, introduction=base.introduction,
            getting_started=base.getting_started, pages=base.pages,
            glossary=[
                GlossaryTerm(term="Var LE1", plain_definition="First variance measure."),
                GlossaryTerm(term="Var LE1 %", plain_definition="Variance as a percentage."),
                GlossaryTerm(term="Var LE1 #", plain_definition="A third colliding term."),
            ],
        )
        html = _render(doc)
        self._assert_unique_ids(html, "user_guide (glossary collision fixture)")
        self.assertIn('id="term-var-le1"', html)
        self.assertIn('id="term-var-le1-2"', html)
        self.assertIn('id="term-var-le1-3"', html)


class WireframeHrefResolutionTest(unittest.TestCase):
    """I3: every ``href="#..."`` a page wireframe emits must resolve to a
    real id somewhere in the same document — the SampleSales fixture has
    slicers, data visuals, and a decomposition-tree/map/card mix with real
    layout coordinates, so its wireframes exercise every link category."""

    _ID_RE = re.compile(r'\bid="([^"]*)"')
    _HREF_RE = re.compile(r'href="#([^"]*)"')

    def _assert_no_dead_hrefs(self, html: str, label: str) -> None:
        ids = set(self._ID_RE.findall(html))
        # exclude JS template-literal hrefs from the shell's scroll-spy
        # script (e.g. href="#${sections[index].id}") — not real markup.
        hrefs = {h for h in self._HREF_RE.findall(html) if h and "$" not in h}
        dead = hrefs - ids
        self.assertEqual(dead, set(), f"{label}: dead href(s) with no matching id: {sorted(dead)}")

    def test_technical_html_wireframe_hrefs_all_resolve(self):
        self._assert_no_dead_hrefs(render_html(_doc()), "technical")

    def test_user_guide_html_wireframe_hrefs_all_resolve(self):
        html = render_user_guide_html(_user_guide_doc())
        self._assert_no_dead_hrefs(html, "user_guide")
        self.assertIn('<details class="diagram-reveal"><summary><span>View wireframe</span></summary>', html)

    def test_grouped_duplicate_visuals_produce_no_dead_hrefs_end_to_end(self):
        """SampleSales has no duplicate/slug-colliding visuals, so it never
        exercised report_pages()'s "Label — Type ×N" relabeling — the one
        case that actually broke the wireframe's own links (Day 13). Proves
        it through the real html.py path, not just report_pages() directly."""
        from pbicompass.schemas.model import Measure, Page, SemanticModel, Table, Visual

        table = Table(name="Sales", measures=[Measure(name="Sale_Value", expression="SUM(Sales[Amount])", table="Sales")])
        page = Page(
            id="p1", display_name="Overview",
            visuals=[Visual(id=f"v{i}", type="card", fields=["Sales.Sale_Value"],
                            x=i * 100, y=0, z=0, width=90, height=70) for i in range(3)],
        )
        model = SemanticModel(report_name="Dup", tables=[table], pages=[page])
        html = render_html(generate_document(model))
        self.assertIn("×3", html)  # confirms the grouping this test targets actually fired
        self._assert_no_dead_hrefs(html, "technical (duplicate-visual fixture)")


class SectionProvenanceHonestyTest(unittest.TestCase):
    """Benchmark check X1: a section pill must match where its prose actually
    came from. The pills used to be a static per-section map, so an offline run
    still stamped §2 "AI-inferred" over deterministic template text, and §16
    claimed AI in every mode while saying "not an AI guess" in its own body."""

    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)

    def _pill(self, html, section_num):
        m = re.search(rf'<h2 id="sec{section_num}">.*?<span class="pill [\w-]+">([\w-]+)</span>', html)
        self.assertIsNotNone(m, f"no provenance pill on section {section_num}")
        return m.group(1)

    def test_offline_run_does_not_claim_ai_wrote_the_summary(self):
        """The reported bug: deterministic engine, AI-inferred pill."""
        html = render_html(generate_document(self.model))
        self.assertEqual(self._pill(html, 2), "Extracted")

    def test_ai_run_does_claim_ai(self):
        """The pill must still earn "AI-inferred" when the LLM really wrote it,
        or the fix would just be a blanket downgrade."""
        html = render_html(generate_document(self.model, _FakeBusinessAnalyst(), on_warning=lambda m: None))
        self.assertEqual(self._pill(html, 2), "AI-inferred")

    def test_ai_that_fails_every_batch_does_not_claim_ai(self):
        """Graceful degradation leaves deterministic text behind; the pill has
        to degrade with it rather than advertise an LLM that never answered."""
        html = render_html(generate_document(self.model, _DeadLLM(), on_warning=lambda m: None))
        self.assertEqual(self._pill(html, 2), "Extracted")

    def test_health_section_never_claims_ai(self):
        """§16 is scored and written by the deterministic rule engine in every
        mode, despite its "AI Recommendations" title."""
        for label, client in (("offline", None), ("ai", _FakeBusinessAnalyst())):
            with self.subTest(mode=label):
                html = render_html(generate_document(self.model, client, on_warning=lambda m: None))
                self.assertEqual(self._pill(html, 16), "Extracted")


class MethodologyDisclosureTest(unittest.TestCase):
    """§19's "AI Agents Used" was a fixed string naming Anthropic Claude, Google
    Gemini and Cohere on every document — false offline (nothing was called) and
    false live (one model is called, not three vendors). It must describe the run."""

    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)

    def _disclosure(self, html):
        m = re.search(r"<strong>AI Agents Used:</strong>([^<]+)</p>", html)
        self.assertIsNotNone(m, "§19 AI disclosure missing")
        return m.group(1).strip()

    def test_offline_names_no_model(self):
        text = self._disclosure(render_html(generate_document(self.model)))
        self.assertIn("No AI model contributed", text)
        for vendor in ("Anthropic", "Claude", "Gemini", "Cohere", "OpenAI"):
            self.assertNotIn(vendor, text, f"offline output must not name {vendor}")

    def test_live_names_only_the_model_actually_called(self):
        client = _FakeBusinessAnalyst()
        client.model = "test-model-xyz"
        text = self._disclosure(render_html(generate_document(self.model, client, on_warning=lambda m: None)))
        self.assertIn("test-model-xyz", text)
        # The old stock sentence's vendors must not reappear alongside it.
        for vendor in ("Gemini", "Cohere"):
            self.assertNotIn(vendor, text)

    def test_engine_version_tracks_the_package(self):
        """The version was hardcoded "v0.1.0"; it must follow __version__ so a
        release can't silently ship a document claiming the previous engine."""
        from pbicompass import __version__

        text = self._disclosure(render_html(generate_document(self.model)))
        self.assertIn(f"v{__version__}", text)

    def test_all_renderers_agree(self):
        doc = generate_document(self.model)
        html_text = self._disclosure(render_html(doc))
        self.assertIn("No AI model contributed", render_markdown(doc))
        with tempfile.TemporaryDirectory() as td:
            out = render_docx(doc, Path(td) / "r.docx")
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
        self.assertIn("No AI model contributed", document)
        self.assertNotIn("Cohere", document)
        self.assertTrue(html_text)


class _FakeBusinessAnalyst:
    """Minimal client that answers the Business Analyst prompt and nothing else."""

    model = "fake"

    def complete_json(self, system, user, schema, *, effort=None):
        if "Business Analyst" in system or "BI consultant" in system:
            payload = json.loads(user)
            return {
                "core_purpose": "AI_WRITTEN_PURPOSE",
                "pages": [{
                    "page_title": p.get("display_name", ""), "summary": "AI page summary.",
                    "users": "Analysts", "business_questions": ["Q?"],
                    "decisions": "A decision.", "confidence": "High",
                } for p in payload.get("pages", [])],
                "navigation_guide": ["nav"],
                "complex_visual_explainers": [],
            }
        raise RuntimeError("not the agent under test")


class _DeadLLM:
    """Present but every call fails — the live credit-exhaustion / outage case."""

    model = "dead"

    def complete_json(self, system, user, schema, *, effort=None):
        raise RuntimeError("provider unavailable")


class ShellScriptSyntaxTest(unittest.TestCase):
    """The shell's JS is one inline block: a single syntax error anywhere in it
    costs the reader *every* interactive feature at once — edit/save, search,
    TOC scrollspy, theme toggle, diagram pan/zoom — and does it silently, in the
    browser, where no Python test looks. It shipped broken exactly that way: the
    ``_SCRIPT`` heredoc wasn't raw, so Python ate the ``\\n`` in a JS string
    literal and left it unterminated. The golden snapshots pinned the breakage
    rather than catching it, since a byte-comparison can't tell valid JS from
    invalid."""

    def _scripts(self):
        from pbicompass.render._html_shell import _SCRIPT, _THEME_INIT_SCRIPT

        for name, block in (("_SCRIPT", _SCRIPT), ("_THEME_INIT_SCRIPT", _THEME_INIT_SCRIPT)):
            yield name, block.replace("<script>", "").replace("</script>", "")

    def test_no_js_string_literal_spans_a_line_break(self):
        """Guards the specific regression with no external dependency."""
        from pbicompass.render._html_shell import _SCRIPT

        self.assertIn(
            "'<!doctype html>" + chr(92) + "n'",
            _SCRIPT,
            "the newline in the Save HTML doctype literal must reach the browser as a JS "
            "escape; a real line break here is an unterminated string literal",
        )

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_inline_script_parses(self):
        """Full parse of every shell script block, when a JS engine is around."""
        for name, js in self._scripts():
            with self.subTest(script=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "block.js"
                path.write_text(js, encoding="utf-8")
                proc = subprocess.run(
                    [shutil.which("node"), "--check", str(path)],
                    capture_output=True, text=True,
                )
                self.assertEqual(proc.returncode, 0, f"{name} is not valid JavaScript:\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
