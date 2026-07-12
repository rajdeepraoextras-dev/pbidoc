"""Phase 2 tests: the agent orchestrator (deterministic + LLM paths) and renderer.

The LLM path is exercised with an in-process fake client, so no API key or
network is required.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.deterministic import translate_dax
from pbicompass.parsers import detect_and_parse
from pbicompass.render import render_markdown

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _fake_report_intelligence_response() -> dict:
    """Canned schema-valid ``ModelInsights`` (Phase 2) shared by every fake
    client below that reaches ``build_job_context`` (anything answering
    the Business Analyst/Data Modeler/Executive/User-Guide prompts also
    triggers the Report Intelligence pass first)."""
    return {
        "business_domain": "FAKE_DOMAIN",
        "report_purpose": {"statement": "FAKE_REPORT_PURPOSE", "confidence": "High"},
        "audience_hypotheses": [],
        "entity_definitions": [],
        "page_workflows": [],
        "kpi_relationships": [],
        "cross_cutting_observations": [],
        "data_quality_notes": [],
    }


class FakeLLMClient:
    """Returns canned schema-valid JSON, routed by the agent's system prompt."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
        if "Business Analyst" in system or "BI consultant" in system:
            return {
                "core_purpose": "FAKE_PURPOSE",
                "pages": [{"page_title": "P1", "summary": "S1"}],
                "navigation_guide": ["nav1"],
                "complex_visual_explainers": [
                    {"visual": "v", "page": "P1", "how_to_read": "hr"}
                ],
            }
        if "senior DAX developer" in system or "DAX measures" in system:
            payload = json.loads(user)
            return {
                "translations": [
                    {"name": m["name"], "plain_english": "FAKE_TX",
                     "calculation_logic": "FAKE_CALC", "caveats": "",
                     "category": "Other", "confidence": "High"}
                    for m in payload["measures"]
                ]
            }
        if "data-modeling" in system:
            return {"summary": "FAKE_MODEL", "risks": ["FAKE_RISK"]}
        if "description for every column" in system or "Column Describer" in system:
            payload = json.loads(user)
            return {
                "columns": [
                    {"table": c["table"], "column": c["column"], "description": "FAKE_COLUMN_DESC"}
                    for c in payload["columns"]
                ]
            }
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        if "fact-checker" in system:  # the grounding pass (Phase 3)
            return {"claims": []}
        raise AssertionError("unexpected system prompt")


class FailingClient:
    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        raise RuntimeError("boom")


class DeterministicOrchestratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)
        cls.doc = generate_document(cls.model)

    def test_metadata(self):
        self.assertEqual(self.doc.metadata.report_name, "SampleSales")
        self.assertEqual(self.doc.metadata.source_format, "pbip-tmdl")

    def test_executive_summary_visible_pages_only(self):
        titles = [p.page_title for p in self.doc.executive_summary.pages]
        self.assertEqual(titles, ["Sales Overview", "Region Detail"])  # hidden page excluded
        self.assertIn("SampleSales", self.doc.executive_summary.core_purpose)

    def test_navigation_and_explainers(self):
        nav = " ".join(self.doc.executive_summary.navigation_guide)
        self.assertIn("slicer", nav)
        self.assertIn("Drill through", nav)
        types = {e.how_to_read for e in self.doc.executive_summary.complex_visual_explainers}
        self.assertEqual(len(self.doc.executive_summary.complex_visual_explainers), 2)

    def test_lineage(self):
        self.assertTrue(any("Sql.Database" in s for s in self.doc.lineage.source_systems))
        self.assertTrue(self.doc.lineage.transformations)

    def test_semantic_model_summary(self):
        self.assertIn("star schema", self.doc.semantic_model.summary)
        # risks are now a separate field (not folded into the summary markdown)
        self.assertTrue(any("bi-directional" in r for r in self.doc.semantic_model.risks))
        self.assertTrue(any("INACTIVE" in r for r in self.doc.semantic_model.relationships))

    def test_measure_catalog(self):
        by_name = {m.name: m for m in self.doc.measure_catalog.measures}
        self.assertIn("Canceled", by_name["Total Revenue"].caveats)
        self.assertEqual(by_name["Total Revenue"].category, "Revenue")
        self.assertEqual(by_name["Revenue YTD"].category, "Time-Intelligence")
        self.assertEqual(by_name["Avg Order Value"].category, "Ratio")
        self.assertTrue(by_name["Total Revenue"].dax)  # raw DAX preserved

    def test_security(self):
        names = {r["name"] for r in self.doc.security.roles}
        self.assertEqual(names, {"Regional Manager", "Sales Rep"})

    def test_audit_orphans_are_deterministic(self):
        self.assertEqual(
            sorted(self.doc.tech_debt.orphaned_measures),
            ["Orphan Margin", "Revenue YTD"],
        )
        self.assertEqual(self.doc.tech_debt.hidden_but_used, [])

    def test_audit_matches_table_prefixed_measure_refs(self):
        # Power BI references measures as "HomeTable.Measure" — the audit must
        # match the trailing segment, else everything looks orphaned.
        from pbicompass.schemas.model import Measure, Page, SemanticModel, Table, Visual
        model = SemanticModel(
            report_name="X",
            tables=[Table(name="MT", measures=[
                Measure(name="Sales", expression="SUM(t[v])"),
                Measure(name="Unused", expression="1")])],
            pages=[Page(id="p1", display_name="P1",
                        visuals=[Visual(id="v1", type="card", fields=["MT.Sales"])])],
        )
        doc = generate_document(model)
        self.assertEqual(doc.tech_debt.orphaned_measures, ["Unused"])
        # and the measure records which page it is used on
        sales = next(m for m in doc.measure_catalog.measures if m.name == "Sales")
        self.assertEqual(sales.used_on, ["P1"])

    def test_renders_enterprise_sections_in_order(self):
        md = render_markdown(self.doc)
        headings = [
            "## 1. Document Control",
            "## 2. Executive Summary",
            "## 5. Data Sources",
            "## 6. Data Model",
            "## 7. Measures & Calculations (DAX Dictionary)",
            "## 8. Report Pages & Visuals",
            "## 10. Row-Level Security (RLS)",
            "## 15. Known Issues, Assumptions & Limitations",
            "## 16. Model Health & AI Recommendations",
            "## 18. Appendix & Sign-off",
        ]
        positions = [md.find(h) for h in headings]
        self.assertNotIn(-1, positions)
        self.assertEqual(positions, sorted(positions))  # in template order


class LLMOrchestratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = detect_and_parse(FIXTURE)

    def test_llm_output_is_used(self):
        client = FakeLLMClient()
        doc = generate_document(self.model, client)
        self.assertEqual(doc.executive_summary.core_purpose, "FAKE_PURPOSE")
        self.assertIn("FAKE_MODEL", doc.semantic_model.summary)
        self.assertTrue(all(m.plain_english == "FAKE_TX" for m in doc.measure_catalog.measures))
        self.assertTrue(any(col["description"] == "FAKE_COLUMN_DESC" for col in doc.semantic_model.data_dictionary))
        # the deterministic audit is unaffected by the LLM
        self.assertEqual(sorted(doc.tech_debt.orphaned_measures), ["Orphan Margin", "Revenue YTD"])

    def test_failing_client_falls_back(self):
        warnings: list[str] = []
        doc = generate_document(self.model, FailingClient(), on_warning=warnings.append)
        # deterministic content present despite the failing client
        self.assertIn("SampleSales", doc.executive_summary.core_purpose)
        self.assertIn("star schema", doc.semantic_model.summary)
        self.assertTrue(any("fallback" in w for w in warnings))


class _BusinessAnalystOneBatchFailsClient:
    """Only answers the Business Analyst agent; raises for any batch whose
    pages include ``poison_title`` (simulating one bad/invalid LLM response),
    so 1.4's per-batch retry-then-fallback path can be exercised without a
    real network dependency. Other agents (DAX Translator, Data Modeler,
    Column Describer) fall back to their deterministic path via the same
    raise, same as ``FailingClient`` elsewhere in this file."""

    def __init__(self, poison_title: str):
        self.poison_title = poison_title
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "Business Analyst" not in system and "BI consultant" not in system:
            raise RuntimeError("this fake only supports the Business Analyst agent")
        payload = json.loads(user)
        titles = [p["display_name"] for p in payload["pages"]]
        if self.poison_title in titles:
            raise RuntimeError("simulated bad batch response")
        return {
            "core_purpose": "FAKE_PURPOSE",
            "pages": [{"page_title": t, "summary": "FAKE_SUMMARY"} for t in titles],
            "navigation_guide": [],
            "complex_visual_explainers": [],
        }


class BusinessAnalystBatchFailureTest(unittest.TestCase):
    """1.4: a bad LLM response for one page-batch degrades only that
    batch's pages, not the whole Executive Summary, and the warning names
    exactly the affected pages."""

    @staticmethod
    def _model_with_pages(n: int):
        from pbicompass.schemas.model import Measure, Page, SemanticModel, Table, Visual
        table = Table(name="Sales", measures=[Measure(name="Metric1", expression="SUM(Sales[Amount])", table="Sales")])
        pages = [
            Page(id=f"p{i}", display_name=f"Page {i}",
                 visuals=[Visual(id=f"v{i}", type="card", fields=["Sales.Metric1"])])
            for i in range(1, n + 1)
        ]
        return SemanticModel(report_name="BatchTest", tables=[table], pages=pages)

    def test_one_bad_batch_degrades_only_its_pages(self):
        # BUSINESS_ANALYST_BATCH_SIZE is 6, so 7 pages split into batches of
        # [Page 1..6] and [Page 7] — poisoning "Page 7" isolates the failure
        # to the second batch.
        model = self._model_with_pages(7)
        client = _BusinessAnalystOneBatchFailsClient(poison_title="Page 7")
        warnings: list[str] = []
        doc = generate_document(model, client, on_warning=warnings.append)

        by_title = {p.page_title: p for p in doc.executive_summary.pages}
        self.assertEqual(len(by_title), 7)
        for i in range(1, 7):
            self.assertEqual(by_title[f"Page {i}"].summary, "FAKE_SUMMARY")
        self.assertNotEqual(by_title["Page 7"].summary, "FAKE_SUMMARY")
        self.assertTrue(any("Page 7" in w for w in warnings))


