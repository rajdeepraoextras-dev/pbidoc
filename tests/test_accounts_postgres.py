"""Day 17: Postgres backend wiring for ``AccountStore`` (§9/§12, A2-1's account half).

No live Postgres server is available in this sandbox (same class of gap
already flagged on Days 5/6/16 for provider credentials and multi-instance
testing), so these tests verify wiring against a **fake ``psycopg`` module**
— the same "fake SDK module" pattern already established in
``test_agents.py`` for Cohere/MeshAPI/OpenAI/Anthropic. The fake connection is
itself backed by a real in-memory sqlite3 database (translating the ``%s``
placeholders ``AccountStore`` sends back to ``?`` before executing), so these
tests genuinely exercise the full create/verify/list/revoke/quota code path
end-to-end through the Postgres branch of ``_Connection`` — not just a
call-was-made assertion — without needing a real Postgres server.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from pbicompass.service.accounts import AccountStore, is_postgres_url


class IsPostgresUrlTest(unittest.TestCase):
    def test_recognizes_both_postgres_schemes(self):
        self.assertTrue(is_postgres_url("postgres://user:pw@host/db"))
        self.assertTrue(is_postgres_url("postgresql://user:pw@host/db"))

    def test_sqlite_paths_and_memory_are_not_postgres(self):
        self.assertFalse(is_postgres_url(":memory:"))
        self.assertFalse(is_postgres_url("pbicompass.db"))
        self.assertFalse(is_postgres_url("/data/pbicompass.db"))


class _FakePsycopgCursor:
    def __init__(self, sqlite_conn: sqlite3.Connection) -> None:
        self._conn = sqlite_conn
        self._cur = sqlite_conn.cursor()

    def execute(self, sql: str, params=()):
        # AccountStore sends %s-style SQL through the Postgres branch;
        # translate back to '?' to run it against the sqlite3 connection
        # standing in for a real Postgres server in this fake. The one
        # parameter-free multi-statement script (schema DDL) is the one
        # case real Postgres allows in a single ``execute`` that sqlite3's
        # cursor.execute doesn't — route it through executescript instead,
        # a difference in this stand-in only, not in ``_Connection`` itself
        # (which never calls cursor().execute() for the script path either).
        translated = sql.replace("%s", "?")
        if not params and translated.strip().count(";") > 1:
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

    def close(self) -> None:
        self._sqlite.close()


def _install_fake_psycopg() -> tuple[ModuleType, dict]:
    captured: dict = {}

    def fake_connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured["kwargs"] = kwargs
        return _FakePsycopgConnection(sqlite3.connect(":memory:", check_same_thread=False))

    fake_module = ModuleType("psycopg")
    fake_module.connect = fake_connect
    fake_module.rows = SimpleNamespace(dict_row=object())
    return fake_module, captured


class AccountStorePostgresBackendTest(unittest.TestCase):
    def test_missing_psycopg_raises_a_clear_install_message(self):
        with patch.dict(sys.modules, {"psycopg": None}):
            with self.assertRaises(RuntimeError) as ctx:
                AccountStore("postgres://user:pw@host/db")
        self.assertIn("pbicompass[postgres]", str(ctx.exception))

    def test_connects_with_the_given_url_and_dict_row_factory(self):
        fake_module, captured = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgresql://user:pw@host/db")
            self.addCleanup(store.close)
        self.assertEqual(captured["dsn"], "postgresql://user:pw@host/db")
        self.assertIs(captured["kwargs"]["row_factory"], fake_module.rows.dict_row)

    def test_full_account_lifecycle_over_the_postgres_branch(self):
        fake_module, _ = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgres://user:pw@host/db")
            self.addCleanup(store.close)

            acct, key = store.create_account("acme", name="Acme BI", plan="pro")
            self.assertTrue(key.startswith("pbicompass_sk_"))
            verified = store.verify(key)
            self.assertEqual(verified.tenant, "acme")
            self.assertEqual(verified.plan, "pro")
            self.assertIsNone(store.verify("pbicompass_sk_wrong"))

            self.assertEqual([a.tenant for a in store.list_accounts()], ["acme"])

            allowed, used, limit = store.try_consume("acme", "pro")
            self.assertEqual((allowed, used, limit), (True, 1, 10))
            self.assertEqual(store.usage_this_month("acme"), 1)

            self.assertTrue(store.revoke_account(acct.id))
            self.assertIsNone(store.verify(key))
            self.assertFalse(store.revoke_account(acct.id))

    def test_onboarding_profile_and_set_plan_over_the_postgres_branch(self):
        # Day 33: company/role columns (added via _ensure_column's ALTER on
        # the Postgres branch) and self-serve set_plan(), proven end-to-end
        # over the same fake-psycopg-backed-by-sqlite path as the lifecycle
        # test above.
        fake_module, _ = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgres://user:pw@host/db")
            self.addCleanup(store.close)

            acct, key = store.create_account("acme", name="Acme BI", plan="free",
                                             company="Acme Analytics", role="Head of BI")
            verified = store.verify(key)
            self.assertEqual(verified.company, "Acme Analytics")
            self.assertEqual(verified.role, "Head of BI")

            # JIT-provision path carries profile on first creation only; the
            # "pro" pick is ignored (Day 38: paid plans aren't self-serve
            # yet), so the account always starts on free.
            jit = store.get_or_create_account_for_supabase_user(
                "sub-pg-1", "u@acme.com", company="Co", role="Analyst", plan="pro")
            self.assertEqual(jit.plan, "free")
            self.assertEqual(jit.company, "Co")

            # self-serve plan change
            self.assertTrue(store.set_plan(acct.id, "pro"))
            self.assertEqual(store.verify(key).plan, "pro")
            with self.assertRaises(ValueError):
                store.set_plan(acct.id, "not-a-plan")

            # v5 snapshot carries the new columns (company/role + email/blocked)
            self.assertEqual(store.dump()["version"], 5)

    def test_quota_upsert_blocks_over_the_postgres_branch(self):
        fake_module, _ = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgres://user:pw@host/db")
            self.addCleanup(store.close)
            with patch.dict("pbicompass.service.accounts.PLAN_LIMITS",
                            {"free": 1, "pro": 200, "business": 100000}, clear=True):
                store.create_account("t", plan="free")
                self.assertEqual(store.try_consume("t", "free"), (True, 1, 1))
                self.assertEqual(store.try_consume("t", "free"), (False, 1, 1))

    def test_dump_and_restore_over_the_postgres_branch(self):
        # Day 20's backup/restore drill mechanism, proven over the Postgres
        # code path the same way every other method above is: a fake
        # ``psycopg`` module backed by a real sqlite3 connection standing in
        # for the server, exercising ``_Connection``'s Postgres branch
        # end-to-end (upsert-by-primary-key SQL included) rather than only
        # asserting a call was made.
        fake_module, _ = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgres://user:pw@host/db")
            self.addCleanup(store.close)
            _, key = store.create_account("acme", plan="pro")
            store.try_consume("acme", "pro")
            snapshot = store.dump()

        fake_module2, _ = _install_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module2}):
            restored = AccountStore("postgres://user:pw@host/db2")
            self.addCleanup(restored.close)
            restored.restore(snapshot)
            restored.restore(snapshot)  # idempotent re-apply must not error/duplicate

            self.assertEqual(restored.verify(key).tenant, "acme")
            self.assertEqual(restored.usage_this_month("acme"), 1)
            self.assertEqual(len(restored.list_accounts()), 1)


class TryConsumeAmbiguousColumnRegressionTest(unittest.TestCase):
    """Day 36 production incident: ``try_consume``'s upsert used a bare
    ``SET count = count + 1``, which real Postgres rejects with
    ``psycopg.errors.AmbiguousColumn`` inside an ``ON CONFLICT DO UPDATE``
    (the target row's existing value and the row that would have been
    inserted are both named ``count`` in that scope) -- sqlite3 has no such
    ambiguity rule, which is exactly why the sqlite-backed
    ``_install_fake_psycopg`` fake used elsewhere in this file never caught
    it. This fake instead encodes Postgres's actual rule for this query
    shape: a bare, unqualified ``count`` on the right-hand side of the SET
    inside ON CONFLICT DO UPDATE raises; a table-qualified or
    ``excluded.``-qualified reference does not."""

    @staticmethod
    def _install_ambiguity_checking_fake_psycopg():
        import re

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row

        class _Cursor:
            def execute(self, sql, params=()):
                if "on conflict" in sql.lower() and "do update set" in sql.lower():
                    set_clause = sql.lower().split("do update set", 1)[1]
                    # A bare "count = count + 1" (no "usage." or "excluded."
                    # qualifier immediately before the right-hand "count")
                    # reproduces the real ambiguity Postgres rejects.
                    if re.search(r"count\s*=\s*count\s*\+", set_clause):
                        raise RuntimeError(
                            'AmbiguousColumn: column reference "count" is ambiguous'
                        )
                translated = sql.replace("%s", "?")
                self._cur = conn.cursor()
                if not params and translated.strip().count(";") > 1:
                    conn.executescript(translated)
                else:
                    self._cur.execute(translated, params)
                return self

            def fetchone(self):
                row = self._cur.fetchone()
                return dict(row) if row is not None else None

            def fetchall(self):
                return [dict(r) for r in self._cur.fetchall()]

            @property
            def rowcount(self):
                return self._cur.rowcount

        class _Conn:
            def cursor(self):
                return _Cursor()

            def commit(self):
                conn.commit()

            def rollback(self):
                conn.rollback()

            def close(self):
                conn.close()

        fake_module = ModuleType("psycopg")
        fake_module.connect = lambda dsn, **kwargs: _Conn()
        fake_module.rows = SimpleNamespace(dict_row=object())
        return fake_module

    def test_try_consume_upsert_is_not_ambiguous_on_postgres(self):
        fake_module = self._install_ambiguity_checking_fake_psycopg()
        with patch.dict(sys.modules, {"psycopg": fake_module}):
            store = AccountStore("postgres://user:pw@host/db")
            self.addCleanup(store.close)
            store.create_account("acme", plan="pro")
            # Two consecutive calls exercise both the INSERT and the
            # ON CONFLICT DO UPDATE (increment) branches of the same upsert.
            allowed1, used1, _ = store.try_consume("acme", "pro")
            allowed2, used2, _ = store.try_consume("acme", "pro")
            self.assertTrue(allowed1)
            self.assertTrue(allowed2)
            self.assertEqual((used1, used2), (1, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
