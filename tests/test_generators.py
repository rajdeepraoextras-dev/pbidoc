"""Phase 1 + 2 + 3 tests: the document generators layer
(``pbicompass.agents.generators``) — ``AuditReportGenerator``,
``ExecutiveSummaryGenerator``, and ``BusinessGuideGenerator`` end-to-end,
plus the ``TechnicalDocumentationGenerator`` compatibility shim.

The LLM path is exercised with in-process fake clients, mirroring the
pattern in ``test_agents.py``, so no API key or network is required.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pbicompass.agents import generate_document
from pbicompass.agents.generators.technical import _join_caveat
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


def _fake_report_intelligence_response() -> dict:
    """Canned schema-valid ``ModelInsights`` (Phase 2) — every generator here
    that builds its own ``ai_context`` (executive/user-guide) triggers the
    Report Intelligence pass before its own agent call, so any fake client
    reaching ``build_job_context`` needs this branch too."""
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


class FakeAuditNarratorClient:
    """Returns a canned narrative for the Audit Narrator system prompt, and a
    canned cluster for the Audit Synthesizer system prompt (Day 7)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "root-cause synthesis" in system:
            return {
                "clusters": [
                    {
                        "root_cause": "FAKE_ROOT_CAUSE",
                        "rule_ids": ["PBIC-PERF-007"],
                        "narrative": "FAKE_CLUSTER_NARRATIVE",
                        "confidence": "High",
                    }
                ],
                "strategic_narrative": "FAKE_STRATEGIC_NARRATIVE",
            }
        if "Audit & Health Report" in system:
            return {"narrative_overview": "FAKE_NARRATIVE_OVERVIEW"}
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        raise AssertionError("unexpected system prompt")