class _PuntAndMetaCommentaryClient:
    """Simulates the two production leaks fixed by D2/D6: the Column
    Describer returning a bare punt or a leaked editing directive, and the
    DAX Translator returning a punt for a measure's business meaning. Every
    other agent answers cleanly so only the guarded merge points are under
    test."""

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
        if "Business Analyst" in system or "BI consultant" in system:
            return {
                "core_purpose": "FAKE_PURPOSE", "pages": [],
                "navigation_guide": [], "complex_visual_explainers": [],
            }
        if "senior DAX developer" in system or "DAX measures" in system:
            payload = json.loads(user)
            return {
                "translations": [
                    {"name": m["name"],
                     "plain_english": "Business meaning could not be inferred automatically; requires business confirmation.",
                     "calculation_logic": "", "caveats": "", "category": "Other", "confidence": "Low"}
                    for m in payload["measures"]
                ]
            }
        if "data-modeling" in system:
            return {"summary": "FAKE_MODEL", "risks": []}
        if "description for every column" in system or "Column Describer" in system:
            payload = json.loads(user)
            descriptions = {
                "CustomerRef": "Unknown — requires business confirmation.",
                "Notes": "Consider providing a more specific description of what Notes contains.",
                "OrderId": "Unique identifier for each order line; used for order-level joins.",
            }
            return {
                "columns": [
                    {"table": c["table"], "column": c["column"],
                     "description": descriptions.get(c["column"], "")}
                    for c in payload["columns"]
                ]
            }
        if "expert technical editor" in system:  # critic pass
            return {"violations": []}
        if "fact-checker" in system:  # grounding pass
            return {"claims": []}
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


class AntiPuntGuardTest(unittest.TestCase):
    """D6: the LLM may only improve a description, never downgrade one —
    and D2: a leaked editing directive is rejected outright. Uses a small
    hand-built model (rather than the SampleSales fixture) so a
    relationship on a column *not* named ``*Id``/``*Key`` is under direct
    control, exercising the broadened join-key derivation."""

    @staticmethod
    def _model():
        from pbicompass.schemas.model import Column, Measure, Page, Relationship, SemanticModel, Table, Visual
        orders = Table(name="Orders", columns=[
            Column(name="OrderId"),
            Column(name="CustomerRef"),
            Column(name="Notes"),
            Column(name="Segment"),
        ], measures=[Measure(name="Order Count", expression="COUNTROWS ( Orders )", table="Orders")])
        customers = Table(name="Customers", columns=[Column(name="CustomerKey")])
        page = Page(id="p1", display_name="Overview", visuals=[
            Visual(id="v1", type="card", fields=["Orders.Segment"]),
        ])
        return SemanticModel(
            report_name="GuardTest",
            tables=[orders, customers],
            relationships=[Relationship(from_table="Orders", from_column="CustomerRef",
                                         to_table="Customers", to_column="CustomerKey")],
            pages=[page],
        )

    def test_relationship_column_keeps_deterministic_join_key_over_a_punt(self):
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        by_col = {(c["table"], c["column"]): c["description"] for c in doc.semantic_model.data_dictionary}
        desc = by_col[("Orders", "CustomerRef")]
        self.assertNotIn("requires business confirmation", desc)
        self.assertIn("Join key linking Orders to Customers", desc)

    def test_meta_commentary_description_is_rejected(self):
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        by_col = {(c["table"], c["column"]): c["description"] for c in doc.semantic_model.data_dictionary}
        desc = by_col[("Orders", "Notes")]
        self.assertNotIn("Consider providing", desc)
        self.assertEqual(desc, "No description set.")

    def test_llm_can_still_improve_a_column_description(self):
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        by_col = {(c["table"], c["column"]): c["description"] for c in doc.semantic_model.data_dictionary}
        self.assertIn("order-level joins", by_col[("Orders", "OrderId")])

    def test_no_column_ever_renders_the_punt_phrase(self):
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        for col in doc.semantic_model.data_dictionary:
            self.assertNotIn("requires business confirmation", col["description"])

    def test_measure_keeps_deterministic_gloss_over_a_punt(self):
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        entry = next(m for m in doc.measure_catalog.measures if m.name == "Order Count")
        self.assertNotIn("requires business confirmation", entry.plain_english)
        self.assertTrue(entry.plain_english)  # the deterministic translate_dax gloss, not empty

    def test_glossary_dimension_with_no_keyword_match_never_gets_the_punt_phrase(self):
        # D6 residual gap found in Sprint 1 Day 5 QA: _infer_glossary (the
        # section-14 business glossary) had its own, un-fixed fallback that
        # still defaulted a genuinely roleless dimension (no date/customer/
        # product/region keyword match) to "Unknown — requires business
        # confirmation." even after the section-6 data dictionary was fixed
        # to say "No description set." for the exact same case.
        doc = generate_document(self._model(), _PuntAndMetaCommentaryClient())
        entry = next(g for g in doc.glossary_entries if g["term"] == "Segment")
        self.assertNotIn("requires business confirmation", entry["definition"])
        self.assertEqual(entry["definition"], "No description set.")


class DaxTranslatorUnitTest(unittest.TestCase):
    def test_sumx_with_filter(self):
        en, caveats, cat = translate_dax(
            "Total Revenue",
            'SUMX ( FILTER ( Sales, Sales[Status] <> "Canceled" ), Sales[Qty] * Sales[Price] )',
            r"\$#,0",
        )
        self.assertIn("row by row", en)
        self.assertIn("Canceled", caveats)
        self.assertEqual(cat, "Revenue")

    def test_divide_is_ratio(self):
        en, _, cat = translate_dax("AOV", "DIVIDE ( [Revenue], [Orders] )", None)
        self.assertIn("divided by", en)
        self.assertEqual(cat, "Ratio")

    def test_time_intelligence(self):
        _, _, cat = translate_dax("YTD", "TOTALYTD ( [Revenue], 'Date'[Date] )", None)
        self.assertEqual(cat, "Time-Intelligence")


