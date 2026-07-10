"""F.3: golden-file HTML snapshots for all four document-type renderers.

Byte-exact comparisons of the deterministic SampleSales output (timestamps
normalized) against a committed snapshot in ``tests/fixtures/golden/``. The
point isn't that these files must never change — Phase 2 intentionally
changes HTML on almost every item — it's that every change to the shared
shell or a renderer becomes a reviewable diff instead of a silent one,
before/after A2-2 and every Phase-2 item (per the plan's F.3 test strategy).

To intentionally update a snapshot after a real change, rerun with
``PBICOMPASS_UPDATE_GOLDEN=1`` set, then inspect the diff under
``tests/fixtures/golden/`` before committing it.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import AuditReportGenerator, BusinessGuideGenerator, ExecutiveSummaryGenerator
from pbicompass.parsers import detect_and_parse
from pbicompass.render import render_audit_html, render_executive_html, render_html, render_user_guide_html

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"

_TIMESTAMP_RE = re.compile(r"\d{1,2} [A-Z][a-z]+ \d{4}, \d{2}:\d{2} ?[A-Za-z]*")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _normalize(html: str) -> str:
    """Replace the non-deterministic bits of the output — the render
    timestamp and any bare ISO date (e.g. the technical doc's sign-off-table
    "generated" date, derived from `today()` at render time) — with a fixed
    placeholder so the snapshot is stable run to run and doesn't drift every
    time midnight passes between when the golden was captured and when the
    suite is next run."""
    html = _TIMESTAMP_RE.sub("TIMESTAMP", html)
    return _ISO_DATE_RE.sub("ISODATE", html)


def _model():
    return detect_and_parse(FIXTURE)


class GoldenHtmlSnapshotTest(unittest.TestCase):
    def _check(self, name: str, html: str) -> None:
        normalized = _normalize(html)
        golden_path = GOLDEN_DIR / f"{name}.html"

        if os.environ.get("PBICOMPASS_UPDATE_GOLDEN") or not golden_path.exists():
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(normalized, encoding="utf-8")
            return

        expected = golden_path.read_text(encoding="utf-8")
        self.assertEqual(
            normalized, expected,
            f"{name}.html changed — if intentional, rerun with "
            f"PBICOMPASS_UPDATE_GOLDEN=1 and review the diff before committing.",
        )

    def test_technical_html_matches_snapshot(self):
        self._check("technical", render_html(generate_document(_model())))

    def test_audit_html_matches_snapshot(self):
        self._check("audit", render_audit_html(AuditReportGenerator.generate(_model())))

    def test_executive_html_matches_snapshot(self):
        self._check("executive", render_executive_html(ExecutiveSummaryGenerator.generate(_model())))

    def test_user_guide_html_matches_snapshot(self):
        self._check("user_guide", render_user_guide_html(BusinessGuideGenerator.generate(_model())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
