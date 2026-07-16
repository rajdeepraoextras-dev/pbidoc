"""Tests for the cross-artifact consistency pass (Day 2): ``agents/consistency.py``'s
deterministic fixed-vocabulary checker, LLM-routed checker, and end-to-end
wiring into the document generators."""

from __future__ import annotations

import unittest
from pathlib import Path

from pbicompass.agents.consistency import (
    AuditVerdicts,
    apply_consistency_pass,
    build_audit_verdicts,
    check_consistency,
    check_deterministic_consistency,
    find_human_claim_discrepancies,
)
from pbicompass.agents.critic import apply_results
from pbicompass.agents.generators import (
    AuditReportGenerator,
    ExecutiveSummaryGenerator,
    TechnicalDocumentationGenerator,
)
from pbicompass.parsers import detect_and_parse
from pbicompass.schemas.model import SemanticModel

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"
CS_FIXTURE = Path(__file__).parent / "fixtures" / "CorporateSpend" / "model.json"


def _model():
    return detect_and_parse(FIXTURE)


def _cs_model():
    return SemanticModel.from_json(CS_FIXTURE.read_text(encoding="utf-8"))


def _verdicts(**overrides) -> AuditVerdicts:
    base = dict(
        schema_shape="a snowflake schema (dimensions relate to other dimensions)",
        is_star_schema=False,
        fact_count=1,
        dim_count=4,
        rls_role_count=2,
        refresh_configured=True,
        description_coverage_pct=40,
    )
    base.update(overrides)
    return AuditVerdicts(**base)


class HumanClaimRlsDiscrepancyTest(unittest.TestCase):
    """A live run surfaced the gap: the intake note "RLS restricts each
    department head to their own cost centers" (model defines 0 roles) raised no
    discrepancy, because the detector only knew validated/tested/configured/...
    The note was then appended to the finding as if true and the AI blended the
    contradiction into incoherent prose ("RLS is not configured ... Ask: review
    RLS role memberships quarterly")."""

    def test_the_live_note_that_slipped_through_now_fires(self):
        d = find_human_claim_discrepancies(
            "RLS restricts each department head to their own cost centers.", 0)
        self.assertEqual(len(d), 1)
        self.assertIn("0 row-level security roles", d[0].model_finding)

    def test_operative_phrasings_fire(self):
        for note in ("Row-level security limits each manager to their region.",
                     "RLS validated in UAT.",
                     "RLS is enforced for all viewers.",
                     "Row-level security filters data by country.",
                     "RLS was set up for the finance team.",
                     "Row-level security segments data by business unit."):
            self.assertTrue(find_human_claim_discrepancies(note, 0), note)

    def test_negations_do_not_false_positive(self):
        """Pre-existing bug: "RLS is not configured" matched RLS...configured
        with nothing accounting for the "not", so a note that AGREES with the
        model (0 roles) raised a contradiction."""
        for note in ("RLS is not configured for this report.",
                     "No row-level security is applied.",
                     "No RLS needed - all viewers see everything.",
                     "RLS isn't enforced here.",
                     "This model has no roles."):
            self.assertEqual(find_human_claim_discrepancies(note, 0), [], note)

    def test_reverse_claim_still_fires(self):
        d = find_human_claim_discrepancies("No RLS is needed for this report.", 2)
        self.assertEqual(len(d), 1)
        self.assertIn("2 row-level security roles", d[0].model_finding)

    def test_no_notes_no_discrepancy(self):
        self.assertEqual(find_human_claim_discrepancies(None, 0), [])
        self.assertEqual(find_human_claim_discrepancies("", 5), [])

    def test_unreadable_rls_raises_no_false_contradiction(self):
        """A .pbix never exposes its roles, so a zero count there means
        "unknown", not "none". Claiming the owner's note contradicts the file
        would manufacture a false security alarm about a report that may be
        fully protected."""
        note = "RLS restricts each department head to their own cost centers."
        self.assertEqual(find_human_claim_discrepancies(note, 0, rls_readable=False), [])
        # ...but the same note against a readable model is a real contradiction.
        self.assertTrue(find_human_claim_discrepancies(note, 0, rls_readable=True))