class _FakeAllAgentsClient:
    """Answers every agent prompt used across all four document types (Phase
    0's shared ``JobAIContext`` needs one client that survives being handed
    to ``build_job_context`` and then every ``DOCUMENT_TYPES`` generator in
    turn) — tracks DAX Translator calls separately from the total so a test
    can prove it only ever runs once per job, not once per document type."""

    def __init__(self):
        self.calls = 0
        self.dax_calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
        if "senior DAX developer" in system or "DAX measures" in system:
            self.dax_calls += 1
            payload = json.loads(user)
            return {
                "translations": [
                    {"name": m["name"], "plain_english": "FAKE_TX",
                     "calculation_logic": "FAKE_CALC", "caveats": "",
                     "category": "Other", "confidence": "High"}
                    for m in payload["measures"]
                ]
            }
        if "Business Analyst" in system or "BI consultant" in system:
            return {
                "core_purpose": "FAKE_PURPOSE",
                "pages": [{"page_title": "P1", "summary": "S1"}],
                "navigation_guide": [],
                "complex_visual_explainers": [],
            }
        if "data-modeling" in system:
            return {"summary": "FAKE_MODEL", "risks": []}
        if "description for every column" in system or "Column Describer" in system:
            payload = json.loads(user)
            return {
                "columns": [
                    {"table": c["table"], "column": c["column"], "description": "FAKE_COLUMN_DESC"}
                    for c in payload["columns"]
                ]
            }
        if "Audit & Health Report" in system:
            return {"narrative_overview": "FAKE_NARRATIVE_OVERVIEW"}
        if "executive summary" in system:
            return {
                "business_purpose": "FAKE_BUSINESS_PURPOSE",
                "business_value": "FAKE_BUSINESS_VALUE",
                "maintenance_overview": "FAKE_MAINTENANCE_OVERVIEW",
            }
        if "Business User Guide" in system:
            payload = json.loads(user)
            return {
                "introduction": "FAKE_INTRODUCTION",
                "pages": [
                    {"page_title": p["page_title"], "purpose": "FAKE_PURPOSE",
                     "common_scenarios": ["FAKE_SCENARIO"]}
                    for p in payload["pages"]
                ],
            }
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        if "fact-checker" in system:  # the grounding pass (Phase 3)
            return {"claims": []}
        raise AssertionError(f"unexpected system prompt: {system[:80]!r}")


class SharedJobContextTest(unittest.TestCase):
    """Phase 0 (``AI_NATIVE_PLAN.md``): ``build_job_context`` runs the DAX
    Translator once for the whole job; every ``DOCUMENT_TYPES`` generator
    given that same ``ai_context`` must consume its ``.translations``
    instead of re-calling the agent — previously up to 3x redundant spend
    in a ``--document all`` job (technical's Measure Catalog, executive's
    Key KPIs, the user guide's glossary each called it independently)."""

    def test_dax_translator_runs_once_across_all_document_types(self):
        from pbicompass.agents.context import build_job_context
        from pbicompass.agents.generators import DOCUMENT_TYPES

        model = detect_and_parse(FIXTURE)
        client = _FakeAllAgentsClient()
        warn = lambda _msg: None  # noqa: E731

        ai_context = build_job_context(model, client, warn)
        calls_after_context_build = client.dax_calls
        self.assertGreaterEqual(calls_after_context_build, 1)

        for generator in DOCUMENT_TYPES.values():
            generator.generate(model, client, ai_context=ai_context, on_warning=warn)

        # Every document type consumed the shared translations — no generator
        # made its own additional DAX Translator call.
        self.assertEqual(client.dax_calls, calls_after_context_build)