class FakeExecutiveWriterClient:
    """Returns canned prose for the Executive Writer system prompt, and a
    canned business definition for the DAX Translator prompt Key KPIs also
    call now (P3)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
        if "executive summary" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "business_purpose": "FAKE_BUSINESS_PURPOSE",
                "business_value": "FAKE_BUSINESS_VALUE",
                "maintenance_overview": "FAKE_MAINTENANCE_OVERVIEW",
                "reframed_risks": [
                    {"rule_id": r["rule_id"], "consequence": f"FAKE_CONSEQUENCE {i}", "ask": f"FAKE_ASK {i}"}
                    for i, r in enumerate(payload["known_risks"])
                ],
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
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        if "fact-checker" in system:  # the grounding pass (Phase 3)
            return {"claims": []}
        raise AssertionError("unexpected system prompt")


class FailingClient:
    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        raise RuntimeError("boom")


class DocumentTypesRegistryTest(unittest.TestCase):
    def test_registry_contains_all_document_types(self):
        self.assertEqual(set(DOCUMENT_TYPES), {"technical", "audit", "executive", "user-guide"})
        self.assertIs(DOCUMENT_TYPES["technical"], TechnicalDocumentationGenerator)
        self.assertIs(DOCUMENT_TYPES["audit"], AuditReportGenerator)
        self.assertIs(DOCUMENT_TYPES["executive"], ExecutiveSummaryGenerator)
        self.assertIs(DOCUMENT_TYPES["user-guide"], BusinessGuideGenerator)


class JoinCaveatTest(unittest.TestCase):
    """P2: the measure catalog's "operates on a different table" caveat
    must never double up terminal punctuation when appended onto an
    existing caveat sentence ("...date filters.. Housed in...")."""

    def test_appends_with_exactly_one_period_when_existing_already_ends_in_one(self):
        result = _join_caveat("Uses date filters.", "Housed in 'X' table but operates on 'Y' table.")
        self.assertEqual(result, "Uses date filters. Housed in 'X' table but operates on 'Y' table.")
        self.assertNotIn("..", result)

    def test_adds_a_period_when_existing_has_none(self):
        result = _join_caveat("Uses date filters", "Second sentence.")
        self.assertEqual(result, "Uses date filters. Second sentence.")

    def test_empty_existing_returns_note_unchanged(self):
        self.assertEqual(_join_caveat("", "Only sentence."), "Only sentence.")
        self.assertEqual(_join_caveat(None, "Only sentence."), "Only sentence.")


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
        # 1 Audit Narrator call + 1 Audit Synthesizer call (Day 7) + 1 critic pass (5.3).
        self.assertEqual(client.calls, 3)
        # everything else stays deterministic even with an LLM client supplied
        deterministic_doc = AuditReportGenerator.generate(_model())
        self.assertEqual(doc.health, deterministic_doc.health)
        self.assertEqual(doc.recommendations, deterministic_doc.recommendations)

    def test_llm_synthesizer_clusters_are_used(self):
        client = FakeAuditNarratorClient()
        doc = AuditReportGenerator.generate(_model(), client)
        self.assertEqual(len(doc.clusters), 1)
        self.assertEqual(doc.clusters[0].root_cause, "FAKE_ROOT_CAUSE")
        self.assertEqual(doc.clusters[0].rule_ids, ["PBIC-PERF-007"])
        self.assertEqual(doc.clusters[0].confidence, "High")
        self.assertEqual(doc.strategic_narrative, "FAKE_STRATEGIC_NARRATIVE")

    def test_failing_client_leaves_clusters_empty(self):
        doc = AuditReportGenerator.generate(_model(), FailingClient(), on_warning=lambda _m: None)
        self.assertEqual(doc.clusters, [])
        self.assertEqual(doc.strategic_narrative, "")

    def test_failing_client_falls_back_to_deterministic_overview(self):
        warnings = []
        doc = AuditReportGenerator.generate(
            _model(), FailingClient(), on_warning=warnings.append,
        )
        self.assertTrue(warnings)
        self.assertIn(str(doc.health.overall), doc.narrative_overview)


class FakePuntLeakClient:
    """Simulates the corrupted output seen in production (P0): the Audit
    Narrator/Synthesizer responses carry the leaked "Unknown — requires
    business confirmation." punt sentence — the shape a mis-firing
    grounding pass leaves behind — plus a wrong health-score number in the
    narrator's own prose, one point off the model's actual computed score."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "root-cause synthesis" in system:
            return {
                "clusters": [
                    {
                        "root_cause": "Auto Date/Time feature enabled",
                        "rule_ids": ["PBIC-PERF-007"],
                        "narrative": (
                            "Address the Unknown — requires business confirmation. Its resolution will "
                            "both eliminate unused calculated columns and Unknown — requires business "
                            "confirmation. Unknown — requires business confirmation."
                        ),
                        "confidence": "High",
                    }
                ],
                "strategic_narrative": (
                    "The Unknown — requires business confirmation. Unknown — requires business confirmation."
                ),
            }
        if "Audit & Health Report" in system:
            import json as _json
            payload = _json.loads(user)
            wrong_score = payload["health_overall"] - 1
            return {"narrative_overview": (
                f"The overall health score of this model is {wrong_score}, categorized as "
                f"'{payload['health_band']}'. The governance and unused assets components are the "
                "primary areas limiting a higher score, Unknown — requires business confirmation. "
                "Unknown — requires business confirmation. Immediate attention should be directed "
                "towards those."
            )}
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        raise AssertionError("unexpected system prompt")