class PbixRlsHonestyTest(unittest.TestCase):
    """A .pbix parse cannot see RLS. Reporting "No row-level security roles are
    defined in this model" there states as fact something the file cannot
    support — the report may be fully protected. Absence and unreadability must
    be different findings."""

    def _audit(self, source_format: str):
        from pbicompass.schemas.model import ModelMeta, Table
        from pbicompass.agents.generators import AuditReportGenerator
        model = SemanticModel(report_name="R", tables=[Table(name="Sales")],
                              meta=ModelMeta(source_format=source_format))
        return AuditReportGenerator.generate(model, None)

    def _rls_finding(self, doc):
        return next((g for g in doc.governance if "row-level" in (g.detail or "").lower()), None)

    def test_pbix_reports_unknown_not_absent(self):
        finding = self._rls_finding(self._audit("pbix"))
        self.assertEqual(finding.rule_id, "PBIC-GOV-012")
        self.assertIn("unknown", finding.detail.lower())
        self.assertNotIn("no row-level security roles are defined", finding.detail.lower())

    def test_pbip_still_reports_genuine_absence(self):
        finding = self._rls_finding(self._audit("pbip-tmdl"))
        self.assertEqual(finding.rule_id, "PBIC-GOV-011")
        self.assertIn("no row-level security roles are defined", finding.detail.lower())

    def test_pbix_technical_doc_does_not_claim_absence(self):
        from pbicompass.agents import generate_document
        from pbicompass.schemas.model import ModelMeta, Table
        model = SemanticModel(report_name="R", tables=[Table(name="Sales")],
                              meta=ModelMeta(source_format="pbix"))
        constraint = generate_document(model, None).security.workspace_constraints[0]
        self.assertIn("cannot be read from a .pbix", constraint)


class BuildAuditVerdictsTest(unittest.TestCase):
    def test_verdicts_reflect_the_audit_documents_own_computation(self):
        model = _model()
        audit_doc = AuditReportGenerator.generate(model)
        verdicts = build_audit_verdicts(model, audit_doc)

        from pbicompass.agents.deterministic import schema_shape
        shape, facts, dims = schema_shape(model)
        self.assertEqual(verdicts.schema_shape, shape)
        self.assertEqual(verdicts.fact_count, len(facts))
        self.assertEqual(verdicts.dim_count, len(dims))
        self.assertEqual(verdicts.rls_role_count, len(model.roles))
        star_check = next(c for c in audit_doc.best_practices if c.id == "star_schema")
        self.assertEqual(verdicts.is_star_schema, star_check.passed)


class CorporateSpendAuditVerdictsTest(unittest.TestCase):
    """Day 7: ``build_audit_verdicts`` against the real Corporate Spend
    fixture — a genuine 2-fact galaxy schema, not a hand-built star-schema
    fixture, so ``is_star_schema`` must correctly come back False and the
    fact/dimension counts must match the model's own real shape."""

    def test_verdicts_reflect_the_real_galaxy_schema(self):
        model = _cs_model()
        audit_doc = AuditReportGenerator.generate(model)
        verdicts = build_audit_verdicts(model, audit_doc)

        self.assertFalse(verdicts.is_star_schema, "Corporate Spend is a 2-fact galaxy schema, not a star schema")
        self.assertEqual(verdicts.fact_count, 2)
        self.assertEqual(verdicts.rls_role_count, 0)

    def test_false_star_schema_claim_is_corrected_against_the_real_galaxy_schema(self):
        # The exact P2/consistency scenario: an LLM narrator claiming a
        # star schema this specific fixture demonstrably does not have.
        model = _cs_model()
        audit_doc = AuditReportGenerator.generate(model)
        verdicts = build_audit_verdicts(model, audit_doc)

        doc = ExecutiveSummaryGenerator.generate(model)
        doc.purpose = "This report is built on a well-structured star schema for fast analysis."

        from pbicompass.agents.generators.executive import _narrative_triples

        triples = _narrative_triples(doc)
        fields = [(loc, text) for loc, text, _ in triples]
        results = check_consistency(fields, None, verdicts=verdicts)
        apply_results(triples, results)

        self.assertNotIn("star schema", doc.purpose)


