"""In-memory job registry with TTL expiry.

A ``Job`` record holds **status and timestamps only** — never uploaded content
or extracted metadata. The rendered documents (the user-owned output) are stored
separately and expire after ``ttl_seconds``. This is the only state that
survives a request, and only briefly.

The store is intentionally simple/in-process; the same interface can be backed
by Redis/Postgres when moving the worker to Celery.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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


class JobStore:
    def __init__(self, ttl_seconds: int = 3600, processing_timeout_seconds: int = 600) -> None:
        self.ttl = ttl_seconds
        # Watchdog ceiling: a job stuck in PROCESSING longer than this is
        # force-failed by ``sweep`` (called on every status poll). Without
        # this, a hung render/LLM call would leave the job "processing"
        # forever with nothing to ever mark it done or failed.
        self.processing_timeout = processing_timeout_seconds
        self._jobs: dict[str, Job] = {}
        self._outputs: dict[str, dict] = {}  # id -> {"data": {fmt: bytes}, "expires": float}
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------
    def create(self, filename: str, tenant: str = "public") -> Job:
        job = Job(id=uuid.uuid4().hex, filename=filename, created_at=time.time(), tenant=tenant)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def mark_processing(self, job_id: str) -> None:
        with self._lock:
            if job := self._jobs.get(job_id):
                job.status = JobStatus.PROCESSING
                job.started_at = time.time()

    def mark_done(self, job_id: str, formats: list[str], warnings: list[str] | None = None) -> None:
        with self._lock:
            if job := self._jobs.get(job_id):
                job.status = JobStatus.DONE
                job.finished_at = time.time()
                job.formats = formats
                if warnings:
                    job.warnings = warnings

    def mark_failed(self, job_id: str, message: str) -> None:
        with self._lock:
            if job := self._jobs.get(job_id):
                job.status = JobStatus.FAILED
                job.finished_at = time.time()
                job.error = message

    def store_outputs(self, job_id: str, data: dict[str, bytes]) -> None:
        with self._lock:
            self._outputs[job_id] = {"data": data, "expires": time.time() + self.ttl}

    # -- access -------------------------------------------------------------
    def get(self, job_id: str) -> Optional[Job]:
        self.sweep()
        with self._lock:
            return self._jobs.get(job_id)

    def get_output(self, job_id: str, fmt: str) -> Optional[bytes]:
        self.sweep()
        with self._lock:
            entry = self._outputs.get(job_id)
            return entry["data"].get(fmt) if entry else None

    def sweep(self) -> None:
        """Drop expired outputs and the job records that go with them; force-fail
        any job stuck in PROCESSING past the watchdog timeout."""
        now = time.time()
        with self._lock:
            for job in self._jobs.values():
                if (job.status is JobStatus.PROCESSING and job.started_at is not None
                        and (now - job.started_at) > self.processing_timeout):
                    job.status = JobStatus.FAILED
                    job.finished_at = now
                    job.error = (
                        "Generation timed out. Try a smaller file, a lower AI "
                        "effort level, or the offline engine."
                    )
            expired = [jid for jid, e in self._outputs.items() if e["expires"] < now]
            for jid in expired:
                self._outputs.pop(jid, None)
            # also expire terminal jobs past their TTL even if outputs were never stored
            stale = [
                jid for jid, j in self._jobs.items()
                if j.finished_at and (now - j.finished_at) > self.ttl and jid not in self._outputs
            ]
            for jid in stale:
                self._jobs.pop(jid, None)

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
        }
        if job.status is JobStatus.DONE:
            payload["downloads"] = {
                fmt: f"/jobs/{job.id}/download?format={fmt}" for fmt in job.formats
            }
        return payload
