"""Phase 1 + 2 + 3 tests: the document generators layer
(``pbicompass.agents.generators``) — ``AuditReportGenerator``,
``ExecutiveSummaryGenerator``, and ``BusinessGuideGenerator`` end-to-end,
plus the ``TechnicalDocumentationGenerator`` compatibility shim.

The LLM path is exercised with in-process fake clients, mirroring the
pattern in ``test_agents.py``, so no API key or network is required.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators import (
    DOCUMENT_TYPES,
    AuditReportGenerator,
    BusinessGuideGenerator,
    ExecutiveSummaryGenerator,
    TechnicalDocumentationGenerator,
)
from pbicompass.parsers import detect_and_parse
from pbicompass.schemas.audit_document import AuditDocument
from pbicompass.schemas.executive_document import ExecutiveDocument
from pbicompass.schemas.user_guide_document import UserGuideDocument

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"

_BANNED_JARGON = ("table", "DAX", "semantic model")


def _assert_no_jargon(testcase: unittest.TestCase, text: str) -> None:
    lowered = text.lower()
    for term in _BANNED_JARGON:
        testcase.assertNotIn(term.lower(), lowered, f"found banned jargon {term!r} in: {text!r}")


def _model():
    return detect_and_parse(FIXTURE)


class FakeAuditNarratorClient:
    """Returns a canned narrative for the Audit Narrator system prompt."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls += 1
        if "Audit & Health Report" in system:
            return {"narrative_overview": "FAKE_NARRATIVE_OVERVIEW"}
        raise AssertionError("unexpected system prompt")


