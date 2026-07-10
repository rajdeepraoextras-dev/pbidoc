"""Phase 4 — asynchronous web service with a strict zero-retention policy.

A FastAPI app (``app.create_app``) accepts an upload, runs the parse → agents →
render pipeline inside a per-job :class:`~pbicompass.service.sandbox.JobSandbox`
(RAM/temp dir, shredded in a ``finally`` block), and serves the rendered
documents for a short TTL. No uploaded file or extracted metadata is persisted.

The worker (:func:`~pbicompass.service.worker.process_job`) is queue-agnostic — it
runs under FastAPI ``BackgroundTasks`` today and can be moved to Celery later
with no change to its signature.
"""

from .accounts import Account, AccountStore, ApiKeyInfo, PLAN_LIMITS
from .jobs import Job, JobStatus, JobStore
from .sandbox import JobSandbox
from .worker import process_job

__all__ = ["Job", "JobStatus", "JobStore", "JobSandbox", "process_job", "create_app",
           "Account", "AccountStore", "PLAN_LIMITS", "ApiKeyInfo"]


def create_app(*args, **kwargs):  # lazy so importing the package doesn't need FastAPI
    from .app import create_app as _create_app
    return _create_app(*args, **kwargs)