class AuditPuntLeakAndScoreConsistencyTest(unittest.TestCase):
    """P0 blockers: the punt-phrase leak and the score-number contradiction
    must never survive into the rendered document, regardless of what an
    LLM narrator/synthesizer returns."""

    @classmethod
    def setUpClass(cls):
        cls.client = FakePuntLeakClient()
        cls.doc = AuditReportGenerator.generate(_model(), cls.client)

    def test_punt_phrase_never_appears_in_narrative_overview(self):
        self.assertNotIn("requires business confirmation", self.doc.narrative_overview.lower())

    def test_punt_phrase_never_appears_in_strategic_narrative(self):
        self.assertNotIn("requires business confirmation", self.doc.strategic_narrative.lower())

    def test_punt_phrase_never_appears_in_cluster_narratives(self):
        for cluster in self.doc.clusters:
            self.assertNotIn("requires business confirmation", cluster.narrative.lower())

    def test_punt_phrase_never_appears_anywhere_in_document_json(self):
        self.assertNotIn("requires business confirmation", self.doc.to_json().lower())

    def test_narrative_overview_still_carries_real_content(self):
        # The strip must not gut the paragraph down to nothing — the score
        # sentence and the trailing real sentence both survive.
        self.assertIn(str(self.doc.health.overall), self.doc.narrative_overview)
        self.assertIn("Immediate attention should be directed towards those.", self.doc.narrative_overview)

    def test_score_number_matches_the_actual_computed_score(self):
        # The narrator claimed health.overall - 1; the post-check must
        # have replaced that sentence with the real number.
        self.assertIn(f"is {self.doc.health.overall}", self.doc.narrative_overview)
        self.assertNotIn(str(self.doc.health.overall - 1), self.doc.narrative_overview)


class FakeAiFixSnippetClient:
    """Routes the Audit Narrator/Synthesizer/critic calls to inert canned
    responses (so a test isolates the Day 9 AI Fix Snippet Writer call) and
    echoes back a fake DAX snippet for every recommendation it's asked
    about, keyed by the ``rule_id`` the caller must round-trip."""

    def __init__(self, code: str = "FAKE_DAX_CODE", language: str = "dax"):
        self.calls = 0
        self.fix_snippet_requests: list[list[dict]] = []
        self._code = code
        self._language = language

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "concrete code sketches" in system:  # AI Fix Snippet Writer (Day 9)
            payload = json.loads(user)
            self.fix_snippet_requests.append(payload["recommendations"])
            return {
                "snippets": [
                    {"rule_id": item["rule_id"], "language": self._language, "code": self._code}
                    for item in payload["recommendations"]
                ]
            }
        if "root-cause synthesis" in system:
            return {"clusters": [], "strategic_narrative": ""}
        if "Audit & Health Report" in system:
            return {"narrative_overview": "FAKE_NARRATIVE_OVERVIEW"}
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        raise AssertionError(f"unexpected system prompt: {system[:80]!r}")