class ReportIntelligenceTest(unittest.TestCase):
    """Phase 2 (``AI_NATIVE_PLAN.md``): the one whole-model Report
    Intelligence call, wired into ``build_job_context`` and stored on
    ``JobAIContext.insights`` (the field Phase 0 reserved for it)."""

    def test_insights_populated_when_client_succeeds(self):
        from pbicompass.agents.context import build_job_context

        model = detect_and_parse(FIXTURE)
        ai_context = build_job_context(model, _FakeAllAgentsClient(), lambda _msg: None)
        self.assertIsNotNone(ai_context.insights)
        self.assertEqual(ai_context.insights["business_domain"], "FAKE_DOMAIN")
        self.assertEqual(ai_context.insights["report_purpose"]["statement"], "FAKE_REPORT_PURPOSE")

    def test_insights_none_when_offline(self):
        from pbicompass.agents.context import build_job_context

        model = detect_and_parse(FIXTURE)
        ai_context = build_job_context(model, None, lambda _msg: None)
        self.assertIsNone(ai_context.insights)

    def test_insights_none_on_failure_with_warning(self):
        from pbicompass.agents.context import build_job_context

        model = detect_and_parse(FIXTURE)
        warnings: list[str] = []
        ai_context = build_job_context(model, FailingClient(), warnings.append)
        self.assertIsNone(ai_context.insights)
        self.assertTrue(any("Report Intelligence" in w for w in warnings))

    def test_digest_contains_tables_measures_and_audit_counts(self):
        from pbicompass.agents.insights import build_model_digest

        model = detect_and_parse(FIXTURE)
        audit_summary = {
            "health_overall": 80, "health_band": "Good", "complexity_level": "Low",
            "dax_finding_count": 1, "failed_practice_count": 2,
            "performance_risk_count": 0, "governance_finding_count": 0,
            "unused_asset_count": 3,
        }
        digest = build_model_digest(model, audit_summary)
        self.assertIn("Health score: 80/100 (Good)", digest)
        self.assertIn("== Tables ==", digest)
        self.assertIn("== Measures ==", digest)
        self.assertIn("== Pages ==", digest)
        self.assertTrue(any(t.name in digest for t in model.tables))

    def test_digest_respects_char_budget(self):
        from pbicompass.agents.insights import build_model_digest

        model = detect_and_parse(FIXTURE)
        audit_summary = {
            "health_overall": 0, "health_band": "Poor", "complexity_level": "Low",
            "dax_finding_count": 0, "failed_practice_count": 0,
            "performance_risk_count": 0, "governance_finding_count": 0,
            "unused_asset_count": 0,
        }
        digest = build_model_digest(model, audit_summary, char_budget=200)
        self.assertLessEqual(len(digest), 200 + len("\n... (truncated)"))
        self.assertTrue(digest.endswith("... (truncated)"))

    def test_report_context_embedded_when_present_and_omitted_when_none(self):
        from pbicompass.agents import io

        model = detect_and_parse(FIXTURE)
        ctx = {"business_domain": "FAKE_DOMAIN"}

        with_ctx = io.business_analyst_input(model, report_context=ctx)
        without_ctx = io.business_analyst_input(model)
        self.assertEqual(with_ctx["report_context"], ctx)
        self.assertNotIn("report_context", without_ctx)

        dax_with_ctx = io.dax_translator_input(model, report_context=ctx)
        dax_without_ctx = io.dax_translator_input(model)
        self.assertEqual(dax_with_ctx["report_context"], ctx)
        self.assertNotIn("report_context", dax_without_ctx)

        for batch in io.dax_translator_batches(model, report_context=ctx):
            self.assertEqual(batch["report_context"], ctx)
        for batch in io.dax_translator_batches(model):
            self.assertNotIn("report_context", batch)

        dm_with_ctx = io.data_modeler_input(model, report_context=ctx)
        self.assertEqual(dm_with_ctx["report_context"], ctx)
        self.assertNotIn("report_context", io.data_modeler_input(model))

        ew_with_ctx = io.executive_writer_input("p", [], {}, {}, [], "m", report_context=ctx)
        self.assertEqual(ew_with_ctx["report_context"], ctx)
        self.assertNotIn("report_context", io.executive_writer_input("p", [], {}, {}, [], "m"))

        ugw_with_ctx = io.user_guide_writer_input("R", "intro", [], report_context=ctx)
        self.assertEqual(ugw_with_ctx["report_context"], ctx)
        self.assertNotIn("report_context", io.user_guide_writer_input("R", "intro", []))


class SandboxedLlmCacheTest(unittest.TestCase):
    """Phase 0: the hosted service points the LLM response cache at a file
    inside the job's own sandbox (``JobAIContext.cache_path``) rather than
    the ``PBICOMPASS_LLM_CACHE`` env var — avoids a race between concurrent
    jobs in the same worker process, and the cache file is shredded with
    everything else in the sandbox when the job ends."""

    def test_cache_file_lives_under_the_sandbox_and_is_shredded_after(self):
        from pbicompass.agents.context import build_job_context
        from pbicompass.service.sandbox import JobSandbox

        sandbox = JobSandbox("test-job", root=tempfile.mkdtemp(prefix="pbicompass_test_sbroot_"))
        cache_path = sandbox.path("llm_cache.db")
        try:
            model = detect_and_parse(FIXTURE)
            client = FakeLLMClient()
            build_job_context(model, client, lambda _msg: None, cache_path=str(cache_path))
            self.assertTrue(cache_path.exists(), "cache file was not created inside the sandbox")
        finally:
            sandbox.cleanup()
        self.assertFalse(cache_path.exists(), "cache file survived sandbox cleanup")
        self.assertFalse(sandbox.dir.exists(), "sandbox directory survived cleanup")


class StructuredOutputSchemaTest(unittest.TestCase):
    """Every schema handed to an LLM must set ``additionalProperties: False``
    on every object node, recursively — OpenAI's strict structured-output
    mode (used by :class:`MeshAPIClient`) 400s on any schema that doesn't
    ("'additionalProperties' is required to be supplied and to be false"),
    even though Anthropic/Gemini/Cohere are all lenient enough that a gap
    goes unnoticed there. Regression: ``critic.py``'s ``CRITIC_SCHEMA`` was
    missing it at both the root and the nested ``violations`` items object —
    every schema in ``io.py`` already had it (per that module's own stated
    invariant), but ``CRITIC_SCHEMA`` lives in a different module and was
    overlooked."""

    @staticmethod
    def _missing_additional_properties(schema, path: str = "root") -> list[str]:
        problems: list[str] = []
        if isinstance(schema, dict):
            if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
                problems.append(path)
            for key, value in schema.items():
                problems.extend(StructuredOutputSchemaTest._missing_additional_properties(value, f"{path}.{key}"))
        elif isinstance(schema, list):
            for i, value in enumerate(schema):
                problems.extend(StructuredOutputSchemaTest._missing_additional_properties(value, f"{path}[{i}]"))
        return problems

    def test_every_agent_schema_sets_additional_properties_false(self):
        from pbicompass.agents import grounding, insights, io
        from pbicompass.agents.critic import CRITIC_SCHEMA

        schemas = {
            "BUSINESS_ANALYST_SCHEMA": io.BUSINESS_ANALYST_SCHEMA,
            "DAX_TRANSLATOR_SCHEMA": io.DAX_TRANSLATOR_SCHEMA,
            "DATA_MODELER_SCHEMA": io.DATA_MODELER_SCHEMA,
            "COLUMN_DESCRIBER_SCHEMA": io.COLUMN_DESCRIBER_SCHEMA,
            "AUDIT_NARRATOR_SCHEMA": io.AUDIT_NARRATOR_SCHEMA,
            "EXECUTIVE_WRITER_SCHEMA": io.EXECUTIVE_WRITER_SCHEMA,
            "USER_GUIDE_WRITER_SCHEMA": io.USER_GUIDE_WRITER_SCHEMA,
            "CRITIC_SCHEMA": CRITIC_SCHEMA,
            "REPORT_INTELLIGENCE_SCHEMA": insights.REPORT_INTELLIGENCE_SCHEMA,
            "GROUNDING_SCHEMA": grounding.GROUNDING_SCHEMA,
        }
        for name, schema in schemas.items():
            with self.subTest(schema=name):
                problems = self._missing_additional_properties(schema)
                self.assertEqual(problems, [], f"{name} is missing additionalProperties: False at {problems}")


