"""Accounts, API keys, and freemium quotas — SQLite by default, Postgres in prod.

This is the multi-tenancy layer: each account belongs to a ``tenant`` and holds
a hashed API key and a plan. Jobs are tagged with the caller's tenant so users
only ever see their own work. Per-plan daily quotas implement the freemium tier.

Only account metadata and per-day usage *counts* are stored — never customer
report metadata, preserving the zero-retention guarantee.

Keys are high-entropy random tokens, so a fast SHA-256 hash is sufficient (no
slow password KDF needed). The raw key is shown once at creation and never
stored.

Backend selection (Day 17, A2-1's account half): ``db_path`` starting with
``postgres://`` or ``postgresql://`` routes to Postgres (via the optional
``psycopg`` driver, ``pip install "pbicompass[postgres]"``); anything else is
a stdlib ``sqlite3`` path (including ``:memory:``, the test/self-host
default). Every method below is written once against the shared
:class:`_Connection` wrapper, so the SQL and row-access code is identical for
both backends — only placeholder style (``?`` vs ``%s``) and how a script of
several statements is executed actually differ between them.

Identity (Day 29+) is Supabase Auth's problem, not this module's: there is no
password hash, session, email-verification token, or OIDC state here anymore
— ``account_users`` just maps a Supabase user id to the tenant/plan/quota
entity this module has always owned. A pre-Day-29 database still has the
retired ``users``/``sessions``/``email_tokens``/``oidc_states``/``memberships``
tables sitting on disk; they're simply no longer read or written, and a
DEPLOYMENT.md-documented ``DROP TABLE`` is the intentional, explicit way to
reclaim them (never done automatically here, to avoid a silent-data-loss
migration on startup).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Daily document quota per plan (jobs accepted per UTC day).
PLAN_LIMITS = {"free": 10, "pro": 200, "enterprise": 100_000}
KEY_PREFIX = "pbicompass_sk_"
MAX_API_KEYS_PER_ACCOUNT = 20             # soft cap so a dashboard user can't mint keys unbounded


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_postgres_url(db_path: str) -> bool:
    return db_path.startswith("postgres://") or db_path.startswith("postgresql://")


class _Connection:
    """Unifies sqlite3/psycopg placeholder and row-access differences behind
    one surface (``execute``/``executemany``/``executescript``/``commit``/
    ``close``), so ``AccountStore``'s SQL and row-access code stays identical
    for both backends. ``psycopg`` is imported lazily — a sqlite-only install
    never needs it (keeps the zero-dependency default intact)."""

    def __init__(self, db_path: str) -> None:
        self.is_postgres = is_postgres_url(db_path)
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - exercised via fake module in tests
                raise RuntimeError(
                    "A postgres:// PBICOMPASS_DB URL needs the 'postgres' extra: "
                    "pip install \"pbicompass[postgres]\""
                ) from exc
            self._conn = psycopg.connect(db_path, row_factory=psycopg.rows.dict_row)
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

    def _sql(self, sql: str) -> str:
        # Safe unconditionally: none of this module's SQL text contains a
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


@dataclass
class Account:
    id: str
    tenant: str
    name: str
    plan: str
    created_at: float
    quota_override: Optional[int] = None  # admin manual override (Day 28); None = use PLAN_LIMITS[plan]


@dataclass
class ApiKeyInfo:
    """Metadata about an API key — never the key itself (only its hash is
    stored; the raw value is shown once at creation)."""
    id: str
    name: str
    created_at: float
    is_primary: bool  # the key minted with the account (labeled "Default")


class AccountStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        # One shared connection guarded by a lock: works for both file/
        # in-memory sqlite and a Postgres connection across FastAPI's
        # threadpool. (A single serialized connection caps write concurrency
        # under Postgres too — acceptable for this stage's account/quota
        # write volume; a pool is a later scaling step, not needed yet.)
        self._conn = _Connection(db_path)
        self._lock = threading.Lock()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    key_hash TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL DEFAULT 'free',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage (
                    tenant TEXT NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant, day)
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_users (
                    user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at REAL NOT NULL,
                    PRIMARY KEY (user_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS admin_users (
                    user_id TEXT PRIMARY KEY,
                    granted_at REAL NOT NULL
                );
                """
            )
            # Day 28: accounts.quota_override, added to a table that may
            # already have rows -- an idempotent ALTER (see _ensure_column).
            self._ensure_column("accounts", "quota_override", "INTEGER")
            # Day 24: ``api_keys`` is the single source of truth for
            # ``verify()``, enabling per-key create/revoke from the account
            # dashboard. Backfill every existing account's primary key into
            # it (idempotent) so a DB created before Day 24 keeps working and
            # revocation is real (verify no longer consults accounts.key_hash).
            # Done in Python, not pure SQL, because generating a per-row id
            # isn't portable across sqlite/Postgres.
            legacy = self._conn.execute(
                "SELECT a.id, a.key_hash, a.created_at FROM accounts a "
                "WHERE NOT EXISTS (SELECT 1 FROM api_keys k WHERE k.key_hash = a.key_hash)"
            ).fetchall()
            for row in legacy:
                self._conn.execute(
                    "INSERT INTO api_keys (id, account_id, key_hash, name, created_at) VALUES (?,?,?,?,?)",
                    (uuid.uuid4().hex, row["id"], row["key_hash"], "Default", row["created_at"]),
                )
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        """Idempotently add a column that didn't exist in an earlier schema
        version. Attempts the ALTER and swallows only a duplicate-column
        error (sqlite3 and psycopg raise different exception types for it,
        so this checks the message rather than the type) — any other
        failure still propagates. A failed ALTER poisons a Postgres
        transaction until rolled back, so that happens unconditionally
        before deciding whether to re-raise."""
        try:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            message = str(exc).lower()
            if "duplicate column" not in message and "already exists" not in message:
                raise

    # -- accounts -----------------------------------------------------------
    def create_account(self, tenant: str, name: str = "", plan: str = "free") -> tuple[Account, str]:
        """Create an account and return (account, raw_api_key). Key shown once."""
        if plan not in PLAN_LIMITS:
            raise ValueError(f"Unknown plan '{plan}'. Choose from {sorted(PLAN_LIMITS)}.")
        raw_key = KEY_PREFIX + secrets.token_urlsafe(24)
        acct = Account(id=uuid.uuid4().hex, tenant=tenant, name=name, plan=plan,
                       created_at=time.time())
        key_hash = _hash_key(raw_key)
        with self._lock:
            self._conn.execute(
                "INSERT INTO accounts (id, tenant, name, key_hash, plan, created_at) VALUES (?,?,?,?,?,?)",
                (acct.id, acct.tenant, acct.name, key_hash, acct.plan, acct.created_at),
            )
            # The account's first key is a managed api_keys row too (labeled
            # "Default"), so it shows up in the dashboard and can be revoked
            # like any other. accounts.key_hash is kept (NOT NULL, legacy) but
            # is no longer what verify() consults.
            self._conn.execute(
                "INSERT INTO api_keys (id, account_id, key_hash, name, created_at) VALUES (?,?,?,?,?)",
                (uuid.uuid4().hex, acct.id, key_hash, "Default", acct.created_at),
            )
            self._conn.commit()
        return acct, raw_key

    def verify(self, raw_key: Optional[str]) -> Optional[Account]:
        # Authoritative lookup is the api_keys table (Day 24): every account's
        # keys — the original "Default" and any created since — live there, so
        # deleting a row is real revocation. The backfill in _init_schema
        # guarantees pre-Day-24 accounts have a row here too.
        if not raw_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT a.* FROM api_keys k JOIN accounts a ON a.id = k.account_id "
                "WHERE k.key_hash = ?", (_hash_key(raw_key),)
            ).fetchone()
        return self._row_to_account(row) if row else None

    def list_accounts(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts ORDER BY created_at"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def revoke_account(self, account_id: str) -> bool:
        """Delete an account — all its API keys stop working immediately.
        Returns True if an account was deleted, False if the id didn't exist."""
        with self._lock:
            self._conn.execute("DELETE FROM api_keys WHERE account_id = ?", (account_id,))
            cur = self._conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # -- per-account API key management (Day 24, §7.6) ----------------------
    def create_api_key(self, account_id: str, name: str = "") -> tuple[ApiKeyInfo, str]:
        """Mint an additional API key on an account. Returns
        ``(ApiKeyInfo, raw_key)`` — the raw key is shown once and never
        stored. Raises ``ValueError`` past the per-account soft cap."""
        raw_key = KEY_PREFIX + secrets.token_urlsafe(24)
        kid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) AS c FROM api_keys WHERE account_id = ?", (account_id,)
            ).fetchone()["c"]
            if count >= MAX_API_KEYS_PER_ACCOUNT:
                raise ValueError(f"An account can have at most {MAX_API_KEYS_PER_ACCOUNT} API keys.")
            self._conn.execute(
                "INSERT INTO api_keys (id, account_id, key_hash, name, created_at) VALUES (?,?,?,?,?)",
                (kid, account_id, _hash_key(raw_key), name or "API key", now),
            )
            self._conn.commit()
        return ApiKeyInfo(id=kid, name=name or "API key", created_at=now, is_primary=False), raw_key

    def list_api_keys(self, account_id: str) -> list[ApiKeyInfo]:
        """Key metadata for an account (never the keys themselves)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT k.id, k.name, k.created_at, k.key_hash, a.key_hash AS primary_hash "
                "FROM api_keys k JOIN accounts a ON a.id = k.account_id "
                "WHERE k.account_id = ? ORDER BY k.created_at",
                (account_id,),
            ).fetchall()
        return [
            ApiKeyInfo(id=r["id"], name=r["name"], created_at=r["created_at"],
                       is_primary=(r["key_hash"] == r["primary_hash"]))
            for r in rows
        ]

    def revoke_api_key(self, account_id: str, key_id: str) -> bool:
        """Delete a single API key by id (scoped to its account so one
        account can't revoke another's). Real revocation — verify() only
        consults api_keys. Returns True if a key was deleted."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM api_keys WHERE id = ? AND account_id = ?", (key_id, account_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _row_to_account(row) -> Account:
        return Account(id=row["id"], tenant=row["tenant"], name=row["name"],
                       plan=row["plan"], created_at=row["created_at"],
                       quota_override=row["quota_override"] if "quota_override" in row.keys() else None)

    # -- accounts for Supabase-authenticated callers (Day 28) ---------------
    def get_or_create_account_for_supabase_user(self, user_id: str, email: str,
                                                 name: str = "") -> Account:
        """JIT-provision an account for a Supabase-authenticated caller on
        their first request — no Supabase webhook needed. ``user_id`` is the
        Supabase ``sub`` claim (a UUID string); ``account_users`` maps it to
        the tenant/account/quota entity this app has always used. A user's
        very first authenticated request creates their account (plan
        ``"free"``, its own tenant, and a "Default" API key for programmatic
        access), the same way self-serve signup used to."""
        with self._lock:
            row = self._conn.execute(
                "SELECT a.* FROM accounts a JOIN account_users m ON m.account_id = a.id "
                "WHERE m.user_id = ? ORDER BY m.created_at LIMIT 1",
                (user_id,),
            ).fetchone()
        if row is not None:
            return self._row_to_account(row)
        acct, _raw_key = self.create_account(
            tenant="u-" + secrets.token_hex(8), name=name or email, plan="free"
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO account_users (user_id, account_id, role, created_at) VALUES (?,?,?,?)",
                (user_id, acct.id, "owner", time.time()),
            )
            self._conn.commit()
        return acct

    # -- admin roles (Day 28, wired up by service/admin_users.py) -----------
    def is_admin(self, user_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM admin_users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row is not None

    def grant_admin(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO admin_users (user_id, granted_at) VALUES (?,?) "
                "ON CONFLICT(user_id) DO NOTHING",
                (user_id, time.time()),
            )
            self._conn.commit()

    def revoke_admin(self, user_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM admin_users WHERE user_id = ?", (user_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def set_quota_override(self, account_id: str, limit: Optional[int]) -> bool:
        """Admin manual override of an account's quota (``None`` clears it,
        reverting to ``PLAN_LIMITS[plan]``). Returns True if the account
        existed."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE accounts SET quota_override = ? WHERE id = ?", (limit, account_id)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # -- quotas -------------------------------------------------------------
    def limit_for(self, plan: str, override: Optional[int] = None) -> int:
        if override is not None:
            return override
        return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    def usage_today(self, tenant: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE tenant = ? AND day = ?",
                (tenant, date.today().isoformat()),
            ).fetchone()
        return row["count"] if row else 0

    def try_consume(self, tenant: str, plan: str, override: Optional[int] = None) -> tuple[bool, int, int]:
        """Atomically check and increment today's usage.

        Returns (allowed, used_after, limit). When not allowed, ``used_after``
        is the unchanged current count. ``override`` is an account's
        ``quota_override`` (Day 28, admin manual override) if it has one.
        """
        limit = self.limit_for(plan, override)
        day = date.today().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE tenant = ? AND day = ?", (tenant, day)
            ).fetchone()
            current = row["count"] if row else 0
            if current >= limit:
                return False, current, limit
            self._conn.execute(
                "INSERT INTO usage (tenant, day, count) VALUES (?,?,1) "
                "ON CONFLICT(tenant, day) DO UPDATE SET count = count + 1",
                (tenant, day),
            )
            self._conn.commit()
            return True, current + 1, limit

    # -- backup / restore drill (Day 20, §9/§12) -----------------------------
    def dump(self) -> dict:
        """Logical snapshot of every row this store owns — a portable
        alternative to ``pg_dump``/``pg_restore`` that works identically
        against either backend (no client binaries required on whatever
        platform runs the app), for the actual restore-drill mechanism
        documented in DEPLOYMENT.md alongside whatever automated snapshotting
        a managed Postgres provider already does at the infrastructure
        level. Content-free by construction — this store never holds
        anything but account metadata, a hashed API key, and per-day usage
        counts, never report data."""
        with self._lock:
            accounts = [dict(r) for r in self._conn.execute(
                "SELECT id, tenant, name, key_hash, plan, created_at, quota_override FROM accounts"
            ).fetchall()]
            usage = [dict(r) for r in self._conn.execute(
                "SELECT tenant, day, count FROM usage"
            ).fetchall()]
            # api_keys is the authoritative key store (Day 24) — must be in the
            # snapshot or a restored account couldn't authenticate at all.
            api_keys = [dict(r) for r in self._conn.execute(
                "SELECT id, account_id, key_hash, name, created_at FROM api_keys"
            ).fetchall()]
            # account_users/admin_users (Day 28) — Supabase-user-id mappings;
            # a restore without them would lose ownership/admin links.
            account_users = [dict(r) for r in self._conn.execute(
                "SELECT user_id, account_id, role, created_at FROM account_users"
            ).fetchall()]
            admin_users = [dict(r) for r in self._conn.execute(
                "SELECT user_id, granted_at FROM admin_users"
            ).fetchall()]
        return {
            "version": 3, "accounts": accounts, "usage": usage, "api_keys": api_keys,
            "account_users": account_users, "admin_users": admin_users,
        }

    def restore(self, snapshot: dict) -> None:
        """Restore rows from :meth:`dump`'s output into an **empty** store
        (a fresh database, or one about to be reset for a restore drill) —
        upserts by primary key so it is safe to call again with the same
        snapshot. Does not delete rows absent from the snapshot; call this
        against a clean database for a true point-in-time restore."""
        with self._lock:
            for a in snapshot.get("accounts", []):
                self._conn.execute(
                    "INSERT INTO accounts (id, tenant, name, key_hash, plan, created_at, quota_override) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "tenant=excluded.tenant, name=excluded.name, key_hash=excluded.key_hash, "
                    "plan=excluded.plan, created_at=excluded.created_at, "
                    "quota_override=excluded.quota_override",
                    (a["id"], a["tenant"], a["name"], a["key_hash"], a["plan"], a["created_at"],
                     a.get("quota_override")),
                )
            for u in snapshot.get("usage", []):
                self._conn.execute(
                    "INSERT INTO usage (tenant, day, count) VALUES (?,?,?) "
                    "ON CONFLICT(tenant, day) DO UPDATE SET count = excluded.count",
                    (u["tenant"], u["day"], u["count"]),
                )
            for k in snapshot.get("api_keys", []):
                self._conn.execute(
                    "INSERT INTO api_keys (id, account_id, key_hash, name, created_at) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "account_id=excluded.account_id, key_hash=excluded.key_hash, "
                    "name=excluded.name, created_at=excluded.created_at",
                    (k["id"], k["account_id"], k["key_hash"], k["name"], k["created_at"]),
                )
            # account_users/admin_users are new in v3 -- absent entirely from
            # an older snapshot, so .get(..., []) is a correct no-op restore.
            for m in snapshot.get("account_users", []):
                self._conn.execute(
                    "INSERT INTO account_users (user_id, account_id, role, created_at) "
                    "VALUES (?,?,?,?) ON CONFLICT(user_id, account_id) DO UPDATE SET "
                    "role=excluded.role, created_at=excluded.created_at",
                    (m["user_id"], m["account_id"], m["role"], m["created_at"]),
                )
            for adm in snapshot.get("admin_users", []):
                self._conn.execute(
                    "INSERT INTO admin_users (user_id, granted_at) VALUES (?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET granted_at=excluded.granted_at",
                    (adm["user_id"], adm["granted_at"]),
                )
            self._conn.commit()
