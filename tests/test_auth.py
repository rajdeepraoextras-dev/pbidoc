"""Phase 5 tests: accounts, API-key auth, tenant isolation, and freemium quotas.

The ``AccountStore`` tests are pure stdlib (sqlite3) and always run. The API
tests need the service extras and skip cleanly without them.
"""

from __future__ import annotations

import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pbicompass.service.accounts import AccountStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "SampleSales"


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in FIXTURE_DIR.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(FIXTURE_DIR.parent))
    return buf.getvalue()


def _h(key: str) -> dict:
    return {"Authorization": "Bearer " + key}


class AccountStoreTest(unittest.TestCase):
    def test_create_and_verify(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        acct, key = store.create_account("acme", name="Acme BI", plan="pro")
        self.assertTrue(key.startswith("pbicompass_sk_"))
        self.assertEqual(store.verify(key).tenant, "acme")
        self.assertEqual(store.verify(key).plan, "pro")
        self.assertIsNone(store.verify("pbicompass_sk_wrong"))
        self.assertIsNone(store.verify(None))

    def test_unknown_plan_rejected(self):
        store = AccountStore(":memory:")
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            store.create_account("x", plan="ultra")

    def test_quota_increments_and_blocks(self):
        with mock.patch.dict("pbicompass.service.accounts.PLAN_LIMITS",
                             {"free": 2, "pro": 200, "enterprise": 100000}, clear=True):
            store = AccountStore(":memory:")
            self.addCleanup(store.close)
            store.create_account("t", plan="free")
            self.assertEqual(store.try_consume("t", "free"), (True, 1, 2))
            self.assertEqual(store.try_consume("t", "free"), (True, 2, 2))
            self.assertEqual(store.try_consume("t", "free"), (False, 2, 2))  # blocked
            self.assertEqual(store.usage_today("t"), 2)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AuthApiTest(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.mkdtemp(prefix="pbicompass_authsb_")
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        _, self.key_a = self.accounts.create_account("tenant-a", plan="enterprise")
        _, self.key_b = self.accounts.create_account("tenant-b", plan="enterprise")
        self.client = TestClient(create_app(
            JobStore(), sandbox_root=self._root,
            account_store=self.accounts, require_auth=True,
        ))

    def _upload(self, key):
        return self.client.post(
            "/jobs",
            files={"file": ("SampleSales.zip", _zip(), "application/zip")},
            data={"provider": "none"},
            headers=_h(key),
        )

    def _wait(self, job_id, key):
        for _ in range(120):
            j = self.client.get(f"/jobs/{job_id}", headers=_h(key)).json()
            if j["status"] in ("done", "failed"):
                return j
            time.sleep(0.05)
        self.fail("job did not finish")

    def test_requires_valid_key(self):
        self.assertEqual(self.client.post("/jobs", files={"file": ("a.zip", b"x", "application/zip")}).status_code, 401)
        bad = self.client.post("/jobs", files={"file": ("a.zip", b"x", "application/zip")},
                               headers=_h("pbicompass_sk_nope"))
        self.assertEqual(bad.status_code, 401)

    def test_me_reports_plan_and_quota(self):
        me = self.client.get("/me", headers=_h(self.key_a)).json()
        self.assertEqual(me["tenant"], "tenant-a")
        self.assertEqual(me["plan"], "enterprise")
        self.assertIn("remaining", me)

    def test_authenticated_flow(self):
        job_id = self._upload(self.key_a).json()["job_id"]
        self.assertEqual(self._wait(job_id, self.key_a)["status"], "done")
        md = self.client.get(f"/jobs/{job_id}/download", params={"format": "md"}, headers=_h(self.key_a))
        self.assertEqual(md.status_code, 200)
        self.assertIn("Orphan Margin", md.text)

    def test_tenant_isolation(self):
        job_id = self._upload(self.key_a).json()["job_id"]
        self._wait(job_id, self.key_a)
        # tenant B cannot see or download tenant A's job
        self.assertEqual(self.client.get(f"/jobs/{job_id}", headers=_h(self.key_b)).status_code, 404)
        self.assertEqual(
            self.client.get(f"/jobs/{job_id}/download", params={"format": "md"}, headers=_h(self.key_b)).status_code,
            404,
        )

    def test_quota_returns_429(self):
        with mock.patch.dict("pbicompass.service.accounts.PLAN_LIMITS",
                             {"free": 1, "pro": 200, "enterprise": 100000}, clear=True):
            accounts = AccountStore(":memory:")
            self.addCleanup(accounts.close)
            _, key = accounts.create_account("lim", plan="free")
            client = TestClient(create_app(JobStore(), sandbox_root=self._root,
                                           account_store=accounts, require_auth=True))
            up = lambda: client.post("/jobs", files={"file": ("s.zip", _zip(), "application/zip")},
                                     data={"provider": "none"}, headers=_h(key))
            self.assertEqual(up().status_code, 200)
            self.assertEqual(up().status_code, 429)  # daily quota of 1 exhausted


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class NoAuthBackwardCompatTest(unittest.TestCase):
    def test_public_mode_needs_no_key(self):
        client = TestClient(create_app(JobStore(), require_auth=False))
        me = client.get("/me").json()
        self.assertEqual(me["tenant"], "public")
        self.assertFalse(me["auth_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
