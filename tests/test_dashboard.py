"""Day 24: the account dashboard (§7.6) — a signed-in user self-serves API
keys and sees usage/job history **without the admin token**.

``AccountStore`` API-key methods and ``JobStore.list_for_tenant`` are pure
stdlib and always run. The ``/app/api/*`` wiring tests need the service extras
and skip cleanly without them.
"""

from __future__ import annotations

import unittest

from pbicompass.service.accounts import (MAX_API_KEYS_PER_ACCOUNT, AccountStore)
from pbicompass.service.jobs import JobStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


class ApiKeyManagementTest(unittest.TestCase):
    def setUp(self):
        self.store = AccountStore(":memory:")
        self.addCleanup(self.store.close)

    def test_account_starts_with_one_default_key_that_verifies(self):
        acct, key = self.store.create_account("acme")
        keys = self.store.list_api_keys(acct.id)
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].is_primary)
        self.assertEqual(keys[0].name, "Default")
        self.assertEqual(self.store.verify(key).tenant, "acme")

    def test_create_additional_key_verifies_independently(self):
        acct, _ = self.store.create_account("acme")
        info, raw = self.store.create_api_key(acct.id, name="CI")
        self.assertFalse(info.is_primary)
        self.assertEqual(self.store.verify(raw).tenant, "acme")
        self.assertEqual(len(self.store.list_api_keys(acct.id)), 2)

    def test_revoke_key_is_real_and_scoped(self):
        acct, primary = self.store.create_account("acme")
        info, raw = self.store.create_api_key(acct.id, name="temp")
        self.assertTrue(self.store.revoke_api_key(acct.id, info.id))
        self.assertIsNone(self.store.verify(raw))          # revoked key dead
        self.assertIsNotNone(self.store.verify(primary))   # others unaffected

    def test_primary_key_can_be_revoked_too(self):
        acct, primary = self.store.create_account("acme")
        prim = [k for k in self.store.list_api_keys(acct.id) if k.is_primary][0]
        self.store.revoke_api_key(acct.id, prim.id)
        self.assertIsNone(self.store.verify(primary))  # legacy accounts.key_hash no longer authenticates

    def test_cannot_revoke_another_accounts_key(self):
        a1, _ = self.store.create_account("t1")
        a2, _ = self.store.create_account("t2")
        info2, raw2 = self.store.create_api_key(a2.id, name="k")
        self.assertFalse(self.store.revoke_api_key(a1.id, info2.id))  # wrong owner
        self.assertIsNotNone(self.store.verify(raw2))                 # untouched

    def test_key_cap_enforced(self):
        acct, _ = self.store.create_account("acme")  # 1 (Default)
        for _ in range(MAX_API_KEYS_PER_ACCOUNT - 1):
            self.store.create_api_key(acct.id)
        with self.assertRaises(ValueError):
            self.store.create_api_key(acct.id)

    def test_revoke_account_drops_all_its_keys(self):
        acct, key = self.store.create_account("acme")
        _, raw = self.store.create_api_key(acct.id)
        self.store.revoke_account(acct.id)
        self.assertIsNone(self.store.verify(key))
        self.assertIsNone(self.store.verify(raw))


