"""Sentry error tracking — off unless ``SENTRY_DSN`` is set (Day 19, §9).

Content-free by construction, matching this project's zero-leakage
guarantee:

- ``send_default_pii=False`` — never attach user/request identifying data.
- ``include_local_variables=False`` — never attach stack-frame local
  variables (which could hold parsed report content mid-pipeline).
- ``include_source_context=False`` — never attach the literal source lines
  surrounding a frame either; a value embedded directly in an f-string on the
  same source line would otherwise be captured as source text even with
  local variables off (found while writing this module's own test — the
  test's own hardcoded "secret" string was captured this way until this
  flag was added, which is exactly the class of leak this guards against).
- ``before_send`` scrubs the exception's own message text down to just its
  *type name* (the same ``type(exc).__name__``-only convention used
  everywhere else in this service) and drops any captured request data,
  since an exception string or a request body could, in principle, echo a
  fragment of the uploaded model.

``sentry_sdk`` is imported lazily so a deploy that never sets ``SENTRY_DSN``
(the default) needs no new dependency at all.
"""

from __future__ import annotations

import os
from typing import Any, Optional


def _scrub_event(event: dict, hint: dict) -> Optional[dict]:
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type = exc_info[0]
        type_name = exc_type.__name__ if exc_type is not None else "Exception"
        for entry in event.get("exception", {}).get("values", []):
            entry["value"] = type_name
    event.pop("request", None)
    return event


def init_sentry(dsn: str | None = None, transport: Any = None) -> bool:
    """Initialize Sentry if a DSN is configured and the SDK is installed.

    Returns ``True`` iff Sentry was actually initialized — callers can log
    this without leaking whether/why it wasn't (missing DSN and missing SDK
    both just mean "off").
    """
    dsn = dsn if dsn is not None else os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False
    kwargs: dict = dict(
        dsn=dsn,
        send_default_pii=False,
        include_local_variables=False,
        include_source_context=False,
        before_send=_scrub_event,
        traces_sample_rate=0.0,
        environment=os.environ.get("PBICOMPASS_ENV", "production"),
    )
    if transport is not None:
        kwargs["transport"] = transport
    sentry_sdk.init(**kwargs)
    return True
