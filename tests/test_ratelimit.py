"""Day 20: per-IP upload rate limiting (§9).

The pure ``RateLimiter`` unit tests always run (stdlib only, injectable
clock). The ``POST /jobs`` wiring test needs the service extras and skips
cleanly without them.
"""

from __future__ import annotations

import unittest

from pbicompass.service.ratelimit import RateLimiter

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RateLimiterTest(unittest.TestCase):
    def test_allows_up_to_the_limit_then_blocks(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertTrue(limiter.allow("1.2.3.4"))
        self.assertFalse(limiter.allow("1.2.3.4"))  # 4th request in the window

    def test_keys_are_independent(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))  # a different key has its own budget

    def test_old_hits_age_out_of_the_window(self):
        clock = _FakeClock()
        limiter = RateLimiter(max_requests=1, window_seconds=60, now=clock)
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))
        clock.advance(61)
        self.assertTrue(limiter.allow("k"))  # the first hit has aged out


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class UploadRateLimitWiringTest(unittest.TestCase):
    def test_exceeding_the_per_ip_limit_returns_429(self):
        import io
        import os
        import zipfile
        from pathlib import Path
        from unittest import mock

        fixture_dir = Path(__file__).parent / "fixtures" / "SampleSales"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in fixture_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(fixture_dir.parent))
        content = buf.getvalue()

        with mock.patch.dict(os.environ, {
            "PBICOMPASS_UPLOAD_RATE_LIMIT": "2",
            "PBICOMPASS_UPLOAD_RATE_WINDOW_SECONDS": "60",
        }):
            client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
            up = lambda: client.post(
                "/jobs", files={"file": ("s.zip", content, "application/zip")},
                data={"provider": "none"},
            )
            self.assertEqual(up().status_code, 200)
            self.assertEqual(up().status_code, 200)
            self.assertEqual(up().status_code, 429)  # 3rd request from the same test client "IP"

        snap = client.get("/metrics", headers={"X-Admin-Token": "t"}).json()
        self.assertEqual(snap["rate_limited_total"], 1)

    def test_default_limit_does_not_interfere_with_normal_use(self):
        client = TestClient(create_app(JobStore(), require_auth=False))
        res = client.post("/jobs", files={"file": ("bad.txt", b"x", "text/plain")})
        # rejected for an unsupported file type, not rate-limited -- proves the
        # default limit is generous enough not to trip on a single request.
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
