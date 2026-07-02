"""Phase 3 tests: HTML, DOCX, and the Pandoc PDF adapter.

The pure-Python renderers (HTML, DOCX) run fully here. The Pandoc adapter is
tested for whichever path this environment supports — real conversion if Pandoc
is installed, graceful ``PandocError`` if not.
"""

from __future__ import annotations

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
    "17. Appendix",
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
        # the 'Total Revenue' DAX contains <> and quotes; they must be escaped
        self.assertIn("&lt;&gt;", self.html)
        self.assertNotIn("<>", self.html)

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
        
        # 2. Inferred requirements
        self.assertTrue(len(doc.inferred_requirements) >= 3)
        self.assertIn("REQ-01", html)
        self.assertIn("REQ-01", md_txt)
        
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

    def test_uses_shared_page_shell(self):
        # the same sidebar/TOC scaffold as the technical renderer
        self.assertIn('class="sidebar"', self.html)
        self.assertIn('class="toc-link"', self.html)


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
    "1. Business Purpose",
    "2. Key KPIs",
    "3. Data Sources",
    "4. Refresh Schedule",
    "5. Security Overview",
    "6. High-Level Architecture",
    "7. Model & Report Statistics",
    "8. Business Value",
    "9. Known Risks",
    "10. Dependencies",
    "11. Maintenance Overview",
    "12. Future Recommendations",
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
        for banned in ("DAX", "semantic model"):
            self.assertNotIn(banned, self.md)

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
            self.assertIn("Business Purpose", document)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
