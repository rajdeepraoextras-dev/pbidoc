"""Celery wiring for the async-worker option (Day 18, §9/§12).

``process_job`` (``service/worker.py``) has been queue-agnostic since it was
written: FastAPI's ``BackgroundTasks`` calls it directly in-process, and
``process_job_task`` below — a thin Celery task wrapper — calls the exact
same function identically from a separate worker process. Nothing about the
job-processing logic, the zero-retention contract, or the watchdog changes;
only *which process* runs it does.

This module is only imported when ``PBICOMPASS_QUEUE=celery`` is actually
selected (see ``app.py::create_job``) or when a real ``celery worker`` process
loads it via ``-A pbicompass.service.celery_app`` — the default inline path
never imports ``celery``, so a plain ``pip install pbicompass[service]``
install stays exactly as dependency-light as before this day's work.

A Celery task's arguments cross a message broker as data (JSON here), not
live Python objects — so unlike the in-process call, this task reconstructs
its own ``JobStore``/``JobSandbox`` handles from plain paths rather than
receiving the objects directly. That means the API process and every worker
process must share the same job-store file/URL and the same sandbox
directory (a mounted volume, or same-host processes) — documented in
``DEPLOYMENT.md``, not silently assumed.
"""

from __future__ import annotations

import os
from pathlib import Path

from celery import Celery

from .jobs import DEFAULT_JOB_TIMEOUT_SECONDS, JobStore
from .output_store import output_store_from_env
from .sandbox import JobSandbox
from .worker import process_job


def _broker_url() -> str:
    return os.environ.get("PBICOMPASS_BROKER_URL", "redis://localhost:6379/0")


def _result_backend() -> str:
    return os.environ.get("PBICOMPASS_RESULT_BACKEND", _broker_url())


def _job_timeout_seconds() -> int:
    # Mirrors app.py's own helper (not imported from there, so a
    # worker-only deployment never needs FastAPI installed just to run
    # ``celery -A pbicompass.service.celery_app worker``). The default
    # itself comes from ``jobs.py`` — which this module already imports —
    # so the API and the worker cannot drift apart on it.
    return int(os.environ.get("PBICOMPASS_JOB_TIMEOUT_SECONDS",
                              str(DEFAULT_JOB_TIMEOUT_SECONDS)))


# Module-level so both ``app.py`` (enqueuing) and the ``celery worker`` CLI
# (``-A pbicompass.service.celery_app``, consuming) import the identical app.
celery_app = Celery("pbicompass", broker=_broker_url(), backend=_result_backend())
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    # This app never inspects Celery's own result store (job status/output
    # lives in JobStore, polled by the client); dropping results keeps the
    # backend from accumulating content-free-but-still-unbounded bookkeeping.
    task_ignore_result=True,
    # Without these, a wedged job (a provider socket that never returns, a
    # pathological render) occupies a worker slot forever: JobStore's own
    # watchdog only rewrites the job *row*, it cannot reclaim the process
    # running it.
    #
    # Three escalating layers, deliberately ordered:
    #   t+0     JobStore.sweep force-fails the row with the actionable
    #           "Generation timed out..." message, and the worker stops at
    #           its next ``active()`` checkpoint.
    #   t+60    soft limit: raises inside the task for a worker that never
    #           reached a checkpoint (a checkpoint only sits *between*
    #           stages, and one LLM call can legitimately run for minutes).
    #           ``process_job``'s ``finally`` still shreds the sandbox, so
    #           the zero-retention contract holds.
    #   t+120   hard limit: kill the process if even that hung.
    #
    # The soft limit trails the store's deadline rather than matching it so
    # the user always sees the store's specific message, never the generic
    # "generation failed" that process_job's catch-all would record for a
    # SoftTimeLimitExceeded. worker.py stays queue-agnostic (it must never
    # import celery), so the ordering — not an exception handler — is what
    # keeps the two consistent.
    task_soft_time_limit=_job_timeout_seconds() + 60,
    task_time_limit=_job_timeout_seconds() + 120,
)


@celery_app.task(name="pbicompass.process_job")
def process_job_task(job_id: str, upload_path: str, sandbox_dir: str,
                      jobs_db_path: str, options: dict) -> None:
    store = JobStore(
        jobs_db_path,
        processing_timeout_seconds=_job_timeout_seconds(),
        output_store=output_store_from_env(),
    )
    try:
        sandbox = JobSandbox.at(sandbox_dir)
        process_job(store, job_id, Path(upload_path), sandbox, options)
    finally:
        store.close()
