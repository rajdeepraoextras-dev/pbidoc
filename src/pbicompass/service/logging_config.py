"""Structured, content-free JSON logging with request/job-id correlation (Day 19, §9).

Every log line becomes one JSON object — timestamp, level, logger name,
message, plus ``request_id``/``job_id`` (``"-"`` when not applicable) so every
line from a single HTTP request or a single job can be grepped/filtered
together across a multi-worker deployment.

Deliberately **excludes** the raw exception message and traceback text — only
the exception's *type name* is recorded. This mirrors the ``type(exc).__name__``
-only pattern already used everywhere else in this service (e.g.
``worker.py::_make_client``) and this project's standing "content-free
message only" convention (see ``Job.error``'s own docstring in ``jobs.py``):
an uncontrolled exception string could, in principle, echo a fragment of
parsed report data (a bad dict key, a malformed value in an f-string, ...),
so it's never serialized, even to a local log file.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("pbicompass_request_id", default="-")
job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("pbicompass_job_id", default="-")


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.job_id = job_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "job_id": getattr(record, "job_id", "-"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None, stream=None) -> logging.Logger:
    """Idempotent: safe to call from ``create_app()`` and from tests without
    accumulating duplicate handlers on repeated calls (e.g. one per test)."""
    root = logging.getLogger("pbicompass")
    root.setLevel((level or os.environ.get("PBICOMPASS_LOG_LEVEL", "INFO")).upper())
    root.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)
    root.propagate = False
    return root
