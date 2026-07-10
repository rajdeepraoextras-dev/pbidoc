"""Day 19: real readiness /healthz — checks the job store, the accounts
store (when configured), and the queue broker (only in celery mode), instead
of the previous unconditional ``{"ok": True}``.
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

from pbicompass.service.accounts import AccountStore
from pbicompass.service.jobs import JobStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

try:
    import celery  # noqa: F401
    _HAVE_CELERY = True
except Exception:  # pragma: no cover
    _HAVE_CELERY = False


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class HealthzHappyPathTest(unittest.TestCase):
    def test_healthy_response_shape(self):
        client = TestClient(create_app(JobStore()))
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body, {"ok": True, "checks": {"jobs_db": True, "queue": True}})

    def test_accounts_db_check_included_when_configured(self):
        accounts = AccountStore(":memory:")
        self.addCleanup(accounts.close)
        client = TestClient(create_app(JobStore(), account_store=accounts, require_auth=True))
        body = client.get("/healthz").json()
        self.assertIn("accounts_db", body["checks"])
        self.assertTrue(body["checks"]["accounts_db"])


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class HealthzUnhealthyTest(unittest.TestCase):
    def test_jobs_db_failure_returns_503(self):
        store = JobStore()
        store.close()  # simulate a broken/unreachable DB connection
        client = TestClient(create_app(store))
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertFalse(body["checks"]["jobs_db"])

    def test_accounts_db_failure_returns_503(self):
        accounts = AccountStore(":memory:")
        accounts.close()
        client = TestClient(create_app(JobStore(), account_store=accounts, require_auth=True))
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["accounts_db"])


@unittest.skipUnless(_HAVE_SERVICE and _HAVE_CELERY, "service/celery extras not installed")
class HealthzQueueCheckTest(unittest.TestCase):
    def test_queue_check_is_bounded_even_when_the_broker_probe_hangs(self):
        # Simulate the exact real-world observation this check was built to
        # survive: a connection attempt that doesn't respect its own
        # timeout and hangs well past what /healthz can afford to wait.
        from pbicompass.service.celery_app import celery_app

        class _HangingConnection:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def ensure_connection(self, *args, **kwargs):
                time.sleep(5)

        with mock.patch.object(celery_app, "connection", return_value=_HangingConnection()):
            with mock.patch.dict(os.environ, {"PBICOMPASS_QUEUE": "celery"}):
                client = TestClient(create_app(JobStore()))
                t0 = time.time()
                resp = client.get("/healthz")
                elapsed = time.time() - t0

        self.assertLess(elapsed, 3.0, "healthz must not block on a hung broker probe")
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json()["checks"]["queue"])

    def test_queue_check_true_in_inline_mode_regardless_of_broker(self):
        prior = os.environ.pop("PBICOMPASS_QUEUE", None)
        try:
            client = TestClient(create_app(JobStore()))
            self.assertTrue(client.get("/healthz").json()["checks"]["queue"])
        finally:
            if prior is not None:
                os.environ["PBICOMPASS_QUEUE"] = prior


if __name__ == "__main__":
    unittest.main(verbosity=2)
