"""Per-key sliding-window request rate limiter (Day 20, §9).

Deliberately separate from ``AdminGuard`` (``admin.py``): that class limits
*failed* auth attempts (brute-force lockout after wrong tokens). This one
limits *every* request from a key regardless of success — the right shape
for "don't let one IP hammer the upload endpoint" abuse protection, which
applies even to the unauthenticated ``public`` tenant that the per-plan daily
quota (``AccountStore.try_consume``) never sees.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float,
                 now: Callable[[], float] = time.time) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._now = now
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Record and permit a request for ``key``, or refuse it (``False``)
        if it would exceed ``max_requests`` within the trailing window."""
        now = self._now()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t >= cutoff]
            if len(hits) >= self.max_requests:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True
