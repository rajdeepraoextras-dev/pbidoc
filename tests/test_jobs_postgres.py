"""Day 34: JobStore over the Postgres branch of the shared ``_Connection``.

Job metadata can now live in managed Postgres (``PBICOMPASS_JOBS_DB=postgres://
...``, e.g. Supabase) instead of a sqlite file — so a hosted deployment keeps
no sqlite at all. Same verification technique as ``test_accounts_postgres.py``:
a **fake ``psycopg`` module** backed by a real in-memory sqlite3 database
(translating the ``%s`` placeholders back to ``?``), so the full
create/lifecycle/list/sweep code path runs end-to-end through the Postgres
branch of ``_Connection`` without needing a live Postgres server.

The rendered output BYTES are never in the DB on either backend (they're held
in process memory, zero-retention), so this file only covers the metadata that
Postgres actually stores.
"""

from __future__ import annotations

import sqlite3
import sys
import time
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from pbicompass.service.jobs import JobStatus, JobStore


class _FakePsycopgCursor:
    def __init__(self, sqlite_conn: sqlite3.Connection) -> None:
        self._conn = sqlite_conn
        self._cur = sqlite_conn.cursor()

    def execute(self, sql: str, params=()):
        # JobStore sends %s-style SQL through the Postgres branch; translate
        # back to '?' to run it against the sqlite3 connection standing in for
        # a real Postgres server. The parameter-free multi-statement DDL
        # script is routed through executescript, the one thing real Postgres
        # allows in a single execute that sqlite3.cursor.execute doesn't.
        translated = sql.replace("%s", "?")
        if not params and translated.strip().count(";") >= 1 and "CREATE TABLE" in translated:
            self._conn.executescript(translated)
        else:
            self._cur.execute(translated, params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _FakePsycopgConnection:
    def __init__(self, sqlite_conn: sqlite3.Connection) -> None:
        self._sqlite = sqlite_conn
        self._sqlite.row_factory = sqlite3.Row

    def cursor(self) -> _FakePsycopgCursor:
        return _FakePsycopgCursor(self._sqlite)

    def commit(self) -> None:
        self._sqlite.commit()

    def rollback(self) -> None:
        self._sqlite.rollback()

    def close(self) -> None:
        self._sqlite.close()


def _install_fake_psycopg():
    def fake_connect(dsn, **kwargs):
        return _FakePsycopgConnection(sqlite3.connect(":memory:", check_same_thread=False))

    fake_module = ModuleType("psycopg")
    fake_module.connect = fake_connect
    fake_module.rows = SimpleNamespace(dict_row=object())
    return fake_module


class JobStorePostgresBackendTest(unittest.TestCase):
    def _store(self) -> JobStore:
        fake = _install_fake_psycopg()
        patcher = patch.dict(sys.modules, {"psycopg": fake})
        patcher.start()
        self.addCleanup(patcher.stop)
        store = JobStore("postgresql://user:pw@host/db")
        self.addCleanup(store.close)
        return store

    def test_create_and_get_over_the_postgres_branch(self):
        store = self._store()
        job = store.create("Model.pbix", tenant="acme")
        got = store.get(job.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.filename, "Model.pbix")
        self.assertEqual(got.tenant, "acme")
        self.assertIs(got.status, JobStatus.QUEUED)

    def test_full_lifecycle_over_the_postgres_branch(self):
        store = self._store()
        job = store.create("x.zip")
        store.mark_processing(job.id)
        self.assertIs(store.get(job.id).status, JobStatus.PROCESSING)
        store.mark_done(job.id, ["md", "html"], warnings=["note"],
                        usage={"Writer": {"calls": 1}})
        done = store.get(job.id)
        self.assertIs(done.status, JobStatus.DONE)
        self.assertEqual(done.formats, ["md", "html"])
        self.assertEqual(done.warnings, ["note"])
        self.assertEqual(done.usage, {"Writer": {"calls": 1}})

    def test_mark_failed_over_the_postgres_branch(self):
        store = self._store()
        job = store.create("x.zip")
        store.mark_failed(job.id, "Could not read the file.")
        self.assertIs(store.get(job.id).status, JobStatus.FAILED)
        self.assertEqual(store.get(job.id).error, "Could not read the file.")

    def test_list_for_tenant_is_scoped_newest_first(self):
        store = self._store()
        a1 = store.create("first.pbix", tenant="acme")
        a2 = store.create("second.pbix", tenant="acme")
        store.create("theirs.pbix", tenant="other")
        listed = store.list_for_tenant("acme")
        self.assertEqual([j.id for j in listed], [a2.id, a1.id])
        self.assertNotIn("theirs.pbix", [j.filename for j in listed])

    def test_list_all_spans_tenants_with_optional_filter(self):
        store = self._store()
        store.create("a.pbix", tenant="t1")
        store.create("b.pbix", tenant="t2")
        self.assertEqual(len(store.list_all()), 2)                    # all tenants
        self.assertEqual(len(store.list_all(tenant="t1")), 1)         # filtered

    def test_timestamps_survive_full_precision_over_postgres(self):
        # DOUBLE PRECISION (not REAL) keeps a ~1.7e9 unix timestamp exact, so
        # the watchdog/TTL math is reliable on Postgres.
        store = self._store()
        job = store.create("x.zip")
        got = store.get(job.id)
        self.assertAlmostEqual(got.created_at, job.created_at, places=3)

    def test_watchdog_force_fail_over_the_postgres_branch(self):
        store = self._store()
        store.processing_timeout = 0.01
        job = store.create("x.zip")
        store.mark_processing(job.id)
        time.sleep(0.05)
        stuck = store.get(job.id)  # triggers sweep()
        self.assertIs(stuck.status, JobStatus.FAILED)
        self.assertIn("timed out", stuck.error)

    def test_outputs_are_in_memory_regardless_of_backend(self):
        # Rendered bytes never touch Postgres — still served from process
        # memory, still TTL-swept, exactly as on the sqlite backend.
        store = self._store()
        job = store.create("x.zip")
        store.store_outputs(job.id, {"md": b"# Report"})
        self.assertEqual(store.get_output(job.id, "md"), b"# Report")


if __name__ == "__main__":
    unittest.main(verbosity=2)
