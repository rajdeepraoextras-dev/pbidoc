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

from .passwords import hash_password, verify_password

# Daily document quota per plan (jobs accepted per UTC day).
PLAN_LIMITS = {"free": 10, "pro": 200, "enterprise": 100_000}
KEY_PREFIX = "pbicompass_sk_"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
VERIFY_TOKEN_TTL_SECONDS = 60 * 60 * 24   # 24h — email verification link
RESET_TOKEN_TTL_SECONDS = 60 * 60         # 1h  — password-reset link (shorter on purpose)
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

    def close(self) -> None:
        self._conn.close()


@dataclass
class Account:
    id: str
    tenant: str
    name: str
    plan: str
    created_at: float


@dataclass
class User:
    id: str
    email: str
    email_verified: bool
    created_at: float


@dataclass
class SessionInfo:
    user: User
    csrf_token: str


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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    user_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at REAL NOT NULL,
                    PRIMARY KEY (user_id, account_id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oidc_states (
                    state_hash TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                """
            )
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

    # -- users / sessions (Day 21, §7.1/§7.2) --------------------------------
    def create_user(self, email: str, password: str, name: str = "",
                    plan: str = "free") -> tuple[User, Account, str]:
        """Self-serve signup: create a user, a brand-new account/tenant for
        them (they're its ``owner``), and mint an API key on it too (the
        same "one auth method among several" account every admin-created
        account already has — a signed-up user can use either the session
        this creates or that key for programmatic access). Returns
        ``(user, account, raw_api_key)``.

        Raises ``ValueError`` for an invalid email, a too-short password, or
        an email already registered — checked explicitly (a pre-check
        SELECT, not a caught UNIQUE-violation exception) so the error
        message and behavior are identical on both the SQLite and Postgres
        backends, which raise different exception types for a constraint
        violation.
        """
        email = email.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise ValueError("Enter a valid email address.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
        if existing:
            raise ValueError("An account with this email already exists.")

        acct, raw_key = self.create_account(
            tenant="u-" + secrets.token_hex(8), name=name or email, plan=plan
        )
        user = User(id=uuid.uuid4().hex, email=email, email_verified=False, created_at=time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (id, email, password_hash, email_verified, created_at) "
                "VALUES (?,?,?,?,?)",
                (user.id, user.email, hash_password(password), 0, user.created_at),
            )
            self._conn.execute(
                "INSERT INTO memberships (user_id, account_id, role, created_at) VALUES (?,?,?,?)",
                (user.id, acct.id, "owner", time.time()),
            )
            self._conn.commit()
        return user, acct, raw_key

    def get_user_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        return self._row_to_user(row) if row else None

    def authenticate(self, email: str, password: str) -> Optional[User]:
        """Verify email + password. Returns ``None`` on any mismatch
        (unknown email or wrong password) without distinguishing which, so
        a failed attempt can't be used to enumerate registered emails."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
            ).fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return self._row_to_user(row)

    def account_for_user(self, user_id: str) -> Optional[Account]:
        """The account a user belongs to — their own, self-serve-created
        one today; ``memberships`` already supports more than one per user
        for the teams/orgs work in §8, so this returns the oldest
        membership (a user's "home" account) rather than assuming exactly
        one row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT a.* FROM accounts a JOIN memberships m ON m.account_id = a.id "
                "WHERE m.user_id = ? ORDER BY m.created_at LIMIT 1",
                (user_id,),
            ).fetchone()
        return self._row_to_account(row) if row else None

    def create_session(self, user_id: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> tuple[str, str]:
        """Returns ``(raw_session_token, csrf_token)``. The session token is
        shown once as a cookie value and verified by hash, the same
        high-entropy-token-so-a-fast-hash-is-fine reasoning as an API key
        (``_hash_key``'s own docstring). The CSRF token is a separate random
        value returned alongside it (not itself a bearer credential — its
        security property relies on same-site JS being able to read it back
        out of its own, non-HttpOnly cookie), for the double-submit check on
        state-changing session-authenticated requests.
        """
        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (_hash_key(raw_token), user_id, csrf_token, now, now + ttl_seconds),
            )
            self._conn.commit()
        return raw_token, csrf_token

    def verify_session(self, raw_token: str) -> Optional[SessionInfo]:
        if not raw_token:
            return None
        now = time.time()
        with self._lock:
            # Lazy sweep of expired sessions on read, the same pattern
            # JobStore.sweep() already established -- no separate background
            # task needed for what is, at this stage, a low-write-volume table.
            self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            row = self._conn.execute(
                "SELECT s.csrf_token AS csrf_token, u.* FROM sessions s "
                "JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at >= ?",
                (_hash_key(raw_token), now),
            ).fetchone()
            self._conn.commit()
        if row is None:
            return None
        return SessionInfo(user=self._row_to_user(row), csrf_token=row["csrf_token"])

    def delete_session(self, raw_token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_key(raw_token),))
            self._conn.commit()

    def _delete_sessions_for_user(self, user_id: str) -> None:
        """Invalidate every live session a user has — called on password
        reset so a stolen-password attacker's existing session dies the
        moment the real owner resets (defense in depth: the reset already
        changes the password, this also boots any session opened with the
        old one)."""
        self._conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    @staticmethod
    def _row_to_user(row) -> User:
        return User(id=row["id"], email=row["email"], email_verified=bool(row["email_verified"]),
                   created_at=row["created_at"])

    # -- email verification / password reset tokens (Day 22, §7.4/§7.5) ------
    def create_email_token(self, user_id: str, purpose: str, ttl_seconds: int) -> str:
        """Mint a single-use, hashed, expiring token for ``verify`` or
        ``reset``. The raw token goes out in the email link; only its hash
        is stored (same reasoning as an API key / session token). Any older
        unused token of the same purpose for this user is dropped first, so
        requesting a fresh link invalidates the previous one."""
        if purpose not in ("verify", "reset"):
            raise ValueError(f"Unknown email-token purpose '{purpose}'.")
        raw_token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose)
            )
            self._conn.execute(
                "INSERT INTO email_tokens (token_hash, user_id, purpose, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (_hash_key(raw_token), user_id, purpose, now, now + ttl_seconds),
            )
            self._conn.commit()
        return raw_token

    def consume_email_token(self, raw_token: str, purpose: str) -> Optional[str]:
        """Verify + burn a token. Returns the ``user_id`` on success (and
        deletes the row, so a link works exactly once), ``None`` for an
        unknown/expired/wrong-purpose token. Expired rows are swept lazily
        on read, same pattern as sessions."""
        if not raw_token:
            return None
        now = time.time()
        with self._lock:
            self._conn.execute("DELETE FROM email_tokens WHERE expires_at < ?", (now,))
            row = self._conn.execute(
                "SELECT user_id FROM email_tokens WHERE token_hash = ? AND purpose = ? AND expires_at >= ?",
                (_hash_key(raw_token), purpose, now),
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            self._conn.execute("DELETE FROM email_tokens WHERE token_hash = ?", (_hash_key(raw_token),))
            self._conn.commit()
        return row["user_id"]

    def mark_email_verified(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
            self._conn.commit()

    def set_password(self, user_id: str, new_password: str) -> None:
        """Set a new password (used by the reset flow) and invalidate every
        existing session for that user in the same transaction."""
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        with self._lock:
            self._conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(new_password), user_id),
            )
            self._delete_sessions_for_user(user_id)
            self._conn.commit()

    # -- OIDC / SSO (Day 23, §7.3) ------------------------------------------
    def create_oidc_state(self, nonce: str, code_verifier: str, ttl_seconds: int) -> str:
        """Persist the per-flow ``nonce``/``code_verifier`` server-side keyed
        by a fresh random ``state`` (hashed at rest, like every other token
        here), and return the raw ``state`` to put in the authorize URL. The
        callback looks it up to (a) reject a forged/replayed redirect [CSRF]
        and (b) recover the PKCE verifier + expected nonce."""
        state = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO oidc_states (state_hash, nonce, code_verifier, created_at, expires_at) "
                "VALUES (?,?,?,?,?)",
                (_hash_key(state), nonce, code_verifier, now, now + ttl_seconds),
            )
            self._conn.commit()
        return state

    def consume_oidc_state(self, state: str) -> Optional[tuple[str, str]]:
        """Single-use lookup: returns ``(nonce, code_verifier)`` and deletes
        the row, or ``None`` for an unknown/expired state. Expired rows swept
        lazily on read, same pattern as sessions/email tokens."""
        if not state:
            return None
        now = time.time()
        with self._lock:
            self._conn.execute("DELETE FROM oidc_states WHERE expires_at < ?", (now,))
            row = self._conn.execute(
                "SELECT nonce, code_verifier FROM oidc_states WHERE state_hash = ? AND expires_at >= ?",
                (_hash_key(state), now),
            ).fetchone()
            if row is None:
                self._conn.commit()
                return None
            self._conn.execute("DELETE FROM oidc_states WHERE state_hash = ?", (_hash_key(state),))
            self._conn.commit()
        return row["nonce"], row["code_verifier"]

    def get_or_create_sso_user(self, email: str, name: str = "") -> tuple[User, Account]:
        """Find-or-create a user from a verified SSO identity (Microsoft). An
        existing account (whether it was created by password signup or a
        prior SSO login) is **linked by email** and returned; a new one gets
        the same tenant/account/API-key setup as a password signup, but with
        a random unusable password (SSO users don't have one — they can set
        one later via password reset if they ever want it) and
        ``email_verified`` already true, since the identity provider verified
        it."""
        email = email.strip().lower()
        existing = self.get_user_by_email(email)
        if existing:
            if not existing.email_verified:
                self.mark_email_verified(existing.id)
            acct = self.account_for_user(existing.id)
            return self.get_user(existing.id), acct
        # A password the user never learns — password login stays closed for
        # an SSO-only account until/unless they run a reset.
        user, acct, _key = self.create_user(email, secrets.token_urlsafe(32), name=name)
        self.mark_email_verified(user.id)
        return self.get_user(user.id), acct

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
                "SELECT id, tenant, name, key_hash, plan, created_at FROM accounts"
            ).fetchall()]
            usage = [dict(r) for r in self._conn.execute(
                "SELECT tenant, day, count FROM usage"
            ).fetchall()]
            # api_keys is the authoritative key store (Day 24) — must be in the
            # snapshot or a restored account couldn't authenticate at all.
            api_keys = [dict(r) for r in self._conn.execute(
                "SELECT id, account_id, key_hash, name, created_at FROM api_keys"
            ).fetchall()]
        return {"version": 2, "accounts": accounts, "usage": usage, "api_keys": api_keys}

    def restore(self, snapshot: dict) -> None:
        """Restore rows from :meth:`dump`'s output into an **empty** store
        (a fresh database, or one about to be reset for a restore drill) —
        upserts by primary key so it is safe to call again with the same
        snapshot. Does not delete rows absent from the snapshot; call this
        against a clean database for a true point-in-time restore."""
        with self._lock:
            for a in snapshot.get("accounts", []):
                self._conn.execute(
                    "INSERT INTO accounts (id, tenant, name, key_hash, plan, created_at) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "tenant=excluded.tenant, name=excluded.name, key_hash=excluded.key_hash, "
                    "plan=excluded.plan, created_at=excluded.created_at",
                    (a["id"], a["tenant"], a["name"], a["key_hash"], a["plan"], a["created_at"]),
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
            self._conn.commit()
