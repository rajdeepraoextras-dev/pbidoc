"""Job registry — metadata in SQLite/Postgres, rendered bytes via a backend.

A ``Job`` record holds **status and timestamps only** — never uploaded content
or extracted metadata. It is the one piece of state that persists, so a job
started before a restart is still visible afterwards (A2-1) and the account
dashboard can show a durable job history. It goes to whatever
``PBICOMPASS_JOBS_DB`` points at: a sqlite path (self-host default) or a
``postgres://`` URL (managed Postgres, e.g. Supabase) via the shared
:class:`~pbicompass.service.db._Connection` wrapper — the same one
``AccountStore`` uses. No sqlite file is required in a hosted deployment.

Rendered documents (the user-owned output) are held behind an output backend:
process memory by default, or an external private store such as Supabase
Storage for hosted Cloud Run deployments. The database persists only
content-free job metadata and an output expiry timestamp.

With an external output store, completed downloads survive restarts and are no
longer tied to one container instance.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .db import _Connection
from .metrics import MetricsRegistry
from .output_store import MemoryOutputStore, OutputStore

_TIMEOUT_MESSAGE = (
    "Generation timed out. Try a smaller file, a lower AI effort level, "
    "or the offline engine."
)


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Feedback:
    id: str
    job_id: str
    tenant: str
    message: str
    created_at: float


@dataclass
class Job:
    id: str
    filename: str
    created_at: float
    tenant: str = "public"  # owning tenant (multi-tenancy isolation)
    status: JobStatus = JobStatus.QUEUED
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None  # content-free message only
    formats: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # e.g. LLM engine fell back to offline
    # Content-free AI spend telemetry (Phase 0): {agent_name: {calls, input_tokens, output_tokens}}.
    usage: dict = field(default_factory=dict)


class JobStore:
    def __init__(self, db_path: str = ":memory:", ttl_seconds: int = 3600,
                 processing_timeout_seconds: int = 600,
                 metrics: MetricsRegistry | None = None,
                 output_store: OutputStore | None = None) -> None:
        # Recorded (not just consumed) so a separate process — a Celery
        # worker (Day 18) — can open its own connection to the exact same
        # database the API process is using, since a Python object can't
        # cross the broker. A postgres:// URL routes to managed Postgres via
        # _Connection (Day 34); anything else is a stdlib sqlite path.
        self.db_path = db_path
        self.ttl = ttl_seconds
        # Watchdog ceiling: a job stuck in PROCESSING longer than this is
        # force-failed by ``sweep`` (called on every status poll). Without
        # this, a hung render/LLM call would leave the job "processing"
        # forever with nothing to ever mark it done or failed.
        self.processing_timeout = processing_timeout_seconds
        # Optional (Day 20): counts jobs created/done/failed for the
        # /metrics endpoint. None by default so every pre-existing
        # ``JobStore()`` test call site is unaffected — this only tracks
        # counts and integer token numbers, never job content.
        self.metrics = metrics
        # One shared metadata connection guarded by a lock: works for both
        # file/in-memory sqlite and a Postgres connection across FastAPI's
        # threadpool (same pattern as ``AccountStore``).
        self._conn = _Connection(db_path)
        self._lock = threading.Lock()
        # Rendered output bytes live behind this backend. The default is the
        # original process-local memory cache; hosted deployments can inject a
        # shared object store so downloads survive restarts and scale-out.
        self._output_store = output_store or MemoryOutputStore()
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            # DOUBLE PRECISION (not REAL): a unix timestamp needs float8 to
            # stay precise on Postgres — REAL is float4 there and would round
            # a ~1.7e9 timestamp by minutes, breaking the TTL/watchdog math.
            # sqlite treats DOUBLE PRECISION as REAL affinity (8-byte float),
            # identical to before. Only job metadata is stored — no outputs
            # table exists anymore (rendered bytes never touch the DB).
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    tenant TEXT NOT NULL DEFAULT 'public',
                    status TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    started_at DOUBLE PRECISION,
                    finished_at DOUBLE PRECISION,
                    error TEXT,
                    formats TEXT NOT NULL DEFAULT '[]',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    usage TEXT NOT NULL DEFAULT '{}',
                    output_expires_at DOUBLE PRECISION
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    tenant TEXT NOT NULL DEFAULT 'public',
                    message TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                );
                """
            )
            self._ensure_output_expires_column()
            self._conn.commit()

    def _ensure_output_expires_column(self) -> None:
        if self._conn.column_exists("jobs", "output_expires_at"):
            return
        try:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN output_expires_at DOUBLE PRECISION")
        except Exception as exc:
            self._conn.rollback()
            message = str(exc).lower()
            if "duplicate column" not in message and "already exists" not in message:
                raise

    # -- lifecycle ----------------------------------------------------------
    def create(self, filename: str, tenant: str = "public") -> Job:
        job = Job(id=uuid.uuid4().hex, filename=filename, created_at=time.time(), tenant=tenant)
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, filename, tenant, status, created_at, formats, warnings, usage) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (job.id, job.filename, job.tenant, job.status.value, job.created_at,
                 "[]", "[]", "{}"),
            )
            self._conn.commit()
        if self.metrics:
            self.metrics.record_job_created()
        return job

    def mark_processing(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
                (JobStatus.PROCESSING.value, time.time(), job_id),
            )
            self._conn.commit()

    def mark_done(self, job_id: str, formats: list[str], warnings: list[str] | None = None,
                  usage: dict | None = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT warnings, usage FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            warnings_json = json.dumps(warnings) if warnings else row["warnings"]
            usage_json = json.dumps(usage) if usage else row["usage"]
            self._conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, formats = ?, warnings = ?, usage = ? "
                "WHERE id = ?",
                (JobStatus.DONE.value, time.time(), json.dumps(formats), warnings_json,
                 usage_json, job_id),
            )
            self._conn.commit()
        if self.metrics:
            self.metrics.record_job_done(usage)

    def mark_failed(self, job_id: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (JobStatus.FAILED.value, time.time(), message, job_id),
            )
            self._conn.commit()
        if self.metrics:
            self.metrics.record_job_failed()

    def store_outputs(self, job_id: str, data: dict[str, bytes]) -> None:
        """Store rendered bytes until ``ttl`` elapses."""
        expires = time.time() + self.ttl
        self._output_store.put_many(job_id, data, expires)
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET output_expires_at = ? WHERE id = ?",
                (expires, job_id),
            )
            self._conn.commit()

    def healthcheck(self, timeout: float = 3.0) -> bool:
        """Bounded health probe: try the store lock with a timeout, then one
        trivial query. Returns ``False`` instead of hanging when the lock is
        wedged behind a stuck DB call — /healthz must *report* a wedge, not
        join the queue behind it (2026-07-13: production hung exactly that
        way and healthz hung with it, invisible to any watchdog)."""
        if not self._lock.acquire(timeout=timeout):
            return False
        try:
            return self._conn.ping()
        except Exception:
            return False
        finally:
            self._lock.release()

    # -- access -------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        self.sweep()
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def get_output(self, job_id: str, fmt: str) -> Optional[bytes]:
        self.sweep()
        return self._output_store.get(job_id, fmt)

    def list_for_tenant(self, tenant: str, limit: int = 50) -> list[Job]:
        """A tenant's recent jobs, newest first — for the account dashboard's
        job history (Day 24). Status/timestamps only; the ``Job`` record has
        never held report content, so this stays zero-retention by
        construction."""
        self.sweep()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE tenant = ? ORDER BY created_at DESC LIMIT ?",
                (tenant, limit),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_all(self, limit: int = 200, tenant: str | None = None) -> list[Job]:
        """Every tenant's recent jobs, newest first — for the admin panel's
        cross-tenant job browser (Day 38). Optional ``tenant`` filter narrows
        it to one tenant. Still status/timestamps only, zero-retention."""
        self.sweep()
        with self._lock:
            if tenant:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE tenant = ? ORDER BY created_at DESC LIMIT ?",
                    (tenant, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row_to_job(r) for r in rows]

    # -- feedback -------------------------------------------------------------
    def add_feedback(self, job_id: str, tenant: str, message: str) -> Feedback:
        """A short free-text note a user left after a job finished — kept
        indefinitely (unlike output bytes) since it's small, user-authored
        text meant for the team to read later, not report content."""
        fb = Feedback(id=uuid.uuid4().hex, job_id=job_id, tenant=tenant,
                       message=message, created_at=time.time())
        with self._lock:
            self._conn.execute(
                "INSERT INTO feedback (id, job_id, tenant, message, created_at) VALUES (?,?,?,?,?)",
                (fb.id, fb.job_id, fb.tenant, fb.message, fb.created_at),
            )
            self._conn.commit()
        return fb

    def list_feedback(self, limit: int = 200) -> list[Feedback]:
        """Every tenant's feedback, newest first — for the admin panel's
        feedback tab (mirrors :meth:`list_all`'s cross-tenant job browser)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            Feedback(id=r["id"], job_id=r["job_id"], tenant=r["tenant"],
                     message=r["message"], created_at=r["created_at"])
            for r in rows
        ]

    @staticmethod
    def public_feedback(fb: Feedback) -> dict:
        return {
            "id": fb.id, "job_id": fb.job_id, "tenant": fb.tenant,
            "message": fb.message, "created_at": fb.created_at,
        }

    def sweep(self) -> None:
        """Force-fail stuck jobs and drop expired output bytes.

        Job metadata is kept as durable history; only the downloadable bytes
        expire from whichever output backend is configured.
        """
        now = time.time()
        expired_outputs: list[tuple[str, list[str]]] = []
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? "
                "WHERE status = ? AND started_at IS NOT NULL AND (? - started_at) > ?",
                (JobStatus.FAILED.value, now, _TIMEOUT_MESSAGE,
                 JobStatus.PROCESSING.value, now, self.processing_timeout),
            )
            timed_out = cur.rowcount
            self._conn.commit()
            rows = self._conn.execute(
                "SELECT id, formats FROM jobs WHERE output_expires_at IS NOT NULL "
                "AND output_expires_at < ?",
                (now,),
            ).fetchall()
            expired_outputs = [(r["id"], json.loads(r["formats"])) for r in rows]
        for jid, formats in expired_outputs:
            self._output_store.delete_job(jid, formats)
        if expired_outputs:
            with self._lock:
                for jid, _formats in expired_outputs:
                    self._conn.execute(
                        "UPDATE jobs SET output_expires_at = NULL WHERE id = ?",
                        (jid,),
                    )
                self._conn.commit()
        if self.metrics and timed_out > 0:
            for _ in range(timed_out):
                self.metrics.record_job_failed()

    def public(self, job: Job) -> dict:
        """JSON-safe status payload (no document content)."""
        payload = {
            "job_id": job.id,
            "filename": job.filename,
            "status": job.status.value,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "error": job.error,
            "formats": job.formats,
            "warnings": job.warnings,
            "usage": job.usage,
        }
        if job.status is JobStatus.DONE:
            payload["downloads"] = {
                fmt: f"/jobs/{job.id}/download?format={fmt}" for fmt in job.formats
            }
        return payload

    @staticmethod
    def _row_to_job(row) -> Job:
        return Job(
            id=row["id"],
            filename=row["filename"],
            created_at=row["created_at"],
            tenant=row["tenant"],
            status=JobStatus(row["status"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            formats=json.loads(row["formats"]),
            warnings=json.loads(row["warnings"]),
            usage=json.loads(row["usage"]),
        )
