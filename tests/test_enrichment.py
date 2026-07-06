"""Tests for the enrichment round-trip and model-diff modules (Phase 5.1/5.2)."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from pbicompass.agents import generate_document
from pbicompass.agents.generators import AuditReportGenerator
from pbicompass.enrichment import (
    apply_enrichment,
    compute_model_diff,
    generate_change_log_markdown,
    generate_enrichment_template,
    get_model_fingerprint,
    load_enrichment,
)
from pbicompass.parsers import detect_and_parse
from pbicompass.render import (
    render_audit_docx,
    render_audit_html,
    render_audit_markdown,
    render_docx,
    render_html,
    render_markdown,
)

FIXTURE = Path(__file__).parent / "fixtures" / "SampleSales" / "SampleSales.pbip"


def _model():
    return detect_and_parse(FIXTURE)


class EnrichmentRoundTripTest(unittest.TestCase):
    def tearDown(self):
        # apply_enrichment's rules_config step (5.1) mutates audit_rules'
        # module-global override state — reset it so a suppression set by
        # one test can't leak into another test's audit findings.
        from pbicompass.agents import audit_rules
        audit_rules.set_rules_override_config({})

    def test_emit_load_emit_is_stable(self):
        model = _model()
        first = generate_enrichment_template(model)
        loaded = yaml.safe_load(first)
        second = generate_enrichment_template(model, previous=loaded)
        self.assertEqual(first, second)

    def test_filling_fields_then_regenerating_carries_them_forward(self):
        model = _model()
        skeleton = yaml.safe_load(generate_enrichment_template(model))
        skeleton["metadata"]["owner"] = "Jane Doe"
        skeleton["metadata"]["classification"] = "Confidential"
        first_measure = model.all_measures()[0].name
        skeleton["measure_descriptions"][first_measure] = "The total of all sales."

        overridden = apply_enrichment(model, skeleton)
        self.assertEqual(overridden["metadata"]["owner"], "Jane Doe")
        self.assertIn(first_measure, overridden["measures"])
        self.assertIn("owner", model.meta.overridden_fields)
        self.assertIn("classification", model.meta.overridden_fields)

        regenerated = yaml.safe_load(generate_enrichment_template(model, previous=skeleton))
        self.assertEqual(regenerated["metadata"]["owner"], "Jane Doe")
        self.assertEqual(regenerated["metadata"]["classification"], "Confidential")
        self.assertEqual(regenerated["measure_descriptions"][first_measure], "The total of all sales.")
        # Untouched measures stay empty, ready to fill.
        for name, desc in regenerated["measure_descriptions"].items():
            if name != first_measure:
                self.assertEqual(desc, "")

        measure_obj = next(m for m in model.all_measures() if m.name == first_measure)
        self.assertEqual(measure_obj.provenance, "Human-provided")

    def test_data_source_and_role_fields_round_trip(self):
        model = _model()
        if not model.data_sources:
            self.skipTest("fixture has no data sources")
        loc = model.data_sources[0].detail or model.data_sources[0].server or ""
        skeleton = yaml.safe_load(generate_enrichment_template(model))
        for ds in skeleton["data_sources"]:
            if ds["location"] == loc:
                ds["authentication_status"] = "Service principal"
                ds["latency_minutes"] = 15

        apply_enrichment(model, skeleton)
        regenerated = yaml.safe_load(generate_enrichment_template(model, previous=skeleton))
        entry = next(d for d in regenerated["data_sources"] if d["location"] == loc)
        self.assertEqual(entry["authentication_status"], "Service principal")
        self.assertEqual(entry["latency_minutes"], 15)

    def test_rules_config_and_history_carry_forward_with_no_model_home(self):
        # rules_config/history have no representation on SemanticModel at
        # all, so they can only survive a round trip via ``previous``.
        model = _model()
        skeleton = yaml.safe_load(generate_enrichment_template(model))
        skeleton["rules_config"]["suppressed_rules"] = ["PBIC-GOV-001"]
        skeleton["history"]["previous_fingerprint"] = "abc123"
        skeleton["history"]["previous_summary"] = "- Added table Foo"

        apply_enrichment(model, skeleton)
        regenerated = yaml.safe_load(generate_enrichment_template(model, previous=skeleton))
        self.assertEqual(regenerated["rules_config"]["suppressed_rules"], ["PBIC-GOV-001"])
        self.assertEqual(regenerated["history"]["previous_fingerprint"], "abc123")
        self.assertEqual(regenerated["history"]["previous_summary"], "- Added table Foo")

    def test_load_enrichment_missing_file_returns_empty_dict(self):
        self.assertEqual(load_enrichment(Path("does-not-exist.enrichment.yaml")), {})

    def test_load_enrichment_bad_yaml_raises_value_error(self, ):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.yaml"
            bad.write_text("key: [unterminated", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_enrichment(bad)


class ModelDiffTest(unittest.TestCase):
    def test_fingerprint_stable_for_same_model_changes_on_edit(self):
        model = _model()
        fp1 = get_model_fingerprint(model)
        fp2 = get_model_fingerprint(model)
        self.assertEqual(fp1, fp2)

        model.all_measures()[0].name = model.all_measures()[0].name + "_renamed"
        fp3 = get_model_fingerprint(model)
        self.assertNotEqual(fp1, fp3)

    def test_diff_detects_added_table_and_changed_measure(self):
        old = {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Amount"}],
                 "measures": [{"name": "Total Sales", "expression": "SUM(Sales[Amount])"}]},
            ],
            "relationships": [],
        }
        new = {
            "tables": [
                {"name": "Sales", "columns": [{"name": "Amount"}],
                 "measures": [{"name": "Total Sales", "expression": "SUM(Sales[Amount]) * 1.1"}]},
                {"name": "Region", "columns": [{"name": "Name"}], "measures": []},
            ],
            "relationships": [],
        }
        diff = compute_model_diff(old, new)
        self.assertEqual(diff["added_tables"], ["Region"])
        self.assertEqual(list(diff["changed_measures"].keys()), ["Total Sales"])

        changelog = generate_change_log_markdown(diff)
        self.assertIn("Region", changelog)
        self.assertIn("Total Sales", changelog)

    def test_diff_with_no_changes_says_so(self):
        same = {"tables": [], "relationships": []}
        diff = compute_model_diff(same, same)
        changelog = generate_change_log_markdown(diff)
        self.assertIn("No structural or logic changes", changelog)


class ChangelogRendererParityTest(unittest.TestCase):
    """5.2's renderer-parity fix: ``doc.changelog``, once populated, must
    show up in every format, not just HTML (the historical gap here)."""

    CHANGELOG = "- **Added Tables:** Region\n- **Modified Measures (Logic changed):** Total Sales"

    def test_technical_doc_changelog_in_all_three_formats(self):
        doc = generate_document(_model())
        doc.changelog = self.CHANGELOG
        html = render_html(doc)
        md = render_markdown(doc)
        self.assertIn("Changes since last documentation", html)
        self.assertIn("Region", html)
        self.assertIn("Changes since last documentation", md)
        self.assertIn("Region", md)
        with __import__("tempfile").TemporaryDirectory() as td:
            out = render_docx(doc, Path(td) / "out.docx")
            import zipfile
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Changes since last documentation", document)
            self.assertIn("Region", document)

    def test_audit_doc_changelog_in_all_three_formats(self):
        doc = AuditReportGenerator.generate(_model())
        doc.changelog = self.CHANGELOG
        html = render_audit_html(doc)
        md = render_audit_markdown(doc)
        self.assertIn("Changes since last documentation", html)
        self.assertIn("Region", html)
        self.assertIn("Changes since last documentation", md)
        self.assertIn("Region", md)
        with __import__("tempfile").TemporaryDirectory() as td:
            out = render_audit_docx(doc, Path(td) / "out.docx")
            import zipfile
            with zipfile.ZipFile(out) as zf:
                document = zf.read("word/document.xml").decode("utf-8")
            self.assertIn("Changes since last documentation", document)
            self.assertIn("Region", document)

    def test_no_changelog_section_when_unset(self):
        doc = generate_document(_model())
        self.assertIsNone(doc.changelog)
        self.assertNotIn("Changes since last documentation", render_html(doc))
        self.assertNotIn("Changes since last documentation", render_markdown(doc))


if __name__ == "__main__":
    unittest.main()