class AuditGeneratorAiFixSnippetTest(unittest.TestCase):
    """Day 9: AI-suggested fix snippets are a paid, plan-gated add-on to the
    top-N prose-only recommendations — never on the free plan, never
    duplicating a recommendation that already has a deterministic code
    fence, and always fenced (so the critic/grounding passes skip them, per
    their own ``"```" in text`` guard)."""

    def test_free_plan_omits_ai_fix_snippets(self):
        client = FakeAiFixSnippetClient()
        doc = AuditReportGenerator.generate(_model(), client, plan="free")
        self.assertEqual(client.fix_snippet_requests, [])
        self.assertFalse(any("AI-suggested" in r.suggested_fix for r in doc.recommendations))

    def test_no_plan_specified_omits_ai_fix_snippets(self):
        """The CLI/offline default (``plan=None``) must not silently grant
        the paid feature — an explicit pro/enterprise plan is required."""
        client = FakeAiFixSnippetClient()
        doc = AuditReportGenerator.generate(_model(), client)
        self.assertEqual(client.fix_snippet_requests, [])
        self.assertFalse(any("AI-suggested" in r.suggested_fix for r in doc.recommendations))

    def test_pro_plan_appends_fenced_ai_suggested_snippet(self):
        client = FakeAiFixSnippetClient()
        doc = AuditReportGenerator.generate(_model(), client, plan="pro")
        touched = [r for r in doc.recommendations if "AI-suggested — review before applying" in r.suggested_fix]
        self.assertTrue(touched)
        for r in touched:
            self.assertIn("```dax\nFAKE_DAX_CODE\n```", r.suggested_fix)

    def test_enterprise_plan_also_gets_ai_fix_snippets(self):
        client = FakeAiFixSnippetClient()
        doc = AuditReportGenerator.generate(_model(), client, plan="enterprise")
        self.assertTrue(any("AI-suggested" in r.suggested_fix for r in doc.recommendations))

    def test_candidates_are_bounded_to_top_n_and_exclude_already_fenced(self):
        client = FakeAiFixSnippetClient()
        doc = AuditReportGenerator.generate(_model(), client, plan="pro")
        self.assertEqual(len(client.fix_snippet_requests), 1)
        requested_rule_ids = {item["rule_id"] for item in client.fix_snippet_requests[0]}
        self.assertLessEqual(len(requested_rule_ids), 3)
        # Recommendations that already carry a deterministic code fence
        # (e.g. PBIC-MOD-001/015's Tabular Editor scripts) must never be
        # re-sent to the AI Fix Snippet Writer.
        already_fenced = {r.rule_id for r in doc.recommendations if r.rule_id
                          and "AI-suggested" not in r.suggested_fix and "```" in r.suggested_fix}
        self.assertTrue(already_fenced)
        self.assertEqual(requested_rule_ids & already_fenced, set())

    def test_meta_commentary_snippet_is_rejected_not_appended(self):
        client = FakeAiFixSnippetClient(code="Consider providing a more specific fix here.")
        before = AuditReportGenerator.generate(_model(), None).recommendations
        doc = AuditReportGenerator.generate(_model(), client, plan="pro")
        self.assertTrue(client.fix_snippet_requests)  # the call did happen
        for r_before, r_after in zip(before, doc.recommendations):
            self.assertEqual(r_before.suggested_fix, r_after.suggested_fix)

    def test_failing_client_leaves_recommendations_untouched(self):
        deterministic = AuditReportGenerator.generate(_model(), None).recommendations
        doc = AuditReportGenerator.generate(
            _model(), FailingClient(), plan="pro", on_warning=lambda _m: None,
        )
        self.assertEqual(
            [r.suggested_fix for r in doc.recommendations],
            [r.suggested_fix for r in deterministic],
        )

    def test_critic_pass_does_not_alter_the_fenced_ai_snippet(self):
        """The critic (5.3) skips any field containing a fenced code block
        (``critic.py::apply_critic_pass``'s ``"```" in text`` guard) — this
        proves that guard actually protects the new Day 9 snippet end to
        end, not just in critic.py's own unit tests."""
        client = FakeAiFixSnippetClient(code="SUM ( Sales[Amount] )")
        doc = AuditReportGenerator.generate(_model(), client, plan="pro")
        touched = [r for r in doc.recommendations if "AI-suggested" in r.suggested_fix]
        self.assertTrue(touched)
        for r in touched:
            self.assertIn("```dax\nSUM ( Sales[Amount] )\n```", r.suggested_fix)


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

    def test_no_technical_jargon_in_purpose(self):
        # concise and non-technical — no table names or "semantic model" talk
        for banned in ("DAX", "semantic model"):
            self.assertNotIn(banned, self.doc.purpose)

    def test_no_model_statistics_or_paths_outside_kpi_strip(self):
        # G.1: the exec doc no longer carries model/report statistics
        # tables or raw file paths — those live in the technical document.
        self.assertFalse(hasattr(self.doc, "model_statistics"))
        self.assertFalse(hasattr(self.doc, "report_statistics"))
        self.assertFalse(hasattr(self.doc, "architecture_overview"))
        for s in self.doc.data_source_types:
            self.assertNotRegex(s, r"[A-Za-z]:[\\/]")

    def test_top_risks_are_business_framed_and_carry_an_ask(self):
        # SampleSales has a known bidirectional Sales<->Date relationship —
        # the same finding the Audit & Health Report and technical document
        # surface (1.10), minus the "dax"-category findings whose issue text
        # names DAX constructs directly.
        self.assertTrue(any("bidirectional cross-filtering" in r.consequence for r in self.doc.top_risks))
        for risk in self.doc.top_risks:
            self.assertNotIn("DAX", risk.consequence)
            self.assertNotIn("USERELATIONSHIP", risk.consequence)
            self.assertNotIn("DAX", risk.ask)
            self.assertNotIn("CROSSFILTER", risk.ask)
            self.assertNotIn("VAR", risk.ask)
            self.assertTrue(risk.ask)

    def test_top_risks_match_audit_engine_severity_order(self):
        # 1.10: exec top_risks are a filtered subset of the same
        # recommendation list the audit/technical docs show, in the same
        # severity order — never independently re-derived.
        order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        ranks = [order[r.severity] for r in self.doc.top_risks]
        self.assertEqual(ranks, sorted(ranks))

    def test_top_risks_carry_a_rule_id_for_deep_linking(self):
        # I5: every risk sourced from a rule-backed finding must carry the
        # rule_id so the rendered doc can deep-link to the exact finding.
        self.assertTrue(any(r.rule_id for r in self.doc.top_risks))

    def test_key_kpis_exclude_text_measures_and_carry_a_meaning(self):
        # Real usage-based selection (1.6): each KPI names its own meaning.
        for kpi in self.doc.key_kpis:
            self.assertIn(" — ", kpi)

    def test_data_source_types_include_sql_and_never_a_path(self):
        self.assertTrue(any("SQL database" in d for d in self.doc.data_source_types))

    def test_next_steps_reuse_audit_engine(self):
        self.assertTrue(self.doc.next_steps)

    def test_next_steps_never_show_doc_completeness_nag(self):
        # D1: document-completeness is an internal production concern, not
        # something an executive reader needs to see — it must never appear
        # in the rendered next_steps, only as a job warning (below).
        for s in self.doc.next_steps:
            self.assertNotIn("% complete", s.action)
            self.assertNotIn("still need business", s.action)

    def test_incomplete_metadata_surfaces_as_a_warning_not_doc_content(self):
        warnings = []
        ExecutiveSummaryGenerator.generate(_model(), on_warning=warnings.append)
        self.assertTrue(any("still need business input" in w for w in warnings))

    def test_maintenance_note_has_no_governance_or_audit_jargon(self):
        # D1: "governance finding(s)"/"best-practice gap(s)" is audit-speak
        # that leaked into the executive doc — the plain-language rewrite
        # must never use those terms, whether items are outstanding or not.
        for banned in ("governance finding", "best-practice gap", "best practice gap"):
            self.assertNotIn(banned, self.doc.maintenance_note.lower())

    def test_next_steps_do_not_repeat_top_risks(self):
        # P6: §11 Future Recommendations used to draw from the same
        # top-severity slice of the recommendation list as §9 Known Risks,
        # so the same issue appeared under both headings — now one merged,
        # ranked list, so what's left for "next steps" is disjoint by
        # construction.
        risk_consequences = [r.consequence for r in self.doc.top_risks]
        for step in self.doc.next_steps:
            for consequence in risk_consequences:
                self.assertNotIn(consequence, step.action)

    def test_ownership_fields_present(self):
        doc = ExecutiveSummaryGenerator.generate(_model(), owner="Jane Doe", classification="Confidential")
        self.assertEqual(doc.metadata.owner, "Jane Doe")
        self.assertEqual(doc.classification, "Confidential")

    def test_to_json_round_trips(self):
        text = self.doc.to_json()
        self.assertIn('"document_type": "executive"', text)
        self.assertIn('"purpose"', text)
        self.assertIn('"top_risks"', text)
        self.assertIn('"next_steps"', text)

    # -- Day 5: boardroom-grade pass ------------------------------------

    def test_health_score_matches_audit_engine(self):
        # The exec doc's score must never be independently re-derived from
        # the audit report's — same rule engine, same inputs.
        from pbicompass.agents import audit_rules

        model = _model()
        measures = model.all_measures()
        expected = audit_rules.compute_health_score(
            audit_rules.find_dax_findings(measures),
            audit_rules.check_best_practices(model),
            audit_rules.find_performance_risks(model),
            audit_rules.check_governance(model),
            audit_rules.find_unused_assets(model),
        )
        doc = ExecutiveSummaryGenerator.generate(_model())
        self.assertEqual(doc.health.overall, expected.overall)
        self.assertEqual(doc.health.band, expected.band)

    def test_health_score_never_names_dax(self):
        # This document never names implementation terms — the "dax"
        # component must get its own business-safe label wherever it's
        # rendered (never bare "DAX" from HEALTH_COMPONENT_LABELS).
        self.assertIn("dax", self.doc.health.component_scores)

    def test_next_steps_are_structured_rows(self):
        for step in self.doc.next_steps:
            self.assertIn(step.severity, ("Critical", "High", "Medium", "Low"))
            self.assertTrue(step.action)
            self.assertIn(step.effort, ("Low", "Medium", "High"))

    def test_next_steps_capped_at_five(self):
        self.assertLessEqual(len(self.doc.next_steps), 5)

    def test_page_thumbnails_reuse_technical_docs_wireframe_svg(self):
        # Reuses the same SVG report_facts.report_pages() builds for the
        # technical document/user guide — never a second drawing.
        from pbicompass.agents.report_facts import report_pages

        expected = [p["wireframe_svg"] for p in report_pages(_model()) if not p["hidden"]]
        self.assertTrue(self.doc.page_thumbnails)
        self.assertEqual([t.svg for t in self.doc.page_thumbnails], expected[: len(self.doc.page_thumbnails)])
        self.assertEqual(self.doc.page_count, len(expected))

    def test_page_thumbnails_skip_hidden_pages(self):
        names = [t.name for t in self.doc.page_thumbnails]
        model = _model()
        hidden_names = {p.display_name for p in model.pages if p.is_hidden}
        self.assertFalse(hidden_names & set(names))

    def test_data_source_never_shows_raw_connector_name(self):
        # File.Contents/Excel.Workbook/etc. are internal Power Query
        # connector names, never a reader-facing label.
        for s in self.doc.data_source_types:
            self.assertNotIn("File.Contents", s)
            self.assertNotRegex(s, r"[A-Z][a-z]+\.[A-Z][a-zA-Z]+")