class CheckDeterministicConsistencyTest(unittest.TestCase):
    def test_false_star_schema_claim_is_corrected(self):
        verdicts = _verdicts(is_star_schema=False, schema_shape="a snowflake schema (dimensions relate to other dimensions)")
        results = check_deterministic_consistency(
            [("a", "This report is built on a well-structured star schema for fast analysis.")],
            verdicts,
        )
        self.assertIn("a", results)
        self.assertNotIn("star schema", results["a"])
        self.assertIn("snowflake schema", results["a"])

    def test_true_star_schema_claim_is_left_untouched(self):
        verdicts = _verdicts(is_star_schema=True, schema_shape="a star schema centred on the 'Sales' fact table")
        results = check_deterministic_consistency(
            [("a", "This report is built on a star schema for fast analysis.")], verdicts,
        )
        self.assertEqual(results, {})

    def test_hedged_not_star_schema_claim_is_left_untouched(self):
        # Already correctly says it's not a star schema — nothing to fix.
        verdicts = _verdicts(is_star_schema=False)
        results = check_deterministic_consistency(
            [("a", "This model is not a star schema; it uses a layered dimension design.")], verdicts,
        )
        self.assertEqual(results, {})

    def test_no_rls_claim_is_corrected_when_roles_exist(self):
        verdicts = _verdicts(rls_role_count=3)
        results = check_deterministic_consistency(
            [("a", "No row-level security is configured for this report.")], verdicts,
        )
        self.assertIn("3 row-level security roles", results["a"])

    def test_wrong_rls_count_is_corrected(self):
        verdicts = _verdicts(rls_role_count=2)
        results = check_deterministic_consistency(
            [("a", "This model defines 5 RLS roles for regional access control.")], verdicts,
        )
        self.assertIn("2 RLS roles", results["a"])
        self.assertNotIn("5 RLS roles", results["a"])

    def test_correct_rls_count_is_left_untouched(self):
        verdicts = _verdicts(rls_role_count=2)
        results = check_deterministic_consistency(
            [("a", "This model defines 2 RLS roles for regional access control.")], verdicts,
        )
        self.assertEqual(results, {})

    def test_refresh_not_configured_claim_is_corrected_when_it_is(self):
        verdicts = _verdicts(refresh_configured=True)
        results = check_deterministic_consistency(
            [("a", "The refresh schedule is not configured for this report.")], verdicts,
        )
        self.assertIn("refresh is configured", results["a"])

    def test_wrong_fact_table_count_is_corrected(self):
        verdicts = _verdicts(fact_count=1)
        results = check_deterministic_consistency(
            [("a", "The model spans 3 fact tables feeding every dashboard.")], verdicts,
        )
        self.assertIn("1 fact table", results["a"])
        self.assertNotIn("3 fact tables", results["a"])

    def test_wrong_dimension_table_count_is_corrected(self):
        verdicts = _verdicts(dim_count=4)
        results = check_deterministic_consistency(
            [("a", "The model includes 9 dimension tables for slicing.")], verdicts,
        )
        self.assertIn("4 dimension tables", results["a"])

    def test_full_coverage_claim_is_corrected_when_partial(self):
        verdicts = _verdicts(description_coverage_pct=40)
        results = check_deterministic_consistency(
            [("a", "Every measure has a description, making the model self-documenting.")], verdicts,
        )
        self.assertIn("40%", results["a"])

    def test_full_coverage_claim_left_untouched_when_actually_full(self):
        verdicts = _verdicts(description_coverage_pct=100)
        results = check_deterministic_consistency(
            [("a", "All measures are documented for easy onboarding.")], verdicts,
        )
        self.assertEqual(results, {})

    def test_multiple_contradictions_in_one_field_all_corrected(self):
        verdicts = _verdicts(is_star_schema=False, rls_role_count=3,
                              schema_shape="a snowflake schema (dimensions relate to other dimensions)")
        results = check_deterministic_consistency(
            [("a", "This star schema model has no row-level security.")], verdicts,
        )
        self.assertIn("snowflake schema", results["a"])
        self.assertNotIn("no row-level security", results["a"])
        self.assertIn("3 row-level security roles", results["a"])

    def test_empty_text_is_skipped(self):
        results = check_deterministic_consistency([("a", "")], _verdicts())
        self.assertEqual(results, {})

    def test_clean_prose_with_no_claims_is_untouched(self):
        verdicts = _verdicts()
        results = check_deterministic_consistency(
            [("a", "This report tracks quarterly sales performance across regions.")], verdicts,
        )
        self.assertEqual(results, {})


class FakeConsistencyClient:
    def __init__(self, contradictions: list[dict]):
        self.contradictions = contradictions
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        self.calls += 1
        return {"contradictions": self.contradictions}


