from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import (
    AuditReportGenerator, BusinessGuideGenerator, ExecutiveSummaryGenerator,
)
from pbicompass.agents.output_gate import (
    OutputQualityError, canonicalize_bundle, validate_bundle,
)
from pbicompass.parsers import detect_and_parse


FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


class OfflineFallbackShipsTest(unittest.TestCase):
    """The deterministic fallback must produce documents. It didn't.

    The user guide reused the Business Analyst's page summary verbatim, so its
    page prose was byte-identical to the technical document's — which the gate
    correctly rejects as duplicated narrative, blocking the entire bundle. An
    offline run (`--provider none`) therefore yielded ZERO documents, breaking
    the project's core "an LLM failure never fails a job" guarantee.

    1074 tests missed it because they all run against SampleSales, whose
    normalized page prose is 106-113 chars — under the gate's 120-char duplicate
    threshold by seven characters. Corporate Spend's is 135-152, so it fires.
    This test therefore uses Corporate Spend deliberately: a fixture that
    actually reaches the threshold.
    """

    CS = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"

    def _docs(self, model):
        from pbicompass.agents.generators import DOCUMENT_TYPES
        return {d: g.generate(model, None, owner="BI Team") for d, g in DOCUMENT_TYPES.items()}

    def _model(self):
        from pbicompass.schemas.model import SemanticModel
        return SemanticModel.from_json(self.CS.read_text(encoding="utf-8"))

    def test_deterministic_bundle_passes_the_gate(self):
        model = self._model()
        validate_bundle(self._docs(model), model)  # raises OutputQualityError on regression

    def test_user_guide_page_prose_differs_from_the_technical_doc(self):
        docs = self._docs(self._model())
        tech_summaries = {
            re.sub(r"\W+", " ", p.summary.casefold()).strip()
            for p in docs["technical"].executive_summary.pages
        }
        for page in docs["user-guide"].pages:
            normalized = re.sub(r"\W+", " ", page.purpose.casefold()).strip()
            self.assertNotIn(normalized, tech_summaries,
                             f"user-guide page '{page.page_title}' reuses the technical summary")

    def test_user_guide_purpose_is_business_facing(self):
        """The technical summary it used to copy is a visual inventory
        ("Presents 5 visuals - 2 cards, 1 bar chart"); a business guide must not
        talk that way."""
        for page in self._docs(self._model())["user-guide"].pages:
            self.assertNotIn("presents", page.purpose.casefold())
            self.assertNotIn("visuals —", page.purpose.casefold())


class SelfContradictingAskTest(unittest.TestCase):
    """The gate scored a live bundle 59/61 while shipping a risk that told the
    reader to review the memberships of roles the same sentence said didn't
    exist. Every structural check passed — none read for sense. Both strings
    below are verbatim from real live runs, before and after the fix."""

    def _docs(self, consequence: str, ask: str):
        from types import SimpleNamespace
        return {"executive": SimpleNamespace(
            top_risks=[SimpleNamespace(consequence=consequence, ask=ask)])}

    def _check(self, consequence: str, ask: str):
        from pbicompass.agents.output_gate import _self_contradicting_ask_issues
        return _self_contradicting_ask_issues(self._docs(consequence, ask))

    def test_catches_the_real_defect_that_shipped(self):
        issues = self._check(
            "Since row-level security is not configured, there is no risk of role "
            "misalignment, but all report viewers have unrestricted access to spend data.",
            "Review RLS role memberships quarterly and adjust as departments change.")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].check_id, "SENSE")
        self.assertIn("contradicts itself", issues[0].detail)

    def test_the_fixed_live_output_is_not_flagged(self):
        """Verbatim from the run after the PBIC-GOV-011 split. 'verify' is a
        legitimate ask when an intake note claims RLS exists but the model has
        none, and the consequence is hedged rather than a flat absence claim."""
        self.assertEqual(self._check(
            "Without row-level security (if needed), department heads could see each "
            "other's spend data, risking confidentiality.",
            "Ask your BI team to verify that RLS roles are correctly applied and that "
            "all users have appropriate access, per the intended security setup."), [])

    def test_other_concepts_are_covered(self):
        self.assertTrue(self._check("No relationships are defined in this model.",
                                    "Review relationships quarterly to keep them accurate."))
        self.assertTrue(self._check("Descriptions are not configured for most measures.",
                                    "Maintain descriptions as the model evolves."))

    def test_create_verbs_are_the_correct_response_to_absence(self):
        for ask in ("Define RLS roles for any data that should be restricted, and assign members.",
                    "Confirm that unrestricted access is intended.",
                    "Create relationships between the fact and dimension tables."):
            self.assertEqual(
                self._check("Row-level security is not configured for this report.", ask), [],
                ask)

    def test_maintenance_ask_is_fine_when_the_thing_exists(self):
        """PBIC-GOV-001's real case: roles DO exist but lack members, so asking
        to review memberships is correct and must not be flagged."""
        self.assertEqual(self._check("Some roles have no members assigned.",
                                     "Review RLS role memberships quarterly."), [])

    def test_missing_fields_are_ignored(self):
        self.assertEqual(self._check("", "Review RLS role memberships."), [])
        self.assertEqual(self._check("RLS is not configured.", ""), [])


class OutputGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)
        cls.docs = {
            "technical": generate_document(cls.model),
            "audit": AuditReportGenerator.generate(cls.model),
            "executive": ExecutiveSummaryGenerator.generate(cls.model),
            "user-guide": BusinessGuideGenerator.generate(cls.model),
        }

    def test_valid_full_bundle_passes(self):
        rendered = validate_bundle(copy.deepcopy(self.docs), self.model)
        self.assertEqual(set(rendered), set(self.docs))

    def test_canonicalizes_model_object_spelling(self):
        docs = copy.deepcopy(self.docs)
        expected = docs["technical"].measure_catalog.measures[0].name
        docs["technical"].measure_catalog.measures[0].name = expected.swapcase()
        canonicalize_bundle(docs, self.model)
        self.assertEqual(docs["technical"].measure_catalog.measures[0].name, expected)

    def test_blocks_raw_placeholder(self):
        docs = copy.deepcopy(self.docs)
        docs["executive"].purpose = "TODO replace this paragraph"
        with self.assertRaisesRegex(OutputQualityError, "PLACEHOLDER"):
            validate_bundle(docs, self.model)

    def test_blocks_cross_document_duplicate_paragraph(self):
        docs = copy.deepcopy(self.docs)
        docs["executive"].purpose = docs["technical"].executive_summary.core_purpose
        with self.assertRaisesRegex(OutputQualityError, "DEDUP"):
            validate_bundle(docs, self.model)

    def test_blocks_broken_internal_navigation(self):
        docs = copy.deepcopy(self.docs)
        page = docs["user-guide"].pages[0]
        page.wireframe_svg = '<svg><a href="#missing-object"><text>Broken</text></a></svg>'
        with self.assertRaisesRegex(OutputQualityError, "HTML-NAV"):
            validate_bundle(docs, self.model)


if __name__ == "__main__":
    unittest.main()
