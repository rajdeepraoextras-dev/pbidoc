"""Accounts, API keys, and freemium quotas — backed by stdlib ``sqlite3``.

This is the multi-tenancy layer: each account belongs to a ``tenant`` and holds
a hashed API key and a plan. Jobs are tagged with the caller's tenant so users
only ever see their own work. Per-plan daily quotas implement the freemium tier.

Only account metadata and per-day usage *counts* are stored — never customer
report metadata, preserving the zero-retention guarantee.

Keys are high-entropy random tokens, so a fast SHA-256 hash is sufficient (no
slow password KDF needed). The raw key is shown once at creation and never
stored.
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


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Account:
    id: str
    tenant: str
    name: str
    plan: str
    created_at: float


class AccountStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        # One shared connection guarded by a lock: works for both file and
        # in-memory DBs across FastAPI's threadpool.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
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
                """
            )
            self._conn.commit()

    # -- accounts -----------------------------------------------------------
    def create_account(self, tenant: str, name: str = "", plan: str = "free") -> tuple[Account, str]:
        """Create an account and return (account, raw_api_key). Key shown once."""
        if plan not in PLAN_LIMITS:
            raise ValueError(f"Unknown plan '{plan}'. Choose from {sorted(PLAN_LIMITS)}.")
        raw_key = KEY_PREFIX + secrets.token_urlsafe(24)
        acct = Account(id=uuid.uuid4().hex, tenant=tenant, name=name, plan=plan,
                       created_at=time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO accounts (id, tenant, name, key_hash, plan, created_at) VALUES (?,?,?,?,?,?)",
                (acct.id, acct.tenant, acct.name, _hash_key(raw_key), acct.plan, acct.created_at),
            )
            self._conn.commit()
        return acct, raw_key

    def verify(self, raw_key: Optional[str]) -> Optional[Account]:
        if not raw_key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE key_hash = ?", (_hash_key(raw_key),)
            ).fetchone()
        return self._row_to_account(row) if row else None

    def list_accounts(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM accounts ORDER BY created_at"
            ).fetchall()
        return [self._row_to_account(r) for r in rows]

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> Account:
        return Account(id=row["id"], tenant=row["tenant"], name=row["name"],
                       plan=row["plan"], created_at=row["created_at"])

    # -- quotas -------------------------------------------------------------
    def limit_for(self, plan: str) -> int:
        return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    def usage_today(self, tenant: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM usage WHERE tenant = ? AND day = ?",
                (tenant, date.today().isoformat()),
            ).fetchone()
        return row["count"] if row else 0

    def try_consume(self, tenant: str, plan: str) -> tuple[bool, int, int]:
        """Atomically check and increment today's usage.

        Returns (allowed, used_after, limit). When not allowed, ``used_after``
        is the unchanged current count.
        """
        limit = self.limit_for(plan)
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