class ExecutiveGeneratorLlmTest(unittest.TestCase):
    def test_llm_prose_is_used(self):
        client = FakeExecutiveWriterClient()
        doc = ExecutiveSummaryGenerator.generate(_model(), client)
        self.assertEqual(doc.purpose, "FAKE_BUSINESS_PURPOSE")
        self.assertEqual(doc.business_value, "FAKE_BUSINESS_VALUE")
        self.assertEqual(doc.maintenance_note, "FAKE_MAINTENANCE_OVERVIEW")
        # 1 Report Intelligence call (Phase 2's whole-model synthesis pass,
        # run once by build_job_context before any other agent) + 1
        # Executive Writer call + 1 DAX Translator batch call (P3: Key KPI
        # meanings reuse the same DAX Translator agent as the technical doc)
        # + 1 critic pass (5.3) + 1 grounding pass (Phase 3).
        self.assertEqual(client.calls, 5)
        self.assertTrue(any("FAKE_KPI_MEANING" in kpi for kpi in doc.key_kpis))
        # deterministic facts (severity, rule_id, next_steps) stay identical
        # regardless of the LLM client; only the risk wording is reframed
        # into business language (D1 — reframed_risks).
        deterministic_doc = ExecutiveSummaryGenerator.generate(_model())
        self.assertEqual([r.severity for r in doc.top_risks], [r.severity for r in deterministic_doc.top_risks])
        self.assertEqual([r.rule_id for r in doc.top_risks], [r.rule_id for r in deterministic_doc.top_risks])
        self.assertTrue(doc.top_risks)
        for r in doc.top_risks:
            self.assertTrue(r.consequence.startswith("FAKE_CONSEQUENCE"))
            self.assertTrue(r.ask.startswith("FAKE_ASK"))
        self.assertEqual(doc.next_steps, deterministic_doc.next_steps)

    def test_failing_client_falls_back_to_deterministic_prose(self):
        warnings = []
        doc = ExecutiveSummaryGenerator.generate(
            _model(), FailingClient(), on_warning=warnings.append,
        )
        self.assertTrue(warnings)
        self.assertNotEqual(doc.purpose, "")
        self.assertNotIn("FAKE", doc.purpose)