class ApplyConsistencyPassTest(unittest.TestCase):
    def test_offline_is_a_noop(self):
        results = apply_consistency_pass([("a", "Some claim.")], None, verdicts=_verdicts())
        self.assertEqual(results, {})

    def test_missing_verdicts_is_a_noop(self):
        client = FakeConsistencyClient([])
        results = apply_consistency_pass([("a", "Some claim.")], client, verdicts=None)
        self.assertEqual(results, {})
        self.assertEqual(client.calls, 0)

    def test_failing_client_degrades_silently_with_a_warning(self):
        class _FailingClient:
            def complete_json(self, system, user, schema, *, effort=None):
                raise RuntimeError("boom")

        warnings: list[str] = []
        results = apply_consistency_pass(
            [("a", "Some claim.")], _FailingClient(), verdicts=_verdicts(), warn=warnings.append,
        )
        self.assertEqual(results, {})
        self.assertTrue(any("Consistency" in w for w in warnings))

    def test_reported_contradiction_is_applied(self):
        client = FakeConsistencyClient([
            {"location": "a", "quote": "used by every department company-wide",
             "correction": "used by the finance team"},
        ])
        results = apply_consistency_pass(
            [("a", "This report is used by every department company-wide.")],
            client, verdicts=_verdicts(),
        )
        self.assertEqual(results["a"], "This report is used by the finance team.")

    def test_quote_not_present_is_ignored(self):
        client = FakeConsistencyClient([
            {"location": "a", "quote": "nonexistent phrase", "correction": "x"},
        ])
        results = apply_consistency_pass([("a", "Some other text.")], client, verdicts=_verdicts())
        self.assertEqual(results, {})

    def test_whole_measure_definition_cannot_be_replaced_by_audit_status(self):
        original = "Net sales after canceled orders are excluded."
        client = FakeConsistencyClient([{
            "location": "measure_catalog.measures[0].plain_english",
            "quote": original,
            "correction": "Description coverage: 100% of measures have a description.",
        }])
        results = apply_consistency_pass(
            [("measure_catalog.measures[0].plain_english", original)],
            client, verdicts=_verdicts(),
        )
        self.assertEqual(results, {})


class CheckConsistencyMergeTest(unittest.TestCase):
    def test_deterministic_and_llm_results_merge(self):
        client = FakeConsistencyClient([
            {"location": "b", "quote": "used by every department",
             "correction": "used by regional managers"},
        ])
        results = check_consistency(
            [("a", "This report is built on a well-structured star schema."),
             ("b", "This dashboard is used by every department.")],
            client, verdicts=_verdicts(is_star_schema=False,
                                       schema_shape="a snowflake schema (dimensions relate to other dimensions)"),
        )
        self.assertIn("snowflake schema", results["a"])
        self.assertEqual(results["b"], "This dashboard is used by regional managers.")

    def test_no_verdicts_available_is_a_noop(self):
        client = FakeConsistencyClient([{"location": "a", "quote": "x", "correction": "y"}])
        results = check_consistency([("a", "star schema x")], client, verdicts=None)
        self.assertEqual(results, {})
        self.assertEqual(client.calls, 0)


class ApplyResultsIntegrationTest(unittest.TestCase):
    def test_setters_receive_deterministic_consistency_corrections(self):
        sink = {}
        triples = [("a", "This report is a well-structured star schema design.",
                    lambda v: sink.__setitem__("a", v))]
        results = check_deterministic_consistency(
            [(loc, text) for loc, text, _ in triples],
            _verdicts(is_star_schema=False, schema_shape="a snowflake schema (dimensions relate to other dimensions)"),
        )
        apply_results(triples, results)
        self.assertIn("snowflake schema", sink["a"])


