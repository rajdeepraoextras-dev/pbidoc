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


class FakeLLMClient:
    """Returns canned schema-valid JSON, routed by the agent's system prompt."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
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
                get_client("mesh", model="anthropic/claude-opus-4-8")

    def test_meshapi_maps_effort_and_parses_response(self):
        # Stub out the 'openai' package (it may not be installed in this
        # environment — MeshAPI is deliberately implemented on top of the
        # official OpenAI SDK pointed at MeshAPI's base URL) so
        # MeshAPIClient's lazy import resolves to a fake client we control,
        # mirroring the Cohere content-parsing regression test above.
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
            client = MeshAPIClient(model="anthropic/claude-opus-4-8", api_key="rsk_test", effort="xhigh")
            out = client.complete_json("sys", "user", {"type": "object"})

        self.assertEqual(out, {"ok": True})
        # xhigh has no MeshAPI equivalent — maps down to its ceiling, "high".
        self.assertEqual(captured["reasoning_effort"], "high")
        self.assertEqual(captured["model"], "anthropic/claude-opus-4-8")
        self.assertEqual(client.last_usage, {"input_tokens": 10, "output_tokens": 5})

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