class ApplyReframedRisksTest(unittest.TestCase):
    """D1: a mismatched-count/shape response from the Executive Writer must
    never be trusted to overwrite the deterministic risk wording — it's
    silently ignored rather than applied partially or out of order."""

    def _risks(self):
        from pbicompass.schemas.executive_document import ExecutiveRisk
        return [
            ExecutiveRisk(severity="High", consequence="orig consequence 1", ask="orig ask 1", rule_id="R1"),
            ExecutiveRisk(severity="Medium", consequence="orig consequence 2", ask="orig ask 2", rule_id="R2"),
        ]

    def test_applies_matching_count_response(self):
        from pbicompass.agents.generators.executive import _apply_reframed_risks
        risks = self._risks()
        _apply_reframed_risks(risks, [
            {"rule_id": "R1", "consequence": "new consequence 1", "ask": "new ask 1"},
            {"rule_id": "R2", "consequence": "new consequence 2", "ask": "new ask 2"},
        ])
        self.assertEqual(risks[0].consequence, "new consequence 1")
        self.assertEqual(risks[1].ask, "new ask 2")

    def test_ignores_mismatched_count_response(self):
        from pbicompass.agents.generators.executive import _apply_reframed_risks
        risks = self._risks()
        _apply_reframed_risks(risks, [{"rule_id": "R1", "consequence": "new consequence 1", "ask": "new ask 1"}])
        self.assertEqual(risks[0].consequence, "orig consequence 1")
        self.assertEqual(risks[1].consequence, "orig consequence 2")

    def test_ignores_none_response(self):
        from pbicompass.agents.generators.executive import _apply_reframed_risks
        risks = self._risks()
        _apply_reframed_risks(risks, None)
        self.assertEqual(risks[0].consequence, "orig consequence 1")


