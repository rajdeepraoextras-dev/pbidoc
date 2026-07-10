"""Shared SQLite/Postgres connection wrapper for the service's stores.

Both :class:`~pbicompass.service.accounts.AccountStore` and
:class:`~pbicompass.service.jobs.JobStore` are written once against this one
surface, so their SQL and row-access code is identical for both backends —
only placeholder style (``?`` vs ``%s``), how a multi-statement DDL script is
run, and the binary-column type (``BLOB`` vs ``BYTEA``) actually differ.

``db_path`` starting with ``postgres://`` / ``postgresql://`` routes to
Postgres (via the optional ``psycopg`` driver, ``pip install
"pbicompass[postgres]"``); anything else is a stdlib ``sqlite3`` path
(including ``:memory:``, the test/self-host default). ``psycopg`` is imported
lazily, so a sqlite-only install never needs it.

Extracted from ``accounts.py`` (Day 34) so the job registry can move off its
own private sqlite handle onto the same managed-Postgres path — no second
dialect fork.
"""

from __future__ import annotations

import sqlite3


def is_postgres_url(db_path: str) -> bool:
    return db_path.startswith("postgres://") or db_path.startswith("postgresql://")


class _Connection:
    """Unifies sqlite3/psycopg placeholder, row-access, and binary-type
    differences behind one surface (``execute``/``executemany``/
    ``executescript``/``commit``/``rollback``/``close`` + ``is_postgres`` and
    ``blob_type``), so a store's SQL and row-access code stays identical for
    both backends. ``psycopg`` is imported lazily — a sqlite-only install
    never needs it (keeps the zero-dependency default intact)."""

    def __init__(self, db_path: str) -> None:
        self.is_postgres = is_postgres_url(db_path)
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - exercised via fake module in tests
                raise RuntimeError(
                    "A postgres:// database URL needs the 'postgres' extra: "
                    "pip install \"pbicompass[postgres]\""
                ) from exc
            self._conn = psycopg.connect(db_path, row_factory=psycopg.rows.dict_row)
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def _sql(self, sql: str) -> str:
        # Safe unconditionally: none of the callers' SQL text contains a
        # literal '?' outside of a placeholder position.
        return sql.replace("?", "%s") if self.is_postgres else sql

    def execute(self, sql: str, params: tuple = ()):
        cur = self._conn.cursor()
        cur.execute(self._sql(sql), params)
        return cur

    def executemany(self, sql: str, seq) -> None:
        cur = self._conn.cursor()
        cur.executemany(self._sql(sql), seq)

    def executescript(self, script: str) -> None:
        # psycopg has no sqlite-style ``executescript``, but both engines
        # happily run a parameter-free, ';'-separated multi-statement string
        # through a single plain ``execute`` — used only for schema DDL.
        if self.is_postgres:
            self._conn.cursor().execute(script)
        else:
            self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()
