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


class _PostgresReadCursor:
    def __init__(self, conn, cursor) -> None:
        self._conn = conn
        self._cursor = cursor

    def _finish_read(self):
        try:
            self._cursor.close()
        except Exception:
            pass
        try:
            self._conn.commit()
        except Exception:
            pass
        try:
            # Supavisor can keep the server connection "idle in transaction"
            # even when the psycopg client believes the read is finished.
            # An explicit SQL ROLLBACK is harmless after a read and reliably
            # returns the pooled server connection to an idle state.
            self._conn.cursor().execute("ROLLBACK")
        except Exception:
            pass

    def fetchone(self):
        row = self._cursor.fetchone()
        self._finish_read()
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._finish_read()
        return rows

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class _Connection:
    """Unifies sqlite3/psycopg placeholder, row-access, and binary-type
    differences behind one surface (``execute``/``executemany``/
    ``executescript``/``commit``/``rollback``/``close`` + ``is_postgres`` and
    ``blob_type``), so a store's SQL and row-access code stays identical for
    both backends. ``psycopg`` is imported lazily — a sqlite-only install
    never needs it (keeps the zero-dependency default intact)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.is_postgres = is_postgres_url(db_path)
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - exercised via fake module in tests
                raise RuntimeError(
                    "A postgres:// database URL needs the 'postgres' extra: "
                    "pip install \"pbicompass[postgres]\""
                ) from exc
            self._psycopg = psycopg
            self._conn = self._pg_connect()
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def _pg_connect(self):
        # This one connection is shared process-wide behind the stores' lock,
        # so a silently dropped socket (managed-Postgres/cloud-NAT idle
        # disconnects) is catastrophic without these: the next query blocks
        # on a dead socket for the OS TCP retransmit timeout (15+ minutes)
        # *while holding the lock*, wedging every request in the app —
        # 2026-07-13: production hung exactly this way (TLS accepted, zero
        # bytes ever sent, downloads silently dead). TCP keepalives detect a
        # dead peer within ~1 minute — but ONLY on an idle connection; a
        # socket that dies mid-query (data sent, reply pending) is governed
        # by the TCP retransmission timeout instead, 15-25 minutes of hang
        # (2026-07-13, hang #2: the keepalive-enabled build froze exactly
        # this way). tcp_user_timeout bounds that un-ACKed-data wait to 30s,
        # after which the query errors and the reconnect path below takes
        # over. connect_timeout caps the reconnect attempt itself. All are
        # client-side libpq parameters, safe through any pooler —
        # deliberately NOT ``options="-c statement_timeout=..."``, which
        # PgBouncer/Supavisor transaction pooling rejects as an unsupported
        # startup parameter (it would break connecting at all).
        conn = self._psycopg.connect(
            self._db_path,
            row_factory=self._psycopg.rows.dict_row,
            connect_timeout=10,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
            tcp_user_timeout=30000,
        )
        conn.autocommit = True
        return conn

    def _pg_reconnect_and_retry(self, exc: Exception):
        """Reconnect once after a broken-connection error (never after an SQL
        error). Returns the fresh connection, or re-raises ``exc`` if this
        doesn't look like a connection failure or reconnecting fails."""
        if not self.is_postgres:
            raise exc
        broken = getattr(self._conn, "broken", False) or getattr(self._conn, "closed", False)
        op_error = getattr(self._psycopg, "OperationalError", ())
        if not (broken or isinstance(exc, op_error)):
            raise exc
        try:
            self._conn.close()
        except Exception:
            pass
        self._conn = self._pg_connect()
        return self._conn

    def _sql(self, sql: str) -> str:
        # Safe unconditionally: none of the callers' SQL text contains a
        # literal '?' outside of a placeholder position.
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _is_read_query(self, sql: str) -> bool:
        head = sql.lstrip().split(None, 1)[0].lower() if sql.strip() else ""
        return head in ("select", "with", "show")

    def execute(self, sql: str, params: tuple = ()):
        sql = self._sql(sql)
        try:
            cur = self._conn.cursor()
            cur.execute(sql, params)
        except Exception as exc:
            # A failed statement leaves a Postgres connection's transaction
            # "aborted" -- every subsequent statement on it fails with
            # InFailedSqlTransaction until something rolls back. Since this
            # one connection is shared (serialized by AccountStore/JobStore's
            # lock) across every request, one bad query would otherwise wedge
            # the whole process until restart. Roll back immediately so the
            # NEXT call gets a clean transaction (sqlite3's rollback() is a
            # harmless no-op with nothing pending, so this is safe there too).
            try:
                self._conn.rollback()
            except Exception:
                pass  # a broken connection can't roll back either
            # A *connection* failure (dead socket, server closed it) gets one
            # reconnect-and-retry; an SQL error re-raises as before.
            conn = self._pg_reconnect_and_retry(exc)
            cur = conn.cursor()
            cur.execute(sql, params)
        if self.is_postgres and self._is_read_query(sql):
            return _PostgresReadCursor(self._conn, cur)
        return cur

    def executemany(self, sql: str, seq) -> None:
        sql = self._sql(sql)
        try:
            self._conn.cursor().executemany(sql, seq)
        except Exception as exc:
            try:
                self._conn.rollback()
            except Exception:
                pass
            conn = self._pg_reconnect_and_retry(exc)
            conn.cursor().executemany(sql, seq)

    def column_exists(self, table: str, column: str) -> bool:
        if self.is_postgres:
            try:
                row = self.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = ? AND column_name = ? LIMIT 1",
                    (table, column),
                ).fetchone()
                return row is not None
            except Exception:
                pass
        try:
            self.execute(f"SELECT {column} FROM {table} LIMIT 0").fetchone()
            return True
        except Exception:
            return False

    def ping(self) -> bool:
        try:
            if self.is_postgres:
                cur = self._conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                try:
                    cur.close()
                except Exception:
                    pass
                self._conn.cursor().execute("ROLLBACK")
                return True
            self.execute("SELECT 1").fetchone()
            return True
        except Exception:
            try:
                self.rollback()
            except Exception:
                pass
            return False

    def _pg_script_statements(self, script: str) -> list[str]:
        statements: list[str] = []
        current: list[str] = []
        in_single_quote = False
        in_line_comment = False
        i = 0
        while i < len(script):
            char = script[i]
            next_char = script[i + 1] if i + 1 < len(script) else ""

            if in_line_comment:
                current.append(char)
                if char == "\n":
                    in_line_comment = False
                i += 1
                continue

            if in_single_quote:
                current.append(char)
                if char == "'" and next_char == "'":
                    current.append(next_char)
                    i += 2
                    continue
                if char == "'":
                    in_single_quote = False
                i += 1
                continue

            if char == "-" and next_char == "-":
                in_line_comment = True
                current.append(char)
                current.append(next_char)
                i += 2
                continue
            if char == "'":
                in_single_quote = True
                current.append(char)
                i += 1
                continue
            if char == ";":
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
                i += 1
                continue

            current.append(char)
            i += 1

        statement = "".join(current).strip()
        if statement:
            statements.append(statement)
        return statements

    def _pg_executescript(self, conn, script: str) -> None:
        # Supabase's pooler is happier with one DDL statement per execute()
        # than with a sqlite-style semicolon-separated script.
        cur = conn.cursor()
        for statement in self._pg_script_statements(script):
            cur.execute(statement)

    def executescript(self, script: str) -> None:
        # psycopg has no sqlite-style ``executescript``, but both engines
        # happily run a parameter-free, ';'-separated multi-statement string
        # through a single plain ``execute`` — used only for schema DDL.
        try:
            if self.is_postgres:
                self._pg_executescript(self._conn, script)
            else:
                self._conn.executescript(script)
        except Exception as exc:
            try:
                self._conn.rollback()
            except Exception:
                pass
            conn = self._pg_reconnect_and_retry(exc)
            self._pg_executescript(conn, script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()