class FakeUserGuideWriterClient:
    """Returns canned prose for the User Guide Writer system prompt, and a
    canned business definition for the DAX Translator prompt the glossary
    also calls now (P3)."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
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
        if "expert technical editor" in system:  # the critic pass (5.3)
            return {"violations": []}
        if "fact-checker" in system:  # the grounding pass (Phase 3)
            return {"claims": []}
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
        # 1 Report Intelligence call (Phase 2's whole-model synthesis pass,
        # run once by build_job_context before any other agent) + 1 User
        # Guide Writer call + 1 DAX Translator batch call (P3: the glossary
        # reuses the same DAX Translator agent as the technical doc instead
        # of only ever falling back to the deterministic gloss) + 1 critic
        # pass (5.3) + 1 grounding pass (Phase 3).
        self.assertEqual(client.calls, 5)
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


class _PuntingGlossaryClient:
    """D2/D6: the DAX Translator returns a bare punt phrase for every
    measure's business meaning — the glossary must fall back to its own
    deterministic gloss rather than shipping the punt as a definition."""

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        if "Report Intelligence" in system:
            return _fake_report_intelligence_response()
        if "Business User Guide" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "introduction": "FAKE_INTRODUCTION",
                "pages": [
                    {"page_title": p["page_title"], "purpose": "FAKE_PURPOSE", "common_scenarios": []}
                    for p in payload["pages"]
                ],
            }
        if "senior DAX developer" in system:
            import json as _json
            payload = _json.loads(user)
            return {
                "translations": [
                    {"name": m["name"],
                     "plain_english": "Business meaning could not be inferred automatically; requires business confirmation.",
                     "calculation_logic": "", "caveats": "", "category": "Other", "confidence": "Low"}
                    for m in payload["measures"]
                ]
            }
        if "expert technical editor" in system:
            return {"violations": []}
        if "fact-checker" in system:
            return {"claims": []}
        raise AssertionError("unexpected system prompt")


class BusinessGuideGlossaryAntiPuntTest(unittest.TestCase):
    def test_glossary_never_ships_a_punt_as_a_definition(self):
        doc = BusinessGuideGenerator.generate(_model(), _PuntingGlossaryClient())
        for term in doc.glossary:
            self.assertNotIn("requires business confirmation", term.plain_definition)


if __name__ == "__main__":
    unittest.main(verbosity=2)