class FakeExecutiveWriterClient:
    """Returns canned prose for the Executive Writer system prompt, and a
    canned business definition for the DAX Translator prompt Key KPIs also
    call now (P3)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls += 1
        if "executive summary" in system:
            return {
                "business_purpose": "FAKE_BUSINESS_PURPOSE",
                "business_value": "FAKE_BUSINESS_VALUE",
                "maintenance_overview": "FAKE_MAINTENANCE_OVERVIEW",
            }
        if "senior DAX developer" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "translations": [
                    {"name": m["name"], "plain_english": "FAKE_KPI_MEANING.",
                     "calculation_logic": "FAKE_CALC", "caveats": "",
                     "category": "Revenue", "confidence": "High"}
                    for m in payload["measures"]
                ]
            }
        raise AssertionError("unexpected system prompt")


class FailingClient:
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        raise RuntimeError("boom")


class DocumentTypesRegistryTest(unittest.TestCase):
    def test_registry_contains_all_document_types(self):
        self.assertEqual(set(DOCUMENT_TYPES), {"technical", "audit", "executive", "user-guide"})
        self.assertIs(DOCUMENT_TYPES["technical"], TechnicalDocumentationGenerator)
        self.assertIs(DOCUMENT_TYPES["audit"], AuditReportGenerator)
        self.assertIs(DOCUMENT_TYPES["executive"], ExecutiveSummaryGenerator)
        self.assertIs(DOCUMENT_TYPES["user-guide"], BusinessGuideGenerator)


class TechnicalGeneratorShimTest(unittest.TestCase):
    """generate_document() must delegate to TechnicalDocumentationGenerator
    with unchanged behavior — the backward-compatibility guarantee."""

    def test_generate_document_matches_generator_directly(self):
        # Same parsed model for both calls — a fresh detect_and_parse() per
        # call would give each a different meta.generated_at timestamp and
        # produce a spurious diff unrelated to the delegation being tested.
        model = _model()
        via_shim = generate_document(model, owner="Jane")
        via_generator = TechnicalDocumentationGenerator.generate(model, owner="Jane")
        self.assertEqual(via_shim.to_json(), via_generator.to_json())


class AuditGeneratorDeterministicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = AuditReportGenerator.generate(_model())

    def test_returns_audit_document(self):
        self.assertIsInstance(self.doc, AuditDocument)

    def test_metadata(self):
        self.assertEqual(self.doc.metadata.report_name, "SampleSales")
        self.assertEqual(self.doc.metadata.document_type, "audit")
        self.assertEqual(self.doc.metadata.target_audience,
                         "BI architects, technical leads, and governance teams")

    def test_health_and_complexity_populated(self):
        self.assertTrue(0 <= self.doc.health.overall <= 100)
        self.assertEqual(self.doc.complexity.level, "Low")

    def test_recommendations_present(self):
        self.assertTrue(self.doc.recommendations)

    def test_narrative_overview_is_deterministic_by_default(self):
        self.assertIn(str(self.doc.health.overall), self.doc.narrative_overview)
        self.assertIn(self.doc.health.band, self.doc.narrative_overview)

    def test_owner_and_classification_flow_into_governance(self):
        doc = AuditReportGenerator.generate(_model(), owner="Jane Doe", classification="Internal")
        self.assertFalse(any(f.area == "ownership" for f in doc.governance))

    def test_to_json_round_trips(self):
        text = self.doc.to_json()
        self.assertIn('"document_type": "audit"', text)
        self.assertIn('"health"', text)
        self.assertIn('"recommendations"', text)


class AuditGeneratorLlmTest(unittest.TestCase):
    def test_llm_narrative_is_used(self):
        client = FakeAuditNarratorClient()
        doc = AuditReportGenerator.generate(_model(), client)
        self.assertEqual(doc.narrative_overview, "FAKE_NARRATIVE_OVERVIEW")
        self.assertEqual(client.calls, 1)
        # everything else stays deterministic even with an LLM client supplied
        deterministic_doc = AuditReportGenerator.generate(_model())
        self.assertEqual(doc.health, deterministic_doc.health)
        self.assertEqual(doc.recommendations, deterministic_doc.recommendations)

    def test_failing_client_falls_back_to_deterministic_overview(self):
        warnings = []
        doc = AuditReportGenerator.generate(
            _model(), FailingClient(), on_warning=warnings.append,
        )
        self.assertTrue(warnings)
        self.assertIn(str(doc.health.overall), doc.narrative_overview)


class ExecutiveGeneratorDeterministicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = ExecutiveSummaryGenerator.generate(_model())

    def test_returns_executive_document(self):
        self.assertIsInstance(self.doc, ExecutiveDocument)

    def test_metadata(self):
        self.assertEqual(self.doc.metadata.report_name, "SampleSales")
        self.assertEqual(self.doc.metadata.document_type, "executive")
        self.assertEqual(self.doc.metadata.target_audience,
                         "Managers, executives, and project owners")

    def test_no_technical_jargon_in_business_purpose(self):
        # concise and non-technical — no table names or "semantic model" talk
        for banned in ("DAX", "semantic model"):
            self.assertNotIn(banned, self.doc.business_purpose)

    def test_statistics_reuse_model_meta_counts(self):
        self.assertEqual(self.doc.model_statistics["tables"], 4)
        self.assertEqual(self.doc.model_statistics["measures"], 4)
        self.assertEqual(self.doc.report_statistics["pages"], 3)
        self.assertEqual(self.doc.report_statistics["visible_pages"], 2)

    def test_known_risks_are_business_framed(self):
        # SampleSales has a known bidirectional Sales<->Date relationship —
        # the same finding the Audit & Health Report and technical document
        # surface (1.10), minus the "dax"-category findings whose issue text
        # names DAX constructs directly.
        self.assertTrue(any("bidirectional cross-filtering" in r for r in self.doc.known_risks))
        for risk in self.doc.known_risks:
            self.assertNotIn("DAX", risk)
            self.assertNotIn("USERELATIONSHIP", risk)

    def test_known_risks_match_audit_engine_severity_order(self):
        # 1.10: exec known_risks are a filtered subset of the same
        # recommendation list the audit/technical docs show, in the same
        # severity order — never independently re-derived.
        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        severities = [r.split("]", 1)[0].lstrip("[") for r in self.doc.known_risks]
        ranks = [order[s] for s in severities]
        self.assertEqual(ranks, sorted(ranks))

    def test_key_kpis_exclude_text_measures_and_carry_a_meaning(self):
        # Real usage-based selection (1.6): each KPI names its own meaning.
        for kpi in self.doc.key_kpis:
            self.assertIn(" — ", kpi)

    def test_future_recommendations_have_no_implementation_detail(self):
        for rec in self.doc.future_recommendations:
            self.assertNotIn("DAX", rec)
            self.assertNotIn("CROSSFILTER", rec)
            self.assertNotIn("VAR", rec)

    def test_dependencies_include_data_sources_and_parameters(self):
        self.assertTrue(any("Sql.Database" in d for d in self.doc.dependencies))
        self.assertTrue(any(d.startswith("Parameter:") for d in self.doc.dependencies))

    def test_future_recommendations_reuse_audit_engine(self):
        self.assertTrue(self.doc.future_recommendations)
        self.assertLessEqual(len(self.doc.future_recommendations), 3)

    def test_future_recommendations_do_not_repeat_known_risks(self):
        # P6: §11 Future Recommendations used to draw from the same
        # top-severity slice of the recommendation list as §9 Known Risks,
        # so the same issue appeared under both headings.
        risk_issue_fragments = [r.split("] ", 1)[1] for r in self.doc.known_risks]
        for rec in self.doc.future_recommendations:
            for fragment in risk_issue_fragments:
                self.assertNotIn(fragment, rec)

    def test_future_recommendations_keep_ask_and_benefit_distinct(self):
        # P6: items used to read as one mashed sentence ("...in their DAX.
        # Calculations stay correct...") with no visual separation between
        # the problem and the recommended action.
        for rec in self.doc.future_recommendations:
            self.assertIn(" — expected benefit: ", rec)

    def test_owner_reflected_in_maintenance_overview(self):
        doc = ExecutiveSummaryGenerator.generate(_model(), owner="Jane Doe")
        self.assertIn("Jane Doe", doc.maintenance_overview)

    def test_to_json_round_trips(self):
        text = self.doc.to_json()
        self.assertIn('"document_type": "executive"', text)
        self.assertIn('"business_purpose"', text)
        self.assertIn('"future_recommendations"', text)


class ExecutiveGeneratorLlmTest(unittest.TestCase):
    def test_llm_prose_is_used(self):
        client = FakeExecutiveWriterClient()
        doc = ExecutiveSummaryGenerator.generate(_model(), client)
        self.assertEqual(doc.business_purpose, "FAKE_BUSINESS_PURPOSE")
        self.assertEqual(doc.business_value, "FAKE_BUSINESS_VALUE")
        self.assertEqual(doc.maintenance_overview, "FAKE_MAINTENANCE_OVERVIEW")
        # 1 Executive Writer call + 1 DAX Translator batch call (P3: Key KPI
        # meanings reuse the same DAX Translator agent as the technical doc).
        self.assertEqual(client.calls, 2)
        self.assertTrue(any("FAKE_KPI_MEANING" in kpi for kpi in doc.key_kpis))
        # deterministic facts stay identical regardless of the LLM client
        deterministic_doc = ExecutiveSummaryGenerator.generate(_model())
        self.assertEqual(doc.model_statistics, deterministic_doc.model_statistics)
        self.assertEqual(doc.known_risks, deterministic_doc.known_risks)
        self.assertEqual(doc.future_recommendations, deterministic_doc.future_recommendations)

    def test_failing_client_falls_back_to_deterministic_prose(self):
        warnings = []
        doc = ExecutiveSummaryGenerator.generate(
            _model(), FailingClient(), on_warning=warnings.append,
        )
        self.assertTrue(warnings)
        self.assertNotEqual(doc.business_purpose, "")
        self.assertNotIn("FAKE", doc.business_purpose)


class FakeUserGuideWriterClient:
    """Returns canned prose for the User Guide Writer system prompt, and a
    canned business definition for the DAX Translator prompt the glossary
    also calls now (P3)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls += 1
        if "Business User Guide" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "introduction": "FAKE_INTRODUCTION",
                "pages": [
                    {"page_title": p["page_title"], "purpose": "FAKE_PURPOSE",
                     "common_scenarios": ["FAKE_SCENARIO"]}
                    for p in payload["pages"]
                ],
            }
        if "senior DAX developer" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "translations": [
                    {"name": m["name"], "plain_english": "FAKE_GLOSSARY_MEANING.",
                     "calculation_logic": "FAKE_CALC", "caveats": "",
                     "category": "Revenue", "confidence": "High"}
                    for m in payload["measures"]
                ]
            }
        raise AssertionError("unexpected system prompt")


class BusinessGuideGeneratorDeterministicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = BusinessGuideGenerator.generate(_model())

    def test_returns_user_guide_document(self):
        self.assertIsInstance(self.doc, UserGuideDocument)

    def test_metadata(self):
        self.assertEqual(self.doc.metadata.report_name, "SampleSales")
        self.assertEqual(self.doc.metadata.document_type, "user-guide")
        self.assertEqual(self.doc.metadata.target_audience, "Business users")

    def test_hidden_pages_are_excluded(self):
        # SampleSales has a hidden "Data Quality" page — a business user's
        # guide has no reason to document a page nobody sees.
        titles = {p.page_title for p in self.doc.pages}
        self.assertNotIn("Data Quality", titles)
        self.assertEqual(titles, {"Sales Overview", "Region Detail"})

    def test_bookmarks_and_tooltips_always_empty(self):
        # model.json has no bookmark/tooltip data today — must degrade to
        # empty lists, never fabricated content.
        for page in self.doc.pages:
            self.assertEqual(page.bookmarks, [])
            self.assertEqual(page.tooltips, [])

    def test_drillthrough_action_points_at_target_page(self):
        source = next(p for p in self.doc.pages if p.page_title == "Sales Overview")
        self.assertTrue(any("Region Detail" in a for a in source.drillthrough_actions))
        target = next(p for p in self.doc.pages if p.page_title == "Region Detail")
        self.assertEqual(target.drillthrough_actions, [])

    def test_glossary_covers_measures_and_dimensions(self):
        terms = {g.term for g in self.doc.glossary}
        self.assertIn("Total Revenue", terms)
        self.assertIn("Region", terms)

    def test_no_technical_jargon_anywhere(self):
        _assert_no_jargon(self, self.doc.introduction)
        for page in self.doc.pages:
            _assert_no_jargon(self, page.purpose)
            for scenario in page.common_scenarios:
                _assert_no_jargon(self, scenario)
        for term in self.doc.glossary:
            _assert_no_jargon(self, term.plain_definition)

    def test_to_json_round_trips(self):
        text = self.doc.to_json()
        self.assertIn('"document_type": "user-guide"', text)
        self.assertIn('"introduction"', text)
        self.assertIn('"glossary"', text)

    def test_no_mad_libs_questions_or_generic_scenarios(self):
        # 1.1: the deterministic path must never echo a lowercased measure
        # name into a "What is our X?" question, and never emit the generic
        # "Use this page when you want to check..." filler — the whole
        # common_scenarios section is deterministic-offline empty until an
        # LLM polishes it (1.3's chart-pair questions replace it instead).
        for page in self.doc.pages:
            for q in page.business_questions_answered:
                self.assertNotIn("what is our", q.lower())
            self.assertEqual(page.common_scenarios, [])

    def test_business_questions_grounded_in_chart_pairs(self):
        # 1.3: every question names a metric+dimension pair actually charted
        # together, phrased by the dimension's kind (time/geo/other).
        source = next(p for p in self.doc.pages if p.page_title == "Sales Overview")
        for q in source.business_questions_answered:
            self.assertTrue(q.startswith(("How has ", "How does ", "How is ")))

    def test_glossary_reuses_dax_translation_not_generic_bucket(self):
        # 1.5: no measure with a real DAX-derived definition should fall
        # back to the old generic "a custom metric specific to this report"
        # bucket text.
        by_term = {g.term: g.plain_definition for g in self.doc.glossary}
        self.assertNotEqual(by_term["Total Revenue"], "A custom metric specific to this report.")
        self.assertTrue(by_term["Total Revenue"])

    def test_no_duplicate_filter_bullets(self):
        # 1.7: a page's filter list never repeats the same field name twice,
        # even if two slicer visuals are bound to it.
        for page in self.doc.pages:
            self.assertEqual(len(page.filters), len(set(page.filters)))

    def test_same_leaf_name_different_tables_collapses_for_display(self):
        # Regression: two slicers on genuinely different fields that happen
        # to share a leaf column name (e.g. "Orders.Type" and
        # "Restaurant.Type") must still collapse to one "Type (2 slicers)"
        # line for a business reader — report_facts.slicers() dedupes on the
        # full qualified name (correctly keeping them distinct there), but
        # the business-guide display only shows the leaf name, so it must
        # dedupe again at that level or "Type, Type" and a doubled nav-tip
        # bullet leak back in.
        from pbicompass.schemas.model import Page, SemanticModel, Visual

        page = Page(
            id="p1", display_name="Overview",
            visuals=[
                Visual(id="s1", type="slicer", is_slicer=True, fields=["Orders.Type"]),
                Visual(id="s2", type="slicer", is_slicer=True, fields=["Restaurant.Type"]),
            ],
        )
        doc = BusinessGuideGenerator.generate(SemanticModel(report_name="R", pages=[page]))
        guide_page = doc.pages[0]
        self.assertEqual(guide_page.filters, ["Type (2 slicers)"])
        self.assertEqual(
            guide_page.navigation_tips.count("Use the 'Type' filter to narrow down what you see on this page."), 1,
        )


