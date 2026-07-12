"""Phase 4 tests: the zero-retention web service.

Requires the service extras (fastapi, httpx, python-multipart). The whole module
skips cleanly when they are absent, so the stdlib-only test run is unaffected.
"""

from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    from pbicompass.service.ingest import _safe_extract
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover - depends on environment
    _HAVE_SERVICE = False

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "SampleSales"


def _zip_fixture() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE_DIR.parent))  # arcname: SampleSales/...
    return buf.getvalue()


@unittest.skipUnless(_HAVE_SERVICE, "service extras (fastapi/httpx/multipart) not installed")
class ServiceTest(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="pbicompass_sbroot_")
        self.client = TestClient(create_app(JobStore(), sandbox_root=self._root))

    def _run_job(self, filename="SampleSales.zip", content=None, provider="none"):
        content = _zip_fixture() if content is None else content
        res = self.client.post(
            "/jobs",
            files={"file": (filename, content, "application/zip")},
            data={"provider": provider},
        )
        return res

    def _wait(self, job_id, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/jobs/{job_id}").json()
            if job["status"] in ("done", "failed"):
                return job
            time.sleep(0.05)
        self.fail("job did not finish in time")

    def test_healthz_and_index(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        body = health.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["checks"]["jobs_db"])
        self.assertTrue(body["checks"]["queue"])  # inline mode: no external dependency
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        # Day 33: the landing page is pure marketing; the uploader moved to
        # /app behind sign-in, so the page CTAs point there rather than
        # wiring /jobs inline.
        self.assertIn("Generate documentation", index.text)  # menu/CTA copy
        self.assertIn("/app", index.text)  # CTAs route to the signed-in workspace
        # The functional uploader (wired to /jobs) now lives at /app.
        app_page = self.client.get("/app")
        self.assertEqual(app_page.status_code, 200)
        self.assertIn("/jobs", app_page.text)  # upload JS wired to the API

    def test_full_flow_and_downloads(self):
        res = self._run_job()
        self.assertEqual(res.status_code, 200)
        job_id = res.json()["job_id"]
        job = self._wait(job_id)
        self.assertEqual(job["status"], "done", job)
        self.assertLessEqual({"md", "json", "html", "docx"}, set(job["formats"]))

        md = self.client.get(f"/jobs/{job_id}/download", params={"format": "md"})
        self.assertEqual(md.status_code, 200)
        self.assertIn("SampleSales", md.text)
        self.assertIn("Orphan Margin", md.text)  # deterministic audit present
        self.assertIn("attachment", md.headers["content-disposition"])

        docx = self.client.get(f"/jobs/{job_id}/download", params={"format": "docx"})
        self.assertEqual(docx.status_code, 200)
        self.assertTrue(docx.content.startswith(b"PK"))  # a real zip/OOXML package

    def test_sandbox_is_shredded(self):
        job_id = self._run_job().json()["job_id"]
        self.assertEqual(self._wait(job_id)["status"], "done")
        leftover = list(Path(self._root).glob("pbicompass_*"))
        self.assertEqual(leftover, [], f"sandbox not cleaned: {leftover}")

    def test_rejects_unsupported_type(self):
        res = self.client.post(
            "/jobs",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"provider": "none"},
        )
        self.assertEqual(res.status_code, 400)

    def test_unknown_job_and_format(self):
        self.assertEqual(self.client.get("/jobs/nope").status_code, 404)
        self.assertEqual(
            self.client.get("/jobs/nope/download", params={"format": "md"}).status_code, 404
        )
        job_id = self._run_job().json()["job_id"]
        self._wait(job_id)
        bad = self.client.get(f"/jobs/{job_id}/download", params={"format": "exe"})
        self.assertEqual(bad.status_code, 400)

    def test_corrupt_upload_fails_gracefully(self):
        # a .zip that isn't a real zip -> job fails with a content-free message, no crash
        job_id = self._run_job(content=b"not a real zip").json()["job_id"]
        job = self._wait(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("Could not read", job["error"])

    def test_rules_file_upload_suppresses_a_rule(self):
        # J.A.3: an optional second upload field lets a caller suppress/
        # override audit rules for this job only — saved into the job's own
        # sandbox and shredded with everything else.
        rules_toml = b'[rules."PBIC-DAX-003"]\nenabled = false\n'
        res = self.client.post(
            "/jobs",
            files={
                "file": ("SampleSales.zip", _zip_fixture(), "application/zip"),
                "rules_file": ("pbicompass.rules.toml", rules_toml, "application/octet-stream"),
            },
            data={"provider": "none", "document_types": "audit"},
        )
        job = self._wait(res.json()["job_id"])
        self.assertEqual(job["status"], "done", job)
        audit_json = self.client.get(f"/jobs/{job['job_id']}/download", params={"format": "json"}).json()
        self.assertIn("PBIC-DAX-003", audit_json["suppressed_rules"])
        self.assertNotIn("PBIC-DAX-003", [f["rule_id"] for f in audit_json["dax_findings"]])

    def test_invalid_rules_file_warns_but_job_still_succeeds(self):
        res = self.client.post(
            "/jobs",
            files={
                "file": ("SampleSales.zip", _zip_fixture(), "application/zip"),
                "rules_file": ("pbicompass.rules.toml", b"not [ valid toml", "application/octet-stream"),
            },
            data={"provider": "none", "document_types": "audit"},
        )
        job = self._wait(res.json()["job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(any("Invalid TOML" in w for w in job.get("warnings", [])), job)

    def test_enrichment_file_upload_applies_descriptions_and_round_trips(self):
        # 5.1: an optional enrichment YAML upload overrides measure/column
        # descriptions and metadata, and the regenerated skeleton (with the
        # filled fields carried forward) comes back in the job's outputs.
        import yaml

        parsed = self.client.post(
            "/jobs", files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none", "document_types": "technical"},
        )
        first_job = self._wait(parsed.json()["job_id"])
        technical_json = self.client.get(
            f"/jobs/{first_job['job_id']}/download", params={"format": "json"}
        ).json()
        first_measure = technical_json["measure_catalog"]["measures"][0]["name"]

        enrichment_yaml = yaml.safe_dump({
            "metadata": {"owner": "Jane Doe"},
            "measure_descriptions": {first_measure: "A human-written definition."},
        })
        res = self.client.post(
            "/jobs",
            files={
                "file": ("SampleSales.zip", _zip_fixture(), "application/zip"),
                "enrichment_file": ("report.enrichment.yaml", enrichment_yaml, "application/x-yaml"),
            },
            data={"provider": "none", "document_types": "technical,audit"},
        )
        job = self._wait(res.json()["job_id"], timeout=30.0)
        self.assertEqual(job["status"], "done", job)
        self.assertIn("enrichment.yaml", job["formats"])

        doc = self.client.get(
            f"/jobs/{job['job_id']}/download", params={"format": "technical.json"}
        ).json()
        self.assertEqual(doc["metadata"]["owner"], "Jane Doe")
        measure = next(m for m in doc["measure_catalog"]["measures"] if m["name"] == first_measure)
        self.assertEqual(measure["plain_english"], "A human-written definition.")
        self.assertEqual(measure["provenance"], "Human-provided")

        regenerated = yaml.safe_load(self.client.get(
            f"/jobs/{job['job_id']}/download", params={"format": "enrichment.yaml"}
        ).text)
        self.assertEqual(regenerated["metadata"]["owner"], "Jane Doe")
        self.assertEqual(regenerated["measure_descriptions"][first_measure],
                        "A human-written definition.")

    def test_invalid_enrichment_file_warns_but_job_still_succeeds(self):
        res = self.client.post(
            "/jobs",
            files={
                "file": ("SampleSales.zip", _zip_fixture(), "application/zip"),
                "enrichment_file": ("bad.yaml", b"key: [unterminated", "application/x-yaml"),
            },
            data={"provider": "none", "document_types": "technical"},
        )
        job = self._wait(res.json()["job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(any("continuing without enrichment" in w for w in job.get("warnings", [])), job)

    def test_omitted_document_types_yields_flat_keys(self):
        # Back-compat: no ``document_types`` field at all -> identical to today's
        # single-"technical"-document behavior, flat format keys.
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none"},
        )
        job = self._wait(res.json()["job_id"])
        self.assertEqual(job["status"], "done", job)
        self.assertLessEqual({"md", "json", "html", "docx"}, set(job["formats"]))
        self.assertTrue(all("." not in fmt for fmt in job["formats"]))

    def test_document_types_all_yields_composite_keys(self):
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none", "document_types": "all"},
        )
        self.assertEqual(res.status_code, 200)
        job = self._wait(res.json()["job_id"], timeout=30.0)
        self.assertEqual(job["status"], "done", job)
        formats = set(job["formats"])
        for dtype in ("technical", "audit", "executive", "user-guide"):
            for fmt in ("md", "json", "html", "docx"):
                self.assertIn(f"{dtype}.{fmt}", formats)

    def test_document_types_all_downloads_are_independently_fetchable(self):
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none", "document_types": "all"},
        )
        job_id = res.json()["job_id"]
        job = self._wait(job_id, timeout=30.0)
        self.assertEqual(job["status"], "done", job)

        audit_md = self.client.get(f"/jobs/{job_id}/download", params={"format": "audit.md"})
        self.assertEqual(audit_md.status_code, 200)
        self.assertIn("Audit & Health Report", audit_md.text)

        exec_html = self.client.get(f"/jobs/{job_id}/download", params={"format": "executive.html"})
        self.assertEqual(exec_html.status_code, 200)
        self.assertIn("Executive Summary", exec_html.text)

        guide_docx = self.client.get(f"/jobs/{job_id}/download", params={"format": "user-guide.docx"})
        self.assertEqual(guide_docx.status_code, 200)
        self.assertTrue(guide_docx.content.startswith(b"PK"))

    def test_document_types_comma_list_selects_subset(self):
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none", "document_types": "audit,executive"},
        )
        job_id = res.json()["job_id"]
        job = self._wait(job_id, timeout=30.0)
        self.assertEqual(job["status"], "done", job)
        formats = set(job["formats"])
        # Per-doc-type outputs only for the two requested types, plus the
        # job-wide hub + zip bundle + model.json (2.1/5.7) every multi-doc
        # job also gets.
        per_type = formats - {"index.html", "zip", "model.json"}
        self.assertTrue(all(fmt.startswith(("audit.", "executive.")) for fmt in per_type), per_type)
        self.assertTrue(any(fmt.startswith("audit.") for fmt in formats))
        self.assertTrue(any(fmt.startswith("executive.") for fmt in formats))
        self.assertIn("index.html", formats)
        self.assertIn("zip", formats)
        self.assertIn("model.json", formats)

    def test_multi_doc_hub_and_zip_have_working_relative_links(self):
        # P1: the hosted service, not just the CLI, must ship a working hub
        # and doc-switcher — the zip bundle is the only place the fixed
        # "{type}.html" names (and the links built on them) are valid side
        # by side, since the standalone /download names depend on the
        # upload filename instead.
        res = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"provider": "none", "document_types": "all"},
        )
        job_id = res.json()["job_id"]
        job = self._wait(job_id, timeout=30.0)
        self.assertEqual(job["status"], "done", job)

        hub = self.client.get(f"/jobs/{job_id}/download", params={"format": "index.html"})
        self.assertEqual(hub.status_code, 200)
        for dtype in ("technical", "audit", "executive", "user-guide"):
            self.assertIn(f"{dtype}.html", hub.text)

        technical_html = self.client.get(
            f"/jobs/{job_id}/download", params={"format": "technical.html"},
        ).text
        self.assertIn('class="doc-switcher"', technical_html)
        self.assertIn("audit.html", technical_html)
        self.assertIn('id="measure-total-revenue"', technical_html)

        audit_html = self.client.get(f"/jobs/{job_id}/download", params={"format": "audit.html"}).text
        self.assertIn('href="technical.html#measure-total-revenue"', audit_html)

        zip_resp = self.client.get(f"/jobs/{job_id}/download", params={"format": "zip"})
        self.assertEqual(zip_resp.status_code, 200)
        self.assertEqual(zip_resp.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            names = set(zf.namelist())
            for dtype in ("technical", "audit", "executive", "user-guide"):
                self.assertIn(f"{dtype}.html", names)
            self.assertIn("index.html", names)
            # the zip's own copy of the docs must carry the same working
            # cross-document links as the standalone downloads above.
            self.assertIn('href="technical.html#measure-total-revenue"', zf.read("audit.html").decode("utf-8"))


@unittest.skipUnless(_HAVE_SERVICE, "service extras (fastapi/httpx/multipart) not installed")
class AssistTest(unittest.TestCase):
    """The Notes tab's "AI Fill" / "Format" buttons -- always MeshAPI (see
    ``_assist_client`` in service/app.py), independent of the job's own
    engine. No real MeshAPI key/network call here: ``get_client`` is
    monkeypatched with a stub so these stay fast and offline like the rest
    of the suite."""

    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="pbicompass_sbroot_")
        self.client = TestClient(create_app(JobStore(), sandbox_root=self._root))
        os.environ.pop("MESHAPI_API_KEY", None)

    def tearDown(self):
        os.environ.pop("MESHAPI_API_KEY", None)

    def test_fill_requires_known_field(self):
        os.environ["MESHAPI_API_KEY"] = "test-key"
        res = self.client.post(
            "/app/api/assist/fill",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"field": "not_a_real_field"},
        )
        self.assertEqual(res.status_code, 400)

    def test_fill_without_meshapi_key_is_unavailable(self):
        res = self.client.post(
            "/app/api/assist/fill",
            files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
            data={"field": "glossary"},
        )
        self.assertEqual(res.status_code, 503)

    def test_format_without_meshapi_key_is_unavailable(self):
        res = self.client.post("/app/api/assist/format", json={"text": "hello world"})
        self.assertEqual(res.status_code, 503)

    def test_format_rejects_empty_text(self):
        os.environ["MESHAPI_API_KEY"] = "test-key"
        res = self.client.post("/app/api/assist/format", json={"text": "   "})
        self.assertEqual(res.status_code, 400)

    def test_fill_rejects_unsupported_file_type(self):
        os.environ["MESHAPI_API_KEY"] = "test-key"
        with mock.patch("pbicompass.service.app.get_client", return_value=object()):
            res = self.client.post(
                "/app/api/assist/fill",
                files={"file": ("notes.txt", b"hello", "text/plain")},
                data={"field": "glossary"},
            )
        self.assertEqual(res.status_code, 400)

    def test_fill_and_format_happy_path_with_stubbed_client(self):
        class StubClient:
            model = "stub"

            def complete_json(self, system, user, schema, *, effort=None):
                if "field_label" in user:
                    return {"text": "Real drafted content."}
                return {"text": "Real formatted content."}

        os.environ["MESHAPI_API_KEY"] = "test-key"
        with mock.patch("pbicompass.service.app.get_client", return_value=StubClient()):
            res = self.client.post(
                "/app/api/assist/fill",
                files={"file": ("SampleSales.zip", _zip_fixture(), "application/zip")},
                data={"field": "glossary", "owner": "Sales Team"},
            )
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["text"], "Real drafted content.")

            res2 = self.client.post("/app/api/assist/format", json={"text": "the sales team  needs this"})
            self.assertEqual(res2.status_code, 200, res2.text)
            self.assertEqual(res2.json()["text"], "Real formatted content.")


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class ZipSlipTest(unittest.TestCase):
    def test_safe_extract_blocks_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
        buf.seek(0)
        with tempfile.TemporaryDirectory() as td:
            with zipfile.ZipFile(buf) as zf:
                with self.assertRaises(ValueError):
                    _safe_extract(zf, Path(td))


if __name__ == "__main__":
    unittest.main(verbosity=2)