class ClientFactoryTest(unittest.TestCase):
    def test_offline_returns_none(self):
        from pbicompass.agents.llm import get_client
        self.assertIsNone(get_client("none"))
        self.assertIsNone(get_client(None))

    def test_unknown_provider_raises(self):
        from pbicompass.agents.llm import get_client
        with self.assertRaises(ValueError):
            get_client("frobnicate")

    def test_gemini_routes_and_needs_package(self):
        from pbicompass.agents.llm import get_client
        try:
            import google.genai  # noqa: F401
            self.skipTest("google-genai installed; ImportError path not exercised")
        except ImportError:
            # Routes to Gemini (not ValueError) even when a Claude model id is passed.
            with self.assertRaises(ImportError):
                get_client("gemini", model="claude-opus-4-8")

    def test_cohere_routes_and_needs_package(self):
        from pbicompass.agents.llm import get_client
        try:
            import cohere  # noqa: F401
            self.skipTest("cohere installed; ImportError path not exercised")
        except ImportError:
            # Routes to Cohere (not ValueError) even when a Claude model id is passed.
            with self.assertRaises(ImportError):
                get_client("cohere", model="claude-opus-4-8")

    def test_cohere_skips_thinking_item_and_parses_text(self):
        # Regression: reasoning models lead message.content with a 'thinking'
        # item that has no .text — complete_json must skip to the 'text' item
        # instead of blindly taking content[0].
        try:
            import cohere  # noqa: F401
        except ImportError:
            self.skipTest("cohere not installed")
        from types import SimpleNamespace
        from pbicompass.agents.llm import CohereClient

        client = CohereClient(api_key="dummy")
        thinking = SimpleNamespace(type="thinking", thinking="pondering...")
        text = SimpleNamespace(type="text", text='{"ok": true}')
        fake = SimpleNamespace(message=SimpleNamespace(content=[thinking, text]))
        client._client = SimpleNamespace(chat=lambda **kw: fake)

        out = client.complete_json("sys", "user", {"type": "object"})
        self.assertEqual(out, {"ok": True})

    def test_meshapi_routes_and_needs_package(self):
        from pbicompass.agents.llm import get_client
        try:
            import openai  # noqa: F401
            self.skipTest("openai installed; ImportError path not exercised")
        except ImportError:
            # Routes to MeshAPI (not ValueError) even when a Claude model id
            # (no "provider/" prefix) is passed — falls back to the MeshAPI
            # default instead.
            with self.assertRaises(ImportError):
                get_client("meshapi", model="claude-opus-4-8")
            with self.assertRaises(ImportError):
                get_client("mesh", model="anthropic/claude-opus-4.8")

    def test_meshapi_never_sends_reasoning_effort_and_parses_response(self):
        # Stub out the 'openai' package (it may not be installed in this
        # environment — MeshAPI is deliberately implemented on top of the
        # official OpenAI SDK pointed at MeshAPI's base URL) so
        # MeshAPIClient's lazy import resolves to a fake client we control,
        # mirroring the Cohere content-parsing regression test above.
        #
        # Regression: MeshAPI documents `reasoning_effort` as a unified
        # field but 400s ("Unrecognized request argument supplied:
        # reasoning_effort") for models that don't recognize it — confirmed
        # against openai/gpt-4o, the default — so it must never be sent,
        # even when an effort was requested.
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",
                                      message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        captured: dict = {}

        class _FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return fake_response

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured["client_kwargs"] = kwargs
                self.chat = _FakeChat()

        fake_module = ModuleType("openai")
        fake_module.OpenAI = _FakeOpenAI

        with patch.dict(sys.modules, {"openai": fake_module}):
            from pbicompass.agents.llm import MeshAPIClient
            client = MeshAPIClient(model="anthropic/claude-opus-4.8", api_key="rsk_test", effort="xhigh")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertNotIn("reasoning_effort", captured)
        self.assertEqual(captured["model"], "anthropic/claude-opus-4.8")
        self.assertEqual(client.last_usage, {"input_tokens": 10, "output_tokens": 5})

    def test_meshapi_default_model_is_not_a_bedrock_routed_anthropic_id(self):
        # Regression: MeshAPI routes at least some "anthropic/..." model ids
        # through AWS Bedrock's Converse API, which 400s on the
        # structured-output parameter MeshAPI's translation layer attaches
        # for them ("output_config.format: Extra inputs are not permitted") —
        # every agent here needs strict JSON output, so the default must be a
        # model MeshAPI's own docs confirm has first-class structured-output
        # support (OpenAI or Gemini), never an unqualified Claude id.
        import sys
        from types import ModuleType
        from unittest.mock import patch

        from pbicompass.agents.llm import get_client

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                pass

        fake_module = ModuleType("openai")
        fake_module.OpenAI = _FakeOpenAI

        with patch.dict(sys.modules, {"openai": fake_module}):
            client = get_client("meshapi")

        self.assertFalse(client.model.startswith("anthropic/"))

    def test_gemini_schema_strips_additional_properties(self):
        from pbicompass.agents.llm import _gemini_schema
        schema = {
            "type": "object", "additionalProperties": False, "required": ["a"],
            "properties": {"a": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"x": {"type": "string", "enum": ["p", "q"]}}}}},
        }
        dumped = json.dumps(_gemini_schema(schema))
        self.assertNotIn("additionalProperties", dumped)
        self.assertIn('"required"', dumped)   # preserved
        self.assertIn('"enum"', dumped)        # preserved


