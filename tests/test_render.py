"""Phase 3 tests: HTML, DOCX, and the Pandoc PDF adapter.

The pure-Python renderers (HTML, DOCX) run fully here. The Pandoc adapter is
tested for whichever path this environment supports — real conversion if Pandoc
is installed, graceful ``PandocError`` if not.
"""

from __future__ import annotations

import re
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


def _executive_doc():
    return ExecutiveSummaryGenerator.generate(detect_and_parse(FIXTURE))


def _user_guide_doc():
    return BusinessGuideGenerator.generate(detect_and_parse(FIXTURE))


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
        for label in ("[Extracted]", "[AI-inferred]", "[Human-provided]"):
            self.assertIn(label, document)


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


class PandocAdapterTest(unittest.TestCase):
    def test_detection_returns_sane_types(self):
        self.assertIsInstance(pandoc.pandoc_available(), bool)
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
        self._assert_no_dead_hrefs(render_user_guide_html(_user_guide_doc()), "user_guide")


if __name__ == "__main__":
    unittest.main(verbosity=2)
