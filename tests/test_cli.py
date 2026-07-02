"""Phase 1 tests: the CLI ``generate`` subcommand's ``--document`` flag.

Runs ``pbicompass.cli.main()`` in-process (no subprocess) so these tests are fast
and need no installed console script.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from pbicompass import cli
from pbicompass.agents.generators import DOCUMENT_TYPES

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+\+00:00")


def _without_timestamps(text: str) -> str:
    """Each ``pbicompass generate`` invocation re-parses the fixture, so
    ``meta.generated_at`` legitimately differs run to run — strip it before
    comparing two generations for structural equality."""
    return _TIMESTAMP.sub("<TS>", text)


class DocumentFlagTest(unittest.TestCase):
    def test_default_document_type_is_technical(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.md"
            code = cli.main(["generate", str(FIXTURE), "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Power BI Documentation", text)
            self.assertIn("## 7. Measures & Calculations (DAX Dictionary)", text)

    def test_explicit_technical_matches_default(self):
        with tempfile.TemporaryDirectory() as td:
            default_out = Path(td) / "default.md"
            explicit_out = Path(td) / "explicit.md"
            cli.main(["generate", str(FIXTURE), "-o", str(default_out), "--quiet"])
            cli.main(["generate", str(FIXTURE), "--document", "technical",
                     "-o", str(explicit_out), "--quiet"])
            self.assertEqual(_without_timestamps(default_out.read_text(encoding="utf-8")),
                            _without_timestamps(explicit_out.read_text(encoding="utf-8")))

    def test_document_audit_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "audit.md"
            code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Audit & Health Report", text)
            self.assertIn("Overall Health Score", text)
            self.assertIn("Recommendations", text)

    def test_document_audit_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "audit.json"
            code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["document_type"], "audit")
            self.assertIn("health", data)
            self.assertIn("recommendations", data)

    def test_document_audit_docx(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "audit.docx"
            code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists() and out.stat().st_size > 0)

    def test_document_executive_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "executive.md"
            code = cli.main(["generate", str(FIXTURE), "--document", "executive",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Executive Summary", text)
            self.assertIn("Business Purpose", text)
            self.assertIn("Future Recommendations", text)

    def test_document_executive_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "executive.json"
            code = cli.main(["generate", str(FIXTURE), "--document", "executive",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["document_type"], "executive")
            self.assertIn("business_purpose", data)
            self.assertIn("future_recommendations", data)

    def test_document_executive_docx(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "executive.docx"
            code = cli.main(["generate", str(FIXTURE), "--document", "executive",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists() and out.stat().st_size > 0)

    def test_document_user_guide_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "guide.md"
            code = cli.main(["generate", str(FIXTURE), "--document", "user-guide",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Business User Guide", text)
            self.assertIn("Getting Started", text)
            self.assertIn("Glossary of Business Terms", text)

    def test_document_user_guide_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "guide.json"
            code = cli.main(["generate", str(FIXTURE), "--document", "user-guide",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["document_type"], "user-guide")
            self.assertIn("introduction", data)
            self.assertIn("glossary", data)

    def test_document_user_guide_docx(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "guide.docx"
            code = cli.main(["generate", str(FIXTURE), "--document", "user-guide",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists() and out.stat().st_size > 0)

    def test_invalid_document_choice_rejected(self):
        with self.assertRaises(SystemExit):
            cli.main(["generate", str(FIXTURE), "--document", "not-a-real-type"])


class DocumentAllTest(unittest.TestCase):
    """``--document all`` generates every registered document type from one parse."""

    def test_all_creates_one_file_per_type(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.md"
            code = cli.main(["generate", str(FIXTURE), "--document", "all",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            for dtype in DOCUMENT_TYPES:
                per_type = out.with_name(f"report.{dtype}.md")
                self.assertTrue(per_type.exists(), f"missing output for '{dtype}'")
                self.assertGreater(per_type.stat().st_size, 0)
            self.assertFalse(out.exists())  # the bare "report.md" is never written in multi-mode

    def test_all_docx_creates_valid_files_per_type(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.docx"
            code = cli.main(["generate", str(FIXTURE), "--document", "all", "--format", "docx",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            for dtype in DOCUMENT_TYPES:
                per_type = out.with_name(f"report.{dtype}.docx")
                self.assertTrue(per_type.exists())
                self.assertGreater(per_type.stat().st_size, 0)

    def test_all_stdout_prints_a_header_per_type(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["generate", str(FIXTURE), "--document", "all", "--quiet"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        for dtype in DOCUMENT_TYPES:
            self.assertIn(f"=== {dtype.upper()} ===", out)

    def test_all_single_document_type_registry_unaffected(self):
        # "all" must not change behavior when only one document type is registered
        # to look up in the CLI's own choices list -- this just guards that "all"
        # expands to every currently-registered type, not a hardcoded list.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.json"
            cli.main(["generate", str(FIXTURE), "--document", "all", "-o", str(out), "--quiet"])
            produced = {p.name for p in Path(td).glob("report.*.json")}
            expected = {f"report.{dtype}.json" for dtype in DOCUMENT_TYPES}
            self.assertEqual(produced, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