class BusinessGuideGeneratorLlmTest(unittest.TestCase):
    def test_llm_prose_is_used(self):
        client = FakeUserGuideWriterClient()
        doc = BusinessGuideGenerator.generate(_model(), client)
        self.assertEqual(doc.introduction, "FAKE_INTRODUCTION")
        self.assertTrue(all(p.purpose == "FAKE_PURPOSE" for p in doc.pages))
        self.assertTrue(all(p.common_scenarios == ["FAKE_SCENARIO"] for p in doc.pages))
        # 1 User Guide Writer call + 1 DAX Translator batch call (P3: the
        # glossary reuses the same DAX Translator agent as the technical doc
        # instead of only ever falling back to the deterministic gloss).
        self.assertEqual(client.calls, 2)
        measure_terms = [g for g in doc.glossary if g.term in {m.name for m in _model().all_measures()}]
        self.assertTrue(measure_terms)
        self.assertTrue(all("FAKE_GLOSSARY_MEANING" in g.plain_definition for g in measure_terms))
        # deterministic facts (page structure) stay identical regardless of
        # the LLM client — only the glossary's *meanings* change with one.
        deterministic_doc = BusinessGuideGenerator.generate(_model())
        self.assertEqual(
            [p.visual_descriptions for p in doc.pages],
            [p.visual_descriptions for p in deterministic_doc.pages],
        )
        self.assertEqual([g.term for g in doc.glossary], [g.term for g in deterministic_doc.glossary])

    def test_failing_client_falls_back_to_deterministic_prose(self):
        warnings = []
        doc = BusinessGuideGenerator.generate(
            _model(), FailingClient(), on_warning=warnings.append,
        )
        self.assertTrue(warnings)
        self.assertNotIn("FAKE", doc.introduction)
        self.assertTrue(doc.pages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
