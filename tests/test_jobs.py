"""Day 16: JobStore is now backed by sqlite3 (A2-1) instead of a plain dict,
behind the exact same method surface. These tests guard the one behavior that
actually matters for production: a job (queued, in-flight, or finished, plus
its rendered output bytes) survives the process that created it going away —
which a bare in-memory dict never could.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pbicompass.service.jobs import JobStatus, JobStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover - depends on environment
    _HAVE_SERVICE = False


class InMemoryDefaultBehaviorTest(unittest.TestCase):
    """The default ``JobStore()`` (no path) must behave exactly as the old
    pure-Python dict implementation did — every existing test constructs it
    this way, so this is a compatibility floor, not new behavior."""

    def setUp(self):
        self.store = JobStore()

    def test_create_get_roundtrip(self):
        job = self.store.create("Model.pbix", tenant="acme")
        fetched = self.store.get(job.id)
        self.assertEqual(fetched.filename, "Model.pbix")
        self.assertEqual(fetched.tenant, "acme")
        self.assertIs(fetched.status, JobStatus.QUEUED)

    def test_lifecycle_transitions(self):
        job = self.store.create("x.zip")
        self.store.mark_processing(job.id)
        self.assertIs(self.store.get(job.id).status, JobStatus.PROCESSING)
        self.store.mark_done(job.id, ["md", "html"], warnings=["fell back to offline"],
                              usage={"Executive Writer": {"calls": 1}})
        done = self.store.get(job.id)
        self.assertIs(done.status, JobStatus.DONE)
        self.assertEqual(done.formats, ["md", "html"])
        self.assertEqual(done.warnings, ["fell back to offline"])
        self.assertEqual(done.usage, {"Executive Writer": {"calls": 1}})

    def test_mark_failed(self):
        job = self.store.create("x.zip")
        self.store.mark_failed(job.id, "Could not read the file.")
        failed = self.store.get(job.id)
        self.assertIs(failed.status, JobStatus.FAILED)
        self.assertEqual(failed.error, "Could not read the file.")

    def test_unknown_job_returns_none(self):
        self.assertIsNone(self.store.get("nope"))
        self.assertIsNone(self.store.get_output("nope", "md"))

    def test_store_and_fetch_outputs(self):
        job = self.store.create("x.zip")
        self.store.store_outputs(job.id, {"md": b"# Report", "html": b"<html></html>"})
        self.assertEqual(self.store.get_output(job.id, "md"), b"# Report")
        self.assertEqual(self.store.get_output(job.id, "html"), b"<html></html>")

    def test_public_payload_has_no_document_content(self):
        job = self.store.create("Model.pbix")
        self.store.mark_done(job.id, ["md"])
        self.store.store_outputs(job.id, {"md": b"# secret model contents"})
        payload = self.store.public(self.store.get(job.id))
        blob = str(payload)
        self.assertNotIn("secret model contents", blob)
        self.assertIn("downloads", payload)


class PersistenceAcrossRestartTest(unittest.TestCase):
    """A2-1's done-when, refined for the Day 34 zero-retention split: a second
    ``JobStore`` pointed at the same DB (simulating a restart) must see all the
    job **metadata** the first wrote — status, timestamps, usage. The rendered
    **output bytes** must NOT survive: they live in process memory only and are
    never persisted (no document content is ever written to the DB or disk)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "jobs.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_finished_job_metadata_survives_a_restart_but_outputs_do_not(self):
        first = JobStore(self.db_path)
        job = first.create("Model.pbix", tenant="acme")
        first.mark_processing(job.id)
        first.mark_done(job.id, ["md", "html"], usage={"agent": {"calls": 2}})
        first.store_outputs(job.id, {"md": b"# Report", "html": b"<html>ok</html>"})
        # In the same instance, the outputs are available for download...
        self.assertEqual(first.get_output(job.id, "md"), b"# Report")
        first.close()

        second = JobStore(self.db_path)
        try:
            revived = second.get(job.id)
            self.assertIsNotNone(revived)
            self.assertIs(revived.status, JobStatus.DONE)
            self.assertEqual(revived.filename, "Model.pbix")
            self.assertEqual(revived.tenant, "acme")
            self.assertEqual(revived.usage, {"agent": {"calls": 2}})
            # ...but the rendered bytes are gone after a restart — never persisted.
            self.assertIsNone(second.get_output(job.id, "md"))
            self.assertIsNone(second.get_output(job.id, "html"))
        finally:
            second.close()

    def test_in_flight_job_survives_a_restart(self):
        # The exact scenario A2-1 calls out: a second instance must not 404
        # on a job another instance is still (or was still) working on.
        first = JobStore(self.db_path)
        job = first.create("Big Model.pbix")
        first.mark_processing(job.id)
        first.close()

        second = JobStore(self.db_path)
        try:
            revived = second.get(job.id)
            self.assertIsNotNone(revived)
            self.assertIs(revived.status, JobStatus.PROCESSING)
            self.assertIsNotNone(revived.started_at)
        finally:
            second.close()

    def test_queued_job_survives_a_restart(self):
        first = JobStore(self.db_path)
        job = first.create("x.zip")
        first.close()

        second = JobStore(self.db_path)
        try:
            self.assertIs(second.get(job.id).status, JobStatus.QUEUED)
        finally:
            second.close()