class ReasoningEffortWiringTest(unittest.TestCase):
    """Day 6 (§4.0): the ``effort`` tier must reach each provider's own
    native reasoning knob where the configured model supports one, and a
    model that rejects the param must degrade via a retry-without-it
    fallback rather than failing the whole agent call."""

    # -- Gemini: thinking_config --------------------------------------

    def test_gemini_effort_maps_to_thinking_budget(self):
        from pbicompass.agents.llm import GeminiClient

        client = GeminiClient(api_key="dummy")
        captured: dict = {}

        def fake_generate_content(*, model, contents, config):
            captured["config"] = config
            from types import SimpleNamespace
            return SimpleNamespace(text='{"ok": true}')

        client._client.models.generate_content = fake_generate_content
        out = client.complete_json("sys", "user", {"type": "object"}, effort="xhigh")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(captured["config"].thinking_config.thinking_budget, 24576)

    def test_gemini_max_effort_requests_dynamic_thinking_budget(self):
        from pbicompass.agents.llm import GeminiClient

        client = GeminiClient(api_key="dummy")
        captured: dict = {}

        def fake_generate_content(*, model, contents, config):
            captured["config"] = config
            from types import SimpleNamespace
            return SimpleNamespace(text='{"ok": true}')

        client._client.models.generate_content = fake_generate_content
        client.complete_json("sys", "user", {"type": "object"}, effort="max")

        self.assertEqual(captured["config"].thinking_config.thinking_budget, -1)

    def test_gemini_retries_without_thinking_config_on_client_error(self):
        from google.genai import errors as genai_errors
        from pbicompass.agents.llm import GeminiClient

        client = GeminiClient(api_key="dummy")
        calls: list = []

        def fake_generate_content(*, model, contents, config):
            calls.append(config)
            if len(calls) == 1:
                raise genai_errors.ClientError(400, {"error": {"message": "thinking not supported"}})
            from types import SimpleNamespace
            return SimpleNamespace(text='{"ok": true}')

        client._client.models.generate_content = fake_generate_content
        out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[0].thinking_config)
        self.assertIsNone(calls[1].thinking_config)

    # -- Cohere: thinking (reasoning-capable models only) --------------

    def test_cohere_thinking_not_sent_for_non_reasoning_model(self):
        from pbicompass.agents.llm import CohereClient

        client = CohereClient(api_key="dummy")  # default command-a-03-2025
        captured: dict = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace
            text = SimpleNamespace(type="text", text='{"ok": true}')
            return SimpleNamespace(message=SimpleNamespace(content=[text]))

        client._client.chat = fake_chat
        client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertNotIn("thinking", captured)

    def test_cohere_thinking_sent_for_reasoning_capable_model(self):
        from pbicompass.agents.llm import CohereClient

        client = CohereClient(model="command-a-reasoning", api_key="dummy")
        captured: dict = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace
            text = SimpleNamespace(type="text", text='{"ok": true}')
            return SimpleNamespace(message=SimpleNamespace(content=[text]))

        client._client.chat = fake_chat
        client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(captured["thinking"], {"type": "enabled", "token_budget": 8192})

    def test_cohere_retries_without_thinking_on_bad_request(self):
        import cohere
        from pbicompass.agents.llm import CohereClient

        client = CohereClient(model="command-a-reasoning", api_key="dummy")
        calls: list = []

        def fake_chat(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise cohere.BadRequestError(body={"message": "thinking not supported"})
            from types import SimpleNamespace
            text = SimpleNamespace(type="text", text='{"ok": true}')
            return SimpleNamespace(message=SimpleNamespace(content=[text]))

        client._client.chat = fake_chat
        out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("thinking", calls[0])
        self.assertNotIn("thinking", calls[1])

    # -- MeshAPI: reasoning_effort (o-series/gpt-5 models only) --------

    def _fake_openai_module(self, on_create):
        import sys
        from types import ModuleType, SimpleNamespace

        class _FakeCompletions:
            def create(self, **kwargs):
                return on_create(kwargs)

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                self.chat = _FakeChat()

        class BadRequestError(Exception):
            pass

        fake_module = ModuleType("openai")
        fake_module.OpenAI = _FakeOpenAI
        fake_module.BadRequestError = BadRequestError
        return fake_module

    @staticmethod
    def _fake_meshapi_response():
        from types import SimpleNamespace
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",
                                      message=SimpleNamespace(content='{"ok": true}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    def test_meshapi_sends_reasoning_effort_for_reasoning_capable_model(self):
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        captured: dict = {}

        def on_create(kwargs):
            captured.update(kwargs)
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="openai/gpt-5", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="medium")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(captured["reasoning_effort"], "medium")

    def test_meshapi_xhigh_and_max_clamp_to_high(self):
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        captured: dict = {}

        def on_create(kwargs):
            captured.update(kwargs)
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="openai/o3-mini", api_key="rsk_test")
            client.complete_json("sys", "user", {"type": "object"}, effort="max")

        self.assertEqual(captured["reasoning_effort"], "high")

    def test_meshapi_retries_without_reasoning_effort_on_bad_request(self):
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        calls: list = []

        def on_create(kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise fake_module.BadRequestError("reasoning_effort not supported")
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="openai/gpt-5", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("reasoning_effort", calls[0])
        self.assertNotIn("reasoning_effort", calls[1])

    # -- MeshAPI: reasoning_effort for DeepSeek's "Thinking" models -----

    def test_meshapi_reasoning_capable_recognizes_deepseek_thinking_models(self):
        from pbicompass.agents.llm import _meshapi_reasoning_capable

        reasoning_capable = [
            "deepseek/deepseek-v3.2-speciale",
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-r1",
            "deepseek/deepseek-r1-0528",
            "deepseek/deepseek-r1-distill-llama-70b",
        ]
        for model in reasoning_capable:
            with self.subTest(model=model):
                self.assertTrue(_meshapi_reasoning_capable(model))

        # Hybrid thinking/non-thinking DeepSeek models toggle reasoning via a
        # separate `reasoning.enabled` boolean MeshAPI exposes — this client
        # doesn't send that, so they must not be treated as reasoning_effort-
        # capable (matching the pre-existing "everything else" default).
        not_reasoning_capable = [
            "deepseek/deepseek-chat",
            "deepseek/deepseek-chat-v3-0324",
            "deepseek/deepseek-chat-v3.1",
            "deepseek/deepseek-v3.1-terminus",
            "deepseek/deepseek-v3.2",
            "deepseek/deepseek-v3.2-exp",
        ]
        for model in not_reasoning_capable:
            with self.subTest(model=model):
                self.assertFalse(_meshapi_reasoning_capable(model))

    def test_meshapi_sends_reasoning_effort_for_deepseek_speciale(self):
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        captured: dict = {}

        def on_create(kwargs):
            captured.update(kwargs)
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="deepseek/deepseek-v3.2-speciale", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(captured["reasoning_effort"], "high")

    # -- MeshAPI: graceful degradation when a model rejects structured output --

    def test_meshapi_retries_without_response_format_on_bad_request(self):
        # Regression: MeshAPI's own dashboard lists the new deepseek-v4-flash
        # default's "Structured Output" capability as unsupported — a
        # rejected response_format must fall back to a plain-text JSON
        # instruction rather than failing the whole agent call.
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        calls: list = []

        def on_create(kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise fake_module.BadRequestError("response_format not supported")
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            # Not a reasoning-capable model id, so there's exactly one
            # fallback tier to exercise here (structured output) rather than
            # two — see the combined test below for both tiers at once.
            client = MeshAPIClient(model="openai/gpt-4o", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])
        # The schema is restated as a plain-text instruction on the fallback.
        self.assertIn("JSON schema", calls[1]["messages"][0]["content"])

    def test_meshapi_falls_back_through_both_reasoning_and_structured_output(self):
        # A model that looks reasoning-capable (per _meshapi_reasoning_capable)
        # but rejects *both* reasoning_effort and response_format needs two
        # fallback tiers, not one — the retry loop must not give up after the
        # first failure just because reasoning_effort was involved.
        import sys
        from unittest.mock import patch
        from pbicompass.agents.llm import MeshAPIClient

        calls: list = []

        def on_create(kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise fake_module.BadRequestError("unrecognized argument")
            return self._fake_meshapi_response()

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="deepseek/deepseek-v4-flash", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="high")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertIn("reasoning_effort", calls[0])
        self.assertIn("response_format", calls[0])
        self.assertNotIn("reasoning_effort", calls[1])
        self.assertIn("response_format", calls[1])
        self.assertNotIn("reasoning_effort", calls[2])
        self.assertNotIn("response_format", calls[2])

    def test_meshapi_loose_json_parse_strips_code_fence(self):
        import sys
        from unittest.mock import patch
        from types import SimpleNamespace
        from pbicompass.agents.llm import MeshAPIClient

        def on_create(kwargs):
            if "response_format" in kwargs:
                raise fake_module.BadRequestError("response_format not supported")
            return SimpleNamespace(
                choices=[SimpleNamespace(finish_reason="stop",
                                          message=SimpleNamespace(content='```json\n{"ok": true}\n```'))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

        fake_module = self._fake_openai_module(on_create)
        with patch.dict(sys.modules, {"openai": fake_module}):
            client = MeshAPIClient(model="openai/gpt-4o", api_key="rsk_test")
            out = client.complete_json("sys", "user", {"type": "object"})

        self.assertEqual(out, {"ok": True})

    # -- Anthropic: graceful degradation on a rejected effort tier -----

    def test_anthropic_retries_without_effort_on_bad_request(self):
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch

        class BadRequestError(Exception):
            pass

        calls: list = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise BadRequestError("effort not supported")
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text='{"ok": true}')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        class _FakeMessages:
            create = staticmethod(fake_create)

        class _FakeAnthropic:
            def __init__(self, **kwargs):
                self.messages = _FakeMessages()

        fake_module = ModuleType("anthropic")
        fake_module.Anthropic = _FakeAnthropic
        fake_module.BadRequestError = BadRequestError

        with patch.dict(sys.modules, {"anthropic": fake_module}):
            from pbicompass.agents.llm import AnthropicClient
            client = AnthropicClient(api_key="dummy")
            out = client.complete_json("sys", "user", {"type": "object"}, effort="max")

        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertIn("effort", calls[0]["output_config"])
        self.assertNotIn("effort", calls[1]["output_config"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
