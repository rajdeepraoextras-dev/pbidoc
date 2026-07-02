"""Phase 4 tests: the zero-retention web service.

Requires the service extras (fastapi, httpx, python-multipart). The whole module
skips cleanly when they are absent, so the stdlib-only test run is unaffected.
"""

from __future__ import annotations

import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

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
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("Generate documentation", index.text)  # upload card heading
        self.assertIn("/jobs", index.text)  # upload JS wired to the API

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
        self.assertTrue(all(fmt.startswith(("audit.", "executive.")) for fmt in formats))
        self.assertTrue(any(fmt.startswith("audit.") for fmt in formats))
        self.assertTrue(any(fmt.startswith("executive.") for fmt in formats))


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