class SweepBehaviorTest(unittest.TestCase):
    """The watchdog force-fail and the output-TTL eviction still behave as
    before; only what expires changed — the ephemeral output bytes expire, but
    the job **metadata** is now kept as a durable history (Day 34) instead of
    being deleted with its BLOBs."""

    def test_watchdog_force_fails_stuck_processing_job(self):
        store = JobStore(processing_timeout_seconds=0.01)
        job = store.create("x.zip")
        store.mark_processing(job.id)
        time.sleep(0.05)
        stuck = store.get(job.id)  # get() triggers sweep()
        self.assertIs(stuck.status, JobStatus.FAILED)
        self.assertIn("timed out", stuck.error)

    def test_expired_output_is_dropped(self):
        store = JobStore(ttl_seconds=0.01)
        job = store.create("x.zip")
        store.mark_done(job.id, ["md"])
        store.store_outputs(job.id, {"md": b"# Report"})
        time.sleep(0.05)
        self.assertIsNone(store.get_output(job.id, "md"))

    def test_finished_job_metadata_is_kept_as_history_after_ttl(self):
        # Day 34: the job record persists (durable job history in the DB) even
        # after its output TTL elapses -- only the ephemeral output bytes go.
        store = JobStore(ttl_seconds=0.01)
        job = store.create("x.zip")
        store.mark_done(job.id, ["md"])
        store.store_outputs(job.id, {"md": b"# Report"})
        time.sleep(0.05)
        revived = store.get(job.id)
        self.assertIsNotNone(revived)                     # metadata kept
        self.assertIs(revived.status, JobStatus.DONE)
        self.assertIsNone(store.get_output(job.id, "md"))  # bytes gone


@unittest.skipUnless(_HAVE_SERVICE, "service extras (fastapi/httpx/multipart) not installed")
class CreateAppDefaultStoreWiringTest(unittest.TestCase):
    """``create_app()`` with no explicit store (the real production entrypoint,
    ``app = create_app()`` at module scope) must wire up a *persistent*
    JobStore honoring ``PBICOMPASS_JOBS_DB``, and close it on shutdown."""

    def setUp(self):
        # ``ignore_cleanup_errors``: on Windows, a sqlite file closed from a
        # TestClient's background portal thread can stay OS-locked for a beat
        # after the Python-level connection reports closed; not worth fighting
        # for a temp-dir teardown that isn't the thing under test.
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self._tmpdir.name) / "jobs.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_default_store_is_file_backed(self):
        with mock.patch.dict(os.environ, {"PBICOMPASS_JOBS_DB": self.db_path}):
            app = create_app()
            self.assertIsInstance(app.state.store, JobStore)
            job = app.state.store.create("Model.pbix")
            app.state.store.mark_done(job.id, ["md"])
            app.state.store.close()

        # A brand-new store pointed at the same path (simulating the next
        # process/restart) must still see the job -- the actual A2-1 requirement.
        reopened = JobStore(self.db_path)
        try:
            self.assertIs(reopened.get(job.id).status, JobStatus.DONE)
        finally:
            reopened.close()

    def test_shutdown_event_closes_the_owned_store(self):
        with mock.patch.dict(os.environ, {"PBICOMPASS_JOBS_DB": self.db_path}):
            app = create_app()
            store = app.state.store
            with TestClient(app):
                pass  # trigger the startup/shutdown lifecycle
            with self.assertRaises(Exception):
                store._conn.execute("SELECT 1")  # closed connections raise

    def test_passing_an_explicit_store_is_not_overridden(self):
        explicit = JobStore()  # in-memory, as every existing test relies on
        app = create_app(explicit)
        self.assertIs(app.state.store, explicit)

    def test_create_app_wires_its_metrics_registry_onto_an_explicit_store(self):
        # Day 20: an explicitly-passed store (the shape every existing test
        # uses) still gets this app's metrics registry attached, so job
        # counts are tracked regardless of who constructed the store.
        explicit = JobStore()
        self.assertIsNone(explicit.metrics)
        app = create_app(explicit)
        self.assertIsNotNone(explicit.metrics)
        self.assertIs(explicit.metrics, app.state.metrics)


class MetricsWiringTest(unittest.TestCase):
    """Day 20: a ``JobStore`` given a ``MetricsRegistry`` reports job counts
    through it at each of its own lifecycle call sites -- ``create``,
    ``mark_done``, ``mark_failed``, and the watchdog force-fail inside
    ``sweep``."""

    def setUp(self):
        from pbicompass.service.metrics import MetricsRegistry
        self.metrics = MetricsRegistry()

    def test_create_and_mark_done_are_recorded(self):
        store = JobStore(metrics=self.metrics)
        job = store.create("x.zip")
        store.mark_done(job.id, ["md"], usage={"Agent": {"calls": 1, "input_tokens": 10, "output_tokens": 5}})
        snap = self.metrics.snapshot()
        self.assertEqual(snap["jobs_created"], 1)
        self.assertEqual(snap["jobs_done"], 1)
        self.assertEqual(snap["avg_input_tokens_per_job"], 10.0)

    def test_mark_failed_is_recorded(self):
        store = JobStore(metrics=self.metrics)
        job = store.create("x.zip")
        store.mark_failed(job.id, "boom")
        self.assertEqual(self.metrics.snapshot()["jobs_failed"], 1)

    def test_watchdog_force_fail_is_recorded(self):
        store = JobStore(processing_timeout_seconds=0.01, metrics=self.metrics)
        job = store.create("x.zip")
        store.mark_processing(job.id)
        time.sleep(0.05)
        store.get(job.id)  # triggers sweep() -> watchdog force-fail
        self.assertEqual(self.metrics.snapshot()["jobs_failed"], 1)

    def test_no_metrics_registry_is_a_silent_no_op(self):
        store = JobStore()  # metrics=None, the default every other test relies on
        job = store.create("x.zip")
        store.mark_done(job.id, ["md"])  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
