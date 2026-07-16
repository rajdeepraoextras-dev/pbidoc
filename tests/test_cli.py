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
import zipfile
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
            self.assertIn("Purpose & Value", text)
            self.assertIn("What's Next", text)

    def test_document_executive_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "executive.json"
            code = cli.main(["generate", str(FIXTURE), "--document", "executive",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["metadata"]["document_type"], "executive")
            self.assertIn("purpose", data)
            self.assertIn("top_risks", data)
            self.assertIn("next_steps", data)

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


class RulesFileFlagTest(unittest.TestCase):
    """J.A.3: ``--rules`` suppresses/overrides audit findings via a
    ``pbicompass.rules.toml`` file; invalid TOML warns but never fails the
    job."""

    def tearDown(self):
        # This is process-wide module state (agents.audit_rules) — never
        # leak one test's rules file into the next.
        from pbicompass.agents import audit_rules
        audit_rules.set_rules_config_path(None)

    def test_disabling_a_rule_moves_it_to_the_suppressed_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            rules_path = Path(td) / "pbicompass.rules.toml"
            rules_path.write_text('[rules."PBIC-DAX-003"]\nenabled = false\n', encoding="utf-8")
            out = Path(td) / "audit.json"
            code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                            "--rules", str(rules_path), "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("PBIC-DAX-003", data["suppressed_rules"])
            self.assertNotIn("PBIC-DAX-003", [f["rule_id"] for f in data["dax_findings"]])

    def test_invalid_toml_warns_and_still_generates(self):
        with tempfile.TemporaryDirectory() as td:
            rules_path = Path(td) / "pbicompass.rules.toml"
            rules_path.write_text("this is not [ valid toml", encoding="utf-8")
            out = Path(td) / "audit.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                                "--rules", str(rules_path), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("Invalid TOML", stderr.getvalue())
            self.assertTrue(out.exists())

    def test_missing_rules_file_warns_and_still_generates(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist.toml"
            out = Path(td) / "audit.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["generate", str(FIXTURE), "--document", "audit",
                                "--rules", str(missing), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("not found", stderr.getvalue())
            self.assertTrue(out.exists())


class EnrichFlagTest(unittest.TestCase):
    """5.1: ``--enrich`` bootstraps a skeleton on first use, then applies
    and round-trips it on subsequent runs."""

    def tearDown(self):
        from pbicompass.agents import audit_rules
        audit_rules.set_rules_override_config({})

    def test_bootstrap_then_fill_then_rerun_round_trips(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            enrich_path = Path(td) / "report.enrichment.yaml"
            out = Path(td) / "technical.json"

            # First run: no file yet -> bootstrap a skeleton, nothing applied.
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                                "--enrich", str(enrich_path), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(enrich_path.exists())
            self.assertIn("wrote a fresh skeleton", stderr.getvalue().lower())

            skeleton = yaml.safe_load(enrich_path.read_text(encoding="utf-8"))
            skeleton["metadata"]["owner"] = "Jane Doe"
            first_measure = next(iter(skeleton["measure_descriptions"]))
            skeleton["measure_descriptions"][first_measure] = "A human-written definition."
            enrich_path.write_text(yaml.safe_dump(skeleton), encoding="utf-8")

            # Second run: file exists -> apply it, then round-trip it.
            code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                            "--enrich", str(enrich_path), "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)

            doc = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(doc["metadata"]["owner"], "Jane Doe")
            measure = next(m for m in doc["measure_catalog"]["measures"] if m["name"] == first_measure)
            self.assertEqual(measure["plain_english"], "A human-written definition.")
            self.assertEqual(measure["provenance"], "Human-provided")

            regenerated = yaml.safe_load(enrich_path.read_text(encoding="utf-8"))
            self.assertEqual(regenerated["metadata"]["owner"], "Jane Doe")
            self.assertEqual(regenerated["measure_descriptions"][first_measure],
                            "A human-written definition.")
            self.assertTrue(regenerated["history"]["previous_fingerprint"])

    def test_invalid_yaml_warns_and_still_generates(self):
        with tempfile.TemporaryDirectory() as td:
            enrich_path = Path(td) / "bad.enrichment.yaml"
            enrich_path.write_text("key: [unterminated", encoding="utf-8")
            out = Path(td) / "technical.json"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                                "--enrich", str(enrich_path), "-o", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("failed to parse", stderr.getvalue().lower())
            self.assertTrue(out.exists())


class PublishCommandTest(unittest.TestCase):
    """C3: the ``publish`` subcommand. Only the filesystem target runs for real;
    nothing here ever reaches the network."""

    def _src(self, td: Path) -> Path:
        src = td / "bundle"
        src.mkdir()
        (src / "technical.html").write_text("<html><body><h1>T</h1></body></html>",
                                            encoding="utf-8")
        (src / "model.json").write_text("{}", encoding="utf-8")
        return src

    def test_dry_run_sends_nothing_and_lists_items(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._src(Path(td))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["publish", "filesystem", str(src),
                                 "--dest", str(Path(td) / "out"), "--dry-run"])
            self.assertEqual(code, 0)
            out = stdout.getvalue()
            self.assertIn("Dry run", out)
            self.assertIn("technical.html", out)
            self.assertIn("Nothing was sent", out)
            self.assertFalse((Path(td) / "out").exists())  # truly nothing happened

    def test_filesystem_publish_copies_files(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._src(Path(td))
            dest = Path(td) / "out"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["publish", "filesystem", str(src), "--dest", str(dest)])
            self.assertEqual(code, 0)
            self.assertTrue((dest / "technical.html").exists())
            self.assertIn("Published 2 document(s)", stdout.getvalue())

    def test_missing_config_exits_nonzero_with_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            src = self._src(Path(td))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = cli.main(["publish", "confluence", str(src)])
            self.assertEqual(code, 1)
            self.assertIn("missing config", stderr.getvalue())


class DiffCommandTest(unittest.TestCase):
    def test_diff_reports_changed_measure(self):
        with tempfile.TemporaryDirectory() as td:
            old_path = Path(td) / "old.json"
            new_path = Path(td) / "new.json"
            cli.main(["parse", str(FIXTURE), "-o", str(old_path), "--quiet"])
            model = json.loads(old_path.read_text(encoding="utf-8"))
            for t in model["tables"]:
                if t.get("measures"):
                    t["measures"][0]["expression"] += " * 1.0"
                    break
            new_path.write_text(json.dumps(model), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["diff", str(old_path), str(new_path)])
            self.assertEqual(code, 0)
            out = stdout.getvalue()
            # C2: richer severity-grouped change log names the object + kind.
            self.assertIn("modified", out)
            self.assertIn("DAX logic changed", out)

    def test_diff_with_no_changes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "model.json"
            cli.main(["parse", str(FIXTURE), "-o", str(path), "--quiet"])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["diff", str(path), str(path)])
            self.assertEqual(code, 0)
            self.assertIn("No structural or logic changes", stdout.getvalue())


class BundleFlagTest(unittest.TestCase):
    """5.7: ``--bundle`` renders every format for every requested document
    type, plus ``model.json``, into one zip."""

    def test_multi_document_bundle_contains_every_format_and_hub(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bundle.zip"
            code = cli.main(["generate", str(FIXTURE), "--document", "all",
                            "--bundle", "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
            for dtype in DOCUMENT_TYPES:
                for fmt in ("md", "json", "html", "docx"):
                    self.assertIn(f"{dtype}.{fmt}", names)
            self.assertIn("index.html", names)
            self.assertIn("model.json", names)
            self.assertNotIn("enrichment.yaml", names)  # no --enrich given

    def test_single_document_bundle_has_no_type_prefix_collision_and_no_hub(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "bundle.zip"
            code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                            "--bundle", "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
            self.assertEqual(names, {"technical.md", "technical.json", "technical.html",
                                     "technical.docx", "model.json"})

    def test_bundle_with_enrich_includes_regenerated_skeleton(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            enrich_path = Path(td) / "report.enrichment.yaml"
            # Bootstrap first (no file yet), then fill one field.
            cli.main(["generate", str(FIXTURE), "--document", "technical",
                     "--enrich", str(enrich_path), "--quiet"])
            skeleton = yaml.safe_load(enrich_path.read_text(encoding="utf-8"))
            skeleton["metadata"]["owner"] = "Jane Doe"
            enrich_path.write_text(yaml.safe_dump(skeleton), encoding="utf-8")

            out = Path(td) / "bundle.zip"
            code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                            "--enrich", str(enrich_path), "--bundle", "-o", str(out),
                            "--quiet"])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertIn("enrichment.yaml", names)
                regenerated = yaml.safe_load(zf.read("enrichment.yaml").decode("utf-8"))
                self.assertEqual(regenerated["metadata"]["owner"], "Jane Doe")
                doc = json.loads(zf.read("technical.json").decode("utf-8"))
                self.assertEqual(doc["metadata"]["owner"], "Jane Doe")

    def test_default_bundle_filename_when_no_out_given(self):
        with tempfile.TemporaryDirectory() as td:
            import os
            old_cwd = os.getcwd()
            os.chdir(td)
            try:
                code = cli.main(["generate", str(FIXTURE), "--document", "technical",
                                "--bundle", "--quiet"])
                self.assertEqual(code, 0)
                self.assertTrue(any(p.name.endswith("-documentation.zip") for p in Path(td).iterdir()))
            finally:
                os.chdir(old_cwd)


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

    def test_all_html_creates_hub_and_doc_switcher_links(self):
        # 2.1/2.7: --document all --format html writes one hub (index.html)
        # linking every sibling doc, and each doc's own sidebar links back
        # to its siblings + the hub, using the real on-disk filenames.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.html"
            code = cli.main(["generate", str(FIXTURE), "--document", "all", "--format", "html",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)

            hub_path = out.with_name("report.index.html")
            self.assertTrue(hub_path.exists())
            hub_html = hub_path.read_text(encoding="utf-8")
            for dtype in DOCUMENT_TYPES:
                self.assertIn(f"report.{dtype}.html", hub_html)

            technical_html = out.with_name("report.technical.html").read_text(encoding="utf-8")
            self.assertIn('class="doc-switcher"', technical_html)
            self.assertIn("report.audit.html", technical_html)
            self.assertIn("report.index.html", technical_html)
            self.assertNotIn("report.technical.html", technical_html.split('class="doc-switcher"')[1].split("</nav>")[0])

    def test_cross_document_content_links_resolve_to_real_anchors(self):
        # 2.7: audit's DAX findings link to the technical doc's measure
        # anchors, and the executive doc's top risks deep-link to the exact
        # audit recommendation card behind each one (I5) — both real anchors
        # that exist in the sibling file, not dead links.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.html"
            code = cli.main(["generate", str(FIXTURE), "--document", "all", "--format", "html",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)

            technical_html = out.with_name("report.technical.html").read_text(encoding="utf-8")
            audit_html = out.with_name("report.audit.html").read_text(encoding="utf-8")
            executive_html = out.with_name("report.executive.html").read_text(encoding="utf-8")

            self.assertIn('id="measure-total-revenue"', technical_html)
            self.assertIn('href="report.technical.html#measure-total-revenue"', audit_html)

            from pbicompass.agents.generators import ExecutiveSummaryGenerator
            from pbicompass.parsers import detect_and_parse

            risk = next(r for r in ExecutiveSummaryGenerator.generate(detect_and_parse(FIXTURE)).top_risks
                       if r.rule_id)
            self.assertIn(f'id="rec-{risk.rule_id}"', audit_html)
            self.assertIn(f'href="report.audit.html#rec-{risk.rule_id}"', executive_html)

    def test_single_document_html_has_no_dead_doc_switcher(self):
        # a single-document run has no siblings to link to — no doc-switcher
        # block, never a dead link.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.html"
            code = cli.main(["generate", str(FIXTURE), "--document", "technical", "--format", "html",
                            "-o", str(out), "--quiet"])
            self.assertEqual(code, 0)
            self.assertNotIn('class="doc-switcher"', out.read_text(encoding="utf-8"))
            self.assertFalse(out.with_name("report.index.html").exists())

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


class AccountCommandTest(unittest.TestCase):
    """``pbicompass account create/list/revoke`` — run in-process against a
    temp SQLite file (mirrors what the CLI does against $PBICOMPASS_DB)."""

    def _db(self, td: str) -> str:
        return str(Path(td) / "accounts.db")

    def test_create_then_list_then_revoke(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.main(["account", "create", "--tenant", "acme",
                                 "--name", "Acme BI", "--plan", "pro", "--db", db])
            self.assertEqual(code, 0)
            created = buf.getvalue()
            self.assertIn("tenant 'acme'", created)
            key_match = re.search(r"(pbicompass_sk_\S+)", created)
            self.assertIsNotNone(key_match)
            key = key_match.group(1)

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["account", "list", "--db", db])
            listing = buf.getvalue()
            self.assertIn("acme", listing)
            self.assertIn("pro", listing)
            account_id = listing.split()[0]

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli.main(["account", "revoke", "--id", account_id, "--db", db])
            self.assertEqual(code, 0)
            self.assertIn("Revoked", buf.getvalue())

            # Close explicitly (not via addCleanup) so the file is unlocked
            # before the TemporaryDirectory context tries to delete it —
            # addCleanup callbacks run after this "with" block already exited.
            from pbicompass.service.accounts import AccountStore
            store = AccountStore(db)
            try:
                self.assertIsNone(store.verify(key))
            finally:
                store.close()

    def test_revoke_unknown_id_fails(self):
        with tempfile.TemporaryDirectory() as td:
            db = self._db(td)
            buf = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(buf):
                code = cli.main(["account", "revoke", "--id", "nope", "--db", db])
            self.assertEqual(code, 1)
            self.assertIn("nope", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