class JobHistoryTest(unittest.TestCase):
    def test_list_for_tenant_is_scoped_and_newest_first(self):
        jobs = JobStore()
        self.addCleanup(jobs.close)
        a1 = jobs.create("first.pbix", tenant="acme")
        a2 = jobs.create("second.pbix", tenant="acme")
        other = jobs.create("theirs.pbix", tenant="other")
        listed = jobs.list_for_tenant("acme")
        self.assertEqual([j.id for j in listed], [a2.id, a1.id])  # newest first
        self.assertNotIn(other.id, [j.id for j in listed])       # tenant-scoped

    def test_list_for_tenant_respects_limit(self):
        jobs = JobStore()
        self.addCleanup(jobs.close)
        for i in range(5):
            jobs.create(f"f{i}.pbix", tenant="acme")
        self.assertEqual(len(jobs.list_for_tenant("acme", limit=3)), 3)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class DashboardApiTest(unittest.TestCase):
    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.store = JobStore()
        self.addCleanup(self.store.close)
        self.client = TestClient(
            create_app(self.store, require_auth=False, admin_token="t", account_store=self.accounts),
            base_url="https://testserver",
        )

    def _signup(self, email="dash@example.com", password="hunter2pass"):
        return self.client.post("/auth/signup", json={"email": email, "password": password})

    def _csrf(self):
        return self.client.cookies.get("pbicompass_csrf")

    # -- auth gating --------------------------------------------------------
    def test_dashboard_apis_require_a_session(self):
        for path in ("/app/api/me", "/app/api/keys", "/app/api/jobs"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_config_is_public(self):
        res = self.client.get("/app/api/config")
        self.assertEqual(res.status_code, 200)
        self.assertIn("oidc_enabled", res.json())

    def test_app_page_is_served(self):
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        self.assertIn("PBICompass", res.text)

    # -- me / usage ---------------------------------------------------------
    def test_me_reports_plan_and_usage_after_signup(self):
        self._signup()
        me = self.client.get("/app/api/me")
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertEqual(body["email"], "dash@example.com")
        self.assertEqual(body["plan"], "free")
        self.assertIn("remaining", body)
        self.assertEqual(body["used_today"], 0)

    # -- API keys -----------------------------------------------------------
    def test_list_keys_shows_the_default_key(self):
        self._signup()
        keys = self.client.get("/app/api/keys").json()["keys"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0]["is_primary"])

    def test_create_key_requires_csrf(self):
        self._signup()
        # session cookie present but no X-CSRF-Token header -> 403
        no_csrf = self.client.post("/app/api/keys", json={"name": "x"})
        self.assertEqual(no_csrf.status_code, 403)

    def test_create_and_revoke_key_via_dashboard(self):
        self._signup()
        created = self.client.post("/app/api/keys", json={"name": "CI"},
                                   headers={"X-CSRF-Token": self._csrf()})
        self.assertEqual(created.status_code, 200, created.text)
        new = created.json()
        self.assertTrue(new["api_key"].startswith("pbicompass_sk_"))
        # the new key actually works against the API-key path
        me = self.client.get("/me", headers={"Authorization": "Bearer " + new["api_key"]})
        self.assertEqual(me.status_code, 200)
        # now there are two keys, then revoke the new one
        self.assertEqual(len(self.client.get("/app/api/keys").json()["keys"]), 2)
        gone = self.client.delete("/app/api/keys/" + new["id"],
                                  headers={"X-CSRF-Token": self._csrf()})
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(len(self.client.get("/app/api/keys").json()["keys"]), 1)
        # the revoked key no longer maps to the account — with auth not
        # required, it now resolves to the anonymous "public" tenant instead.
        who = self.client.get("/me", headers={"Authorization": "Bearer " + new["api_key"]}).json()
        self.assertEqual(who["tenant"], "public")

    def test_revoke_missing_key_is_404(self):
        self._signup()
        res = self.client.delete("/app/api/keys/nonexistent",
                                 headers={"X-CSRF-Token": self._csrf()})
        self.assertEqual(res.status_code, 404)

    # -- job history --------------------------------------------------------
    def test_jobs_history_is_tenant_scoped(self):
        self._signup()
        me = self.client.get("/app/api/me").json()
        tenant = me["tenant"]
        # a job for this tenant, and one for someone else
        self.store.create("mine.pbix", tenant=tenant)
        self.store.create("theirs.pbix", tenant="someone-else")
        jobs = self.client.get("/app/api/jobs").json()["jobs"]
        self.assertEqual([j["filename"] for j in jobs], ["mine.pbix"])
        # status-only, zero-retention: no document content in the payload
        self.assertNotIn("outputs", jobs[0])

    # -- isolation ----------------------------------------------------------
    def test_two_users_only_see_their_own_keys(self):
        self._signup(email="a@example.com")
        a_keys = {k["id"] for k in self.client.get("/app/api/keys").json()["keys"]}
        # log out, sign up a second user
        self.client.post("/auth/logout", headers={"X-CSRF-Token": self._csrf()})
        self._signup(email="b@example.com")
        b_keys = {k["id"] for k in self.client.get("/app/api/keys").json()["keys"]}
        self.assertTrue(a_keys.isdisjoint(b_keys))


if __name__ == "__main__":
    unittest.main(verbosity=2)
