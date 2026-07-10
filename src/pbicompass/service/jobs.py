"""Job registry with TTL expiry — backed by stdlib ``sqlite3``.

A ``Job`` record holds **status and timestamps only** — never uploaded content
or extracted metadata. The rendered documents (the user-owned output) are
stored as BLOBs in the same database and expire after ``ttl_seconds``. This is
the only state that survives a request, and only briefly.

Defaults to an in-memory database (``:memory:``, matching ``AccountStore``'s
convention) so tests and ad hoc runs behave exactly as the previous pure-Python
implementation did. Point ``db_path`` at a file (or a mounted volume, via
``PBICOMPASS_JOBS_DB``) so a job started before a restart/redeploy is still
visible to the poller afterwards, instead of silently 404ing (A2-1).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .metrics import MetricsRegistry


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


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
                 metrics: MetricsRegistry | None = None) -> None:
        # Recorded (not just consumed) so a separate process — a Celery
        # worker (Day 18) — can open its own connection to the exact same
        # database the API process is using, since a Python object can't
        # cross the broker.
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
        # One shared connection guarded by a lock: works for both file and
        # in-memory DBs across FastAPI's threadpool (same pattern as
        # ``AccountStore``).
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
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    tenant TEXT NOT NULL DEFAULT 'public',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    error TEXT,
                    formats TEXT NOT NULL DEFAULT '[]',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    usage TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS outputs (
                    job_id TEXT NOT NULL,
                    format TEXT NOT NULL,
                    data BLOB NOT NULL,
                    expires REAL NOT NULL,
                    PRIMARY KEY (job_id, format)
                );
                """
            )
            self._conn.commit()

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
        expires = time.time() + self.ttl
        with self._lock:
            self._conn.execute("DELETE FROM outputs WHERE job_id = ?", (job_id,))
            self._conn.executemany(
                "INSERT INTO outputs (job_id, format, data, expires) VALUES (?,?,?,?)",
                [(job_id, fmt, blob, expires) for fmt, blob in data.items()],
            )
            self._conn.commit()

    # -- access -------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        self.sweep()
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def get_output(self, job_id: str, fmt: str) -> Optional[bytes]:
        self.sweep()
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM outputs WHERE job_id = ? AND format = ?", (job_id, fmt)
            ).fetchone()
        return row["data"] if row else None

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

    def sweep(self) -> None:
        """Drop expired outputs and the job records that go with them; force-fail
        any job stuck in PROCESSING past the watchdog timeout."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? "
                "WHERE status = ? AND started_at IS NOT NULL AND (? - started_at) > ?",
                (JobStatus.FAILED.value, now,
                 "Generation timed out. Try a smaller file, a lower AI "
                 "effort level, or the offline engine.",
                 JobStatus.PROCESSING.value, now, self.processing_timeout),
            )
            timed_out = cur.rowcount
            self._conn.execute("DELETE FROM outputs WHERE expires < ?", (now,))
            # also expire terminal jobs past their TTL even if outputs were never stored
            self._conn.execute(
                "DELETE FROM jobs WHERE finished_at IS NOT NULL AND (? - finished_at) > ? "
                "AND id NOT IN (SELECT job_id FROM outputs)",
                (now, self.ttl),
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
    def _row_to_job(row: sqlite3.Row) -> Job:
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
