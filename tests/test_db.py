"""Day 36: ``_Connection``'s auto-rollback-on-error (the InFailedSqlTransaction
production incident).

Real Postgres aborts a connection's whole transaction on any failed
statement -- every subsequent statement on that connection then fails with
``InFailedSqlTransaction`` until something calls ``rollback()``. Nothing in
``_Connection.execute``/``executemany``/``executescript`` did that, so in
production a single bad query permanently wedged the one shared connection
(serialized by AccountStore/JobStore's lock across every request) until the
process was restarted -- a full outage from one failed statement.

The fake ``psycopg`` module used elsewhere (``test_accounts_postgres.py``) is
backed by real sqlite3, which has no such "poisoned transaction" concept, so
it can't reproduce this bug. This file uses a purpose-built fake that DOES
simulate it: once a statement fails, every subsequent statement raises the
same error until ``rollback()`` is called on the connection.
"""

from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from pbicompass.service.db import _Connection


class _PoisonableCursor:
    def __init__(self, conn: "_PoisonableConnection") -> None:
        self._conn = conn

    def execute(self, sql, params=()):
        if self._conn.poisoned:
            raise RuntimeError("current transaction is aborted, commands ignored until end of transaction block")
        if sql.startswith("FAIL"):
            self._conn.poisoned = True
            raise RuntimeError("simulated statement failure")
        return self

    def executemany(self, sql, seq):
        return self.execute(sql)

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _PoisonableConnection:
    """Simulates real Postgres's abort-the-whole-transaction-on-error
    behavior: after one failed statement, every later statement on this same
    connection fails too, until ``rollback()`` clears the poisoned flag."""

    def __init__(self) -> None:
        self.poisoned = False
        self.rollback_calls = 0

    def cursor(self):
        return _PoisonableCursor(self)

    def commit(self):
        pass

    def rollback(self):
        self.rollback_calls += 1
        self.poisoned = False

    def close(self):
        pass


def _install_poisonable_psycopg():
    conn = _PoisonableConnection()
    fake_module = ModuleType("psycopg")
    fake_module.connect = lambda dsn, **kwargs: conn
    fake_module.rows = SimpleNamespace(dict_row=object())
    return fake_module, conn


class ConnectionAutoRollbackTest(unittest.TestCase):
    def _connection(self) -> tuple[_Connection, _PoisonableConnection]:
        fake_module, conn = _install_poisonable_psycopg()
        patcher = patch.dict(sys.modules, {"psycopg": fake_module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return _Connection("postgres://user:pw@host/db"), conn

    def test_a_failed_statement_rolls_back_so_the_next_call_succeeds(self):
        wrapper, conn = self._connection()
        with self.assertRaises(RuntimeError):
            wrapper.execute("FAIL this statement")
        self.assertEqual(conn.rollback_calls, 1)
        # Before the fix, this next call would raise InFailedSqlTransaction
        # (simulated here as the poisoned-cursor error) because nothing ever
        # rolled back the aborted transaction from the failed statement above.
        wrapper.execute("SELECT 1")  # must not raise

    def test_the_original_exception_still_propagates(self):
        wrapper, _conn = self._connection()
        with self.assertRaises(RuntimeError) as ctx:
            wrapper.execute("FAIL this statement")
        self.assertIn("simulated statement failure", str(ctx.exception))

    def test_executemany_also_rolls_back_on_failure(self):
        wrapper, conn = self._connection()
        with self.assertRaises(RuntimeError):
            wrapper.executemany("FAIL", [(1,), (2,)])
        self.assertEqual(conn.rollback_calls, 1)
        wrapper.execute("SELECT 1")  # connection usable again

    def test_postgres_script_splitter_ignores_semicolons_inside_comments_and_strings(self):
        wrapper, _conn = self._connection()
        statements = wrapper._pg_script_statements(
            """
            -- comment text; not a statement boundary
            CREATE TABLE first (note TEXT DEFAULT 'literal; value');
            CREATE TABLE second (id INTEGER);
            """
        )
        self.assertEqual(
            statements,
            [
                "-- comment text; not a statement boundary\n            CREATE TABLE first (note TEXT DEFAULT 'literal; value')",
                "CREATE TABLE second (id INTEGER)",
            ],
        )

    def test_executescript_also_rolls_back_on_failure(self):
        wrapper, conn = self._connection()
        with self.assertRaises(RuntimeError):
            wrapper.executescript("FAIL; more sql;")
        self.assertEqual(conn.rollback_calls, 1)
        wrapper.execute("SELECT 1")  # connection usable again

    def test_a_successful_statement_never_rolls_back(self):
        wrapper, conn = self._connection()
        wrapper.execute("SELECT 1")
        self.assertEqual(conn.rollback_calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