class ExecutiveGeneratorWiringTest(unittest.TestCase):
    """Day 2 end-to-end: a false star-schema claim seeded into the executive
    document's purpose, checked against audit verdicts, must be corrected in
    the final ExecutiveDocument via the same triples/apply_results mechanism
    the critic and grounding passes already use."""

    def test_false_star_schema_claim_is_corrected_against_audit_verdicts(self):
        model = _model()
        # Verdicts deliberately contradict this fixture's real shape — this
        # test exercises the wiring/correction mechanism, not whether
        # SampleSales itself happens to be a star schema (covered by
        # BuildAuditVerdictsTest instead).
        verdicts = _verdicts(is_star_schema=False,
                             schema_shape="a snowflake schema (dimensions relate to other dimensions)")

        doc = ExecutiveSummaryGenerator.generate(model)
        # Simulate an LLM having written a contradicting claim (offline mode
        # keeps generation deterministic, so we inject the contradiction the
        # same way GroundingGeneratorWiringTest does).
        doc.purpose = "This report is built on a well-structured star schema for fast analysis."

        from pbicompass.agents.generators.executive import _narrative_triples

        triples = _narrative_triples(doc)
        fields = [(loc, text) for loc, text, _ in triples]
        results = check_consistency(fields, None, verdicts=verdicts)
        apply_results(triples, results)

        self.assertNotIn("star schema", doc.purpose)
        self.assertIn("snowflake", doc.purpose.lower())


class _RlsContradictingClient:
    """A minimal LLMClient exercising every branch
    ``TechnicalDocumentationGenerator.generate`` calls with a client present,
    with the Business Analyst reporting a false "no RLS" claim — SampleSales
    genuinely defines 2 roles — so the generator's own internal
    ``_run_consistency`` call (wired into ``generate``, not invoked directly
    by the test) has a real contradiction to fix against the sibling Audit
    document's real verdicts."""

    def complete_json(self, system: str, user: str, schema: dict, *, effort: str | None = None) -> dict:
        if "consistency-checker" in system:
            # The false RLS claim is already fixed by the deterministic
            # layer before this LLM layer ever sees the text — nothing left
            # to report.
            return {"contradictions": []}
        if "fact-checker" in system:
            return {"claims": []}
        if "Report Intelligence" in system:
            return {
                "business_domain": "FAKE_DOMAIN",
                "report_purpose": {"statement": "FAKE_REPORT_PURPOSE", "confidence": "High"},
                "audience_hypotheses": [], "entity_definitions": [], "page_workflows": [],
                "kpi_relationships": [], "cross_cutting_observations": [], "data_quality_notes": [],
            }
        if "Business Analyst" in system or "BI consultant" in system:
            return {
                "core_purpose": "No row-level security is configured for this report.",
                "pages": [], "navigation_guide": [], "complex_visual_explainers": [],
            }
        if "senior DAX developer" in system or "DAX measures" in system:
            import json
            payload = json.loads(user)
            return {"translations": [
                {"name": m["name"], "plain_english": "A measure.",
                 "calculation_logic": "calc", "caveats": "", "category": "Other",
                 "confidence": "High"}
                for m in payload["measures"]
            ]}
        if "data-modeling" in system:
            return {"summary": "A model.", "risks": []}
        if "description for every column" in system or "Column Describer" in system:
            import json
            payload = json.loads(user)
            return {"columns": [
                {"table": c["table"], "column": c["column"], "description": "d"}
                for c in payload["columns"]
            ]}
        if "expert technical editor" in system:
            return {"violations": []}
        raise AssertionError(f"unexpected system prompt: {system[:60]}")


class TechnicalGeneratorConsistencyWiringTest(unittest.TestCase):
    """Day 2 "done when": a false RLS claim injected via a fake LLM client
    into the Business Analyst's output — checked entirely through
    ``TechnicalDocumentationGenerator.generate``'s own internal
    ``_run_consistency`` wiring, not by calling the consistency module
    directly — must be corrected in the final ``Document.executive_summary
    .core_purpose`` against the real, independently-computed Audit & Health
    Report verdict for this fixture (2 RLS roles). Deleting the
    ``_run_consistency(...)`` call from ``technical.py``'s ``generate``, or
    reverting ``check_deterministic_consistency``'s RLS check, makes this
    test fail."""

    def test_false_no_rls_claim_is_corrected_against_sibling_audit_doc(self):
        model = _model()
        self.assertEqual(len(model.roles), 2, "fixture must define RLS roles for this test to be meaningful")

        audit_doc = AuditReportGenerator.generate(model)
        verdicts = build_audit_verdicts(model, audit_doc)
        self.assertEqual(verdicts.rls_role_count, 2)

        doc = TechnicalDocumentationGenerator.generate(
            model, _RlsContradictingClient(), audit_verdicts=verdicts,
        )

        self.assertNotIn("No row-level security is configured", doc.executive_summary.core_purpose)
        self.assertIn("2 row-level security roles", doc.executive_summary.core_purpose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
