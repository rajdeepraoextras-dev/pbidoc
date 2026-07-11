"""Admin panel: token auth, brute-force lockout, and account CRUD over HTTP.

``AdminGuard``/``verify_admin_token`` are pure stdlib and always run. The API
tests need the service extras and skip cleanly without them (matching the
pattern in test_auth.py).
"""

from __future__ import annotations

import unittest
from unittest import mock

from pbicompass.service.accounts import AccountStore
from pbicompass.service.admin import AdminGuard, verify_admin_token

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


def _h(token: str) -> dict:
    return {"X-Admin-Token": token}


class VerifyAdminTokenTest(unittest.TestCase):
    def test_matching_tokens(self):
        self.assertTrue(verify_admin_token("secret", "secret"))

    def test_mismatched_tokens(self):
        self.assertFalse(verify_admin_token("secret", "wrong"))

    def test_empty_sides_never_match(self):
        self.assertFalse(verify_admin_token(None, "x"))
        self.assertFalse(verify_admin_token("x", None))
        self.assertFalse(verify_admin_token("", ""))
        self.assertFalse(verify_admin_token(None, None))


class AdminGuardTest(unittest.TestCase):
    def test_locks_out_after_max_failures(self):
        guard = AdminGuard(max_failures=3, window_seconds=60, lockout_seconds=60)
        self.assertFalse(guard.is_locked("1.2.3.4"))
        guard.record_failure("1.2.3.4")
        guard.record_failure("1.2.3.4")
        self.assertFalse(guard.is_locked("1.2.3.4"))
        guard.record_failure("1.2.3.4")
        self.assertTrue(guard.is_locked("1.2.3.4"))

    def test_success_resets_failures(self):
        guard = AdminGuard(max_failures=2, window_seconds=60, lockout_seconds=60)
        guard.record_failure("a")
        guard.record_success("a")
        guard.record_failure("a")
        self.assertFalse(guard.is_locked("a"))

    def test_clients_tracked_independently(self):
        guard = AdminGuard(max_failures=1, window_seconds=60, lockout_seconds=60)
        guard.record_failure("a")
        self.assertTrue(guard.is_locked("a"))
        self.assertFalse(guard.is_locked("b"))

    def test_old_failures_outside_window_do_not_count(self):
        guard = AdminGuard(max_failures=2, window_seconds=60, lockout_seconds=60)
        with mock.patch("pbicompass.service.admin.time.time", return_value=1000.0):
            guard.record_failure("a")
        with mock.patch("pbicompass.service.admin.time.time", return_value=1000.0 + 61):
            guard.record_failure("a")
            self.assertFalse(guard.is_locked("a"))

    def test_lockout_expires(self):
        guard = AdminGuard(max_failures=1, window_seconds=60, lockout_seconds=10)
        with mock.patch("pbicompass.service.admin.time.time", return_value=1000.0):
            guard.record_failure("a")
            self.assertTrue(guard.is_locked("a"))
        with mock.patch("pbicompass.service.admin.time.time", return_value=1000.0 + 11):
            self.assertFalse(guard.is_locked("a"))


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AdminApiDisabledTest(unittest.TestCase):
    """No PBICOMPASS_ADMIN_TOKEN configured -> the panel is off, not silently open."""

    def test_admin_endpoints_503_without_token_configured(self):
        client = TestClient(create_app(JobStore(), require_auth=False))
        self.assertEqual(client.post("/admin/api/verify", headers=_h("anything")).status_code, 503)
        self.assertEqual(client.get("/admin/api/accounts", headers=_h("anything")).status_code, 503)

    def test_admin_page_still_serves_shell(self):
        # The HTML page itself is public (like "/"); only the API is gated.
        client = TestClient(create_app(JobStore(), require_auth=False))
        res = client.get("/admin")
        self.assertEqual(res.status_code, 200)
        self.assertIn("PBICompass", res.text)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AdminApiTest(unittest.TestCase):
    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.token = "test-admin-token"
        self.client = TestClient(create_app(
            JobStore(), account_store=self.accounts,
            require_auth=False, admin_token=self.token,
        ))

    def test_verify_requires_token(self):
        self.assertEqual(self.client.post("/admin/api/verify").status_code, 401)
        self.assertEqual(self.client.post("/admin/api/verify", headers=_h("nope")).status_code, 401)
        self.assertEqual(self.client.post("/admin/api/verify", headers=_h(self.token)).status_code, 200)

    def test_create_list_and_use_account(self):
        res = self.client.post(
            "/admin/api/accounts", headers=_h(self.token),
            json={"tenant": "acme", "name": "Acme BI", "plan": "pro"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["api_key"].startswith("pbicompass_sk_"))
        self.assertEqual(body["account"]["tenant"], "acme")

        # The minted key actually authenticates against the account store —
        # this is the "generate credentials, user logs in with them" flow.
        acct = self.accounts.verify(body["api_key"])
        self.assertIsNotNone(acct)
        self.assertEqual(acct.plan, "pro")

        listed = self.client.get("/admin/api/accounts", headers=_h(self.token)).json()["accounts"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["tenant"], "acme")
        self.assertEqual(listed[0]["monthly_limit"], 10)
        self.assertEqual(listed[0]["used_this_month"], 0)

    def test_create_requires_tenant(self):
        res = self.client.post("/admin/api/accounts", headers=_h(self.token), json={"tenant": ""})
        self.assertEqual(res.status_code, 400)

    def test_create_rejects_unknown_plan(self):
        res = self.client.post(
            "/admin/api/accounts", headers=_h(self.token),
            json={"tenant": "x", "plan": "ultra"},
        )
        self.assertEqual(res.status_code, 400)

    def test_revoke_disables_the_key(self):
        created = self.client.post(
            "/admin/api/accounts", headers=_h(self.token), json={"tenant": "gone", "plan": "free"},
        ).json()
        account_id = created["account"]["id"]
        key = created["api_key"]
        self.assertIsNotNone(self.accounts.verify(key))

        res = self.client.delete(f"/admin/api/accounts/{account_id}", headers=_h(self.token))
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(self.accounts.verify(key))

        # Revoking an id that no longer exists is a 404, not a silent success.
        self.assertEqual(
            self.client.delete(f"/admin/api/accounts/{account_id}", headers=_h(self.token)).status_code,
            404,
        )

    def test_wrong_token_never_touches_accounts(self):
        res = self.client.get("/admin/api/accounts", headers=_h("nope"))
        self.assertEqual(res.status_code, 401)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AdminLockoutTest(unittest.TestCase):
    def test_repeated_bad_tokens_lock_out_even_a_correct_one(self):
        token = "correct-token"
        client = TestClient(create_app(
            JobStore(), require_auth=False, admin_token=token,
            admin_guard=AdminGuard(max_failures=3, window_seconds=60, lockout_seconds=60),
        ))
        for _ in range(3):
            self.assertEqual(client.post("/admin/api/verify", headers=_h("bad")).status_code, 401)
        locked = client.post("/admin/api/verify", headers=_h(token))
        self.assertEqual(locked.status_code, 429)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AccountStoreCreatedWithoutRequireAuthTest(unittest.TestCase):
    """Setting only PBICOMPASS_ADMIN_TOKEN (no auth enforcement yet) should
    still let an operator provision accounts ahead of flipping auth on."""

    def test_admin_token_alone_provisions_an_account_store(self):
        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t"))
        res = client.post("/admin/api/accounts", headers=_h("t"), json={"tenant": "early", "plan": "free"})
        self.assertEqual(res.status_code, 200)
        # And /jobs is still open (require_auth=False) — accounts existing
        # doesn't retroactively lock the public tenant out.
        me = client.get("/me").json()
        self.assertEqual(me["tenant"], "public")


if __name__ == "__main__":
    unittest.main(verbosity=2)
