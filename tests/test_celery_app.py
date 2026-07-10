"""Day 18: Celery/Redis async-worker wiring (§9/§12).

No live Redis server is available in this sandbox (same class of gap flagged
on Days 5/6/16/17 for provider credentials / multi-instance testing), so
these tests exercise the real ``celery`` package (installed into this
environment specifically for this work) in its ``task_always_eager`` mode —
the task body runs synchronously, in-process, with no broker connection at
all — rather than faking Celery's API. This is a different, lower-risk kind
of "no live smoke" gap than a faked SDK: the actual Celery
task-registration/invocation machinery runs for real; only the network hop
to an actual broker is skipped.
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

from pbicompass.service.jobs import JobStore
from pbicompass.service.sandbox import JobSandbox

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

try:
    import celery  # noqa: F401 - presence check only; the queue extra is optional
    _HAVE_CELERY = True
except Exception:  # pragma: no cover
    _HAVE_CELERY = False

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "SampleSales"


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE_DIR.parent))
    return buf.getvalue()


@unittest.skipUnless(_HAVE_CELERY, "celery extra not installed (pip install \"pbicompass[queue]\")")
class CeleryTaskBodyTest(unittest.TestCase):
    """Exercise ``process_job_task`` directly — no FastAPI, no eager mode
    needed, just the task function reconstructing store/sandbox handles from
    plain paths and calling the same ``process_job`` the inline path uses."""

    def test_task_reconstructs_store_and_sandbox_and_completes_the_job(self):
        from pbicompass.service.celery_app import process_job_task

        db_dir = tempfile.mkdtemp(prefix="pbicompass_celery_db_")
        db_path = str(Path(db_dir) / "jobs.db")
        store = JobStore(db_path)
        self.addCleanup(store.close)

        sandbox = JobSandbox(job_id="celerytest")
        self.addCleanup(sandbox.cleanup)
        upload_path = sandbox.path("upload.zip")
        upload_path.write_bytes(_zip())

        job = store.create("SampleSales.zip")
        options = {"provider": "none", "document_types": "technical"}

        # Call the task function directly (not .delay()/.apply_async()) —
        # exactly what a real Celery worker does once it pulls the message
        # off the broker and invokes the registered task body; no broker
        # needed to prove the task body itself is correct.
        process_job_task(job.id, str(upload_path), str(sandbox.dir), db_path, options)

        # A second, independent JobStore instance opened against the same DB
        # — standing in for the API process's poller — sees the job METADATA
        # the task (conceptually a different process) produced: status and
        # formats. Day 34: the rendered output BYTES live in each process's
        # own memory and are never persisted (zero-retention), so they do NOT
        # cross instances — a Celery/multi-instance deployment needs a shared
        # ephemeral output cache to serve downloads. Inline mode (the default,
        # and what this app runs) is unaffected: there the worker and the API
        # share one store instance in one process.
        poller = JobStore(db_path)
        self.addCleanup(poller.close)
        finished = poller.get(job.id)
        self.assertEqual(finished.status.value, "done")
        self.assertIn("md", finished.formats)
        self.assertIsNone(poller.get_output(job.id, "md"))  # ephemeral, process-local

    def test_task_marks_job_failed_on_bad_upload_without_raising(self):
        from pbicompass.service.celery_app import process_job_task

        db_dir = tempfile.mkdtemp(prefix="pbicompass_celery_db2_")
        db_path = str(Path(db_dir) / "jobs.db")
        store = JobStore(db_path)
        self.addCleanup(store.close)

        sandbox = JobSandbox(job_id="celerytest2")
        self.addCleanup(sandbox.cleanup)
        upload_path = sandbox.path("upload.zip")
        upload_path.write_bytes(b"not a real pbix or zip")

        job = store.create("bad.zip")
        process_job_task(job.id, str(upload_path), str(sandbox.dir), db_path,
                         {"provider": "none", "document_types": "technical"})

        finished = JobStore(db_path).get(job.id)
        self.assertEqual(finished.status.value, "failed")


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
@unittest.skipUnless(_HAVE_CELERY, "celery extra not installed (pip install \"pbicompass[queue]\")")
class CeleryEndToEndTest(unittest.TestCase):
    """Drive the real /jobs endpoint with PBICOMPASS_QUEUE=celery, using
    Celery's task_always_eager mode so the task body runs synchronously in
    this same process — no Redis broker needed to prove the wiring end to
    end, from HTTP upload through to a downloadable finished document."""

    def setUp(self):
        from pbicompass.service.celery_app import celery_app
        self._prev_eager = celery_app.conf.task_always_eager
        self._prev_propagates = celery_app.conf.task_eager_propagates
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        self.addCleanup(lambda: setattr(celery_app.conf, "task_always_eager", self._prev_eager))
        self.addCleanup(lambda: setattr(celery_app.conf, "task_eager_propagates", self._prev_propagates))

        self._root = tempfile.mkdtemp(prefix="pbicompass_celerysb_")
        db_dir = tempfile.mkdtemp(prefix="pbicompass_celery_jobsdb_")
        self.store = JobStore(str(Path(db_dir) / "jobs.db"))
        self.addCleanup(self.store.close)

        self._env = mock.patch.dict(os.environ, {"PBICOMPASS_QUEUE": "celery"})
        self._env.start()
        self.addCleanup(self._env.stop)

        self.client = TestClient(create_app(self.store, sandbox_root=self._root))

    def _wait(self, job_id):
        for _ in range(100):
            j = self.client.get(f"/jobs/{job_id}").json()
            if j["status"] in ("done", "failed"):
                return j
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_upload_completes_via_the_celery_dispatch_path(self):
        resp = self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip(), "application/zip")},
            data={"provider": "none"},
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["job_id"]
        status = self._wait(job_id)
        self.assertEqual(status["status"], "done")
        self.assertIn("md", status["formats"])

        # Day 34 (zero-retention): the Celery task ran in its own reconstructed
        # store, so the rendered bytes are in *that* store's memory, not the
        # API process's — cross-process downloads need a shared ephemeral
        # output cache (a follow-up for Celery/multi-instance). The job
        # metadata (status/formats) still crosses via the shared DB, which is
        # what this dispatch test proves. In inline mode (the default) the
        # same-process store serves downloads normally — see
        # InlineQueueUnaffectedTest and test_service.py's full download flow.
        md = self.client.get(f"/jobs/{job_id}/download", params={"format": "md"})
        self.assertEqual(md.status_code, 404)  # outputs not shared across processes

    def test_in_memory_store_with_celery_queue_is_rejected_clearly(self):
        # A separate worker process can't share a ":memory:" sqlite DB — the
        # app must refuse this combination up front instead of silently
        # stranding every job at "queued" forever.
        client = TestClient(create_app(JobStore(), sandbox_root=self._root))
        resp = client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip(), "application/zip")},
            data={"provider": "none"},
        )
        self.assertEqual(resp.status_code, 500)
        self.assertIn("file-backed", resp.json()["detail"])


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class InlineQueueUnaffectedTest(unittest.TestCase):
    """Regression: the default (PBICOMPASS_QUEUE unset) still uses the
    inline BackgroundTasks path — Day 18's celery branch must be additive,
    never the default behavior."""

    def test_default_queue_mode_is_inline(self):
        from pbicompass.service.app import _queue_mode
        prior = os.environ.pop("PBICOMPASS_QUEUE", None)
        try:
            self.assertEqual(_queue_mode(), "inline")
        finally:
            if prior is not None:
                os.environ["PBICOMPASS_QUEUE"] = prior

    def test_upload_still_completes_synchronously_without_celery(self):
        root = tempfile.mkdtemp(prefix="pbicompass_inlinesb_")
        client = TestClient(create_app(JobStore(), sandbox_root=root))
        resp = client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip(), "application/zip")},
            data={"provider": "none"},
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["job_id"]
        for _ in range(200):
            j = client.get(f"/jobs/{job_id}").json()
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.02)
        else:
            self.fail("job did not finish")
        self.assertEqual(j["status"], "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
