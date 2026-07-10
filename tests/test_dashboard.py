"""Day 24: the account dashboard (§7.6) — a signed-in user self-serves API
keys and sees usage/job history **without the admin token**. Day 29 moved
its auth from a session cookie to a Supabase-issued Bearer JWT.

``AccountStore`` API-key methods and ``JobStore.list_for_tenant`` are pure
stdlib and always run. The ``/app/api/*`` wiring tests need the service
extras *and* the ``auth`` extra (PyJWT) and skip cleanly without them.

No real Supabase project or network call is used: ``jwt.PyJWKClient.fetch_data``
is monkeypatched to return a JWKS built from a locally-generated RSA keypair
(the same technique ``test_supabase_auth.py`` uses), so every request here
carries a genuinely RS256-signed, fully-verified token.
"""

from __future__ import annotations

import json
import time
import unittest
import uuid
from unittest import mock

from pbicompass.service.accounts import (MAX_API_KEYS_PER_ACCOUNT, AccountStore)
from pbicompass.service.jobs import JobStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False

try:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from pbicompass.service.supabase_auth import SupabaseAuthConfig
    _HAVE_AUTH = True
except ImportError:  # pragma: no cover
    _HAVE_AUTH = False


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


@unittest.skipUnless(_HAVE_SERVICE and _HAVE_AUTH,
                     "service + auth extras not installed (pip install \"pbicompass[service,auth]\")")
class DashboardApiTest(unittest.TestCase):
    KID = "test-kid-dashboard"
    _URL = "https://project-ref.supabase.co"

    @classmethod
    def setUpClass(cls):
        cls._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls._private_pem = cls._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls._private_key.public_key()))
        jwk["kid"] = cls.KID
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        cls._jwks = {"keys": [jwk]}

    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.store = JobStore()
        self.addCleanup(self.store.close)
        self.cfg = SupabaseAuthConfig(url=self._URL, anon_key="anon-key")
        self.client = TestClient(
            create_app(self.store, require_auth=False, admin_token="t",
                       account_store=self.accounts, supabase_config=self.cfg),
            base_url="https://testserver",
        )
        from pbicompass.service import supabase_auth as supabase_auth_mod
        supabase_auth_mod._jwks_clients.clear()
        patcher = mock.patch.object(jwt.PyJWKClient, "fetch_data", return_value=self._jwks)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _token_for(self, email: str, sub: str | None = None) -> str:
        """Mint a fresh, fully-signed Supabase-shaped access token for a
        (new or existing) user -- the sub claim is the stable identity
        get_or_create_account_for_supabase_user() JIT-provisions an account
        against, exactly as a real Supabase-issued token would."""
        claims = {
            "sub": sub or uuid.uuid4().hex, "email": email, "aud": "authenticated",
            "iss": self.cfg.issuer, "exp": time.time() + 3600,
        }
        return jwt.encode(claims, self._private_pem, algorithm="RS256", headers={"kid": self.KID})

    def _headers(self, email="dash@example.com", sub=None):
        return {"Authorization": "Bearer " + self._token_for(email, sub=sub)}

    # -- auth gating --------------------------------------------------------
    def test_dashboard_apis_require_a_token(self):
        for path in ("/app/api/me", "/app/api/keys", "/app/api/jobs"):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_config_is_public(self):
        res = self.client.get("/app/api/config")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["supabase_enabled"])
        self.assertEqual(body["supabase_url"], self._URL)
        self.assertIn("byok_enabled", body)

    def test_app_page_is_served(self):
        res = self.client.get("/app")
        self.assertEqual(res.status_code, 200)
        self.assertIn("PBICompass", res.text)

    # -- me / usage ---------------------------------------------------------
    def test_me_reports_plan_and_usage_after_first_authenticated_request(self):
        me = self.client.get("/app/api/me", headers=self._headers())
        self.assertEqual(me.status_code, 200)
        body = me.json()
        self.assertEqual(body["email"], "dash@example.com")
        self.assertEqual(body["plan"], "free")
        self.assertIn("remaining", body)
        self.assertEqual(body["used_today"], 0)

    # -- API keys -----------------------------------------------------------
    def test_list_keys_shows_the_default_key(self):
        headers = self._headers()
        keys = self.client.get("/app/api/keys", headers=headers).json()["keys"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0]["is_primary"])

    def test_create_and_revoke_key_via_dashboard(self):
        headers = self._headers()
        created = self.client.post("/app/api/keys", json={"name": "CI"}, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)
        new = created.json()
        self.assertTrue(new["api_key"].startswith("pbicompass_sk_"))
        # the new key actually works against the API-key path
        me = self.client.get("/me", headers={"Authorization": "Bearer " + new["api_key"]})
        self.assertEqual(me.status_code, 200)
        # now there are two keys, then revoke the new one
        self.assertEqual(len(self.client.get("/app/api/keys", headers=headers).json()["keys"]), 2)
        gone = self.client.delete("/app/api/keys/" + new["id"], headers=headers)
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(len(self.client.get("/app/api/keys", headers=headers).json()["keys"]), 1)
        # the revoked key no longer maps to the account — with auth not
        # required, it now resolves to the anonymous "public" tenant instead.
        who = self.client.get("/me", headers={"Authorization": "Bearer " + new["api_key"]}).json()
        self.assertEqual(who["tenant"], "public")

    def test_revoke_missing_key_is_404(self):
        headers = self._headers()
        res = self.client.delete("/app/api/keys/nonexistent", headers=headers)
        self.assertEqual(res.status_code, 404)

    # -- job history --------------------------------------------------------
    def test_jobs_history_is_tenant_scoped(self):
        headers = self._headers()
        me = self.client.get("/app/api/me", headers=headers).json()
        tenant = me["tenant"]
        # a job for this tenant, and one for someone else
        self.store.create("mine.pbix", tenant=tenant)
        self.store.create("theirs.pbix", tenant="someone-else")
        jobs = self.client.get("/app/api/jobs", headers=headers).json()["jobs"]
        self.assertEqual([j["filename"] for j in jobs], ["mine.pbix"])
        # status-only, zero-retention: no document content in the payload
        self.assertNotIn("outputs", jobs[0])

    # -- isolation / JIT provisioning ----------------------------------------
    def test_same_sub_maps_to_the_same_account_across_requests(self):
        sub = uuid.uuid4().hex
        t1 = self.client.get("/app/api/me", headers=self._headers("same@example.com", sub=sub)).json()["tenant"]
        t2 = self.client.get("/app/api/me", headers=self._headers("same@example.com", sub=sub)).json()["tenant"]
        self.assertEqual(t1, t2)  # JIT provisioning is idempotent per Supabase user id

    def test_two_users_only_see_their_own_keys(self):
        a_keys = {k["id"] for k in self.client.get("/app/api/keys", headers=self._headers("a@example.com")).json()["keys"]}
        b_keys = {k["id"] for k in self.client.get("/app/api/keys", headers=self._headers("b@example.com")).json()["keys"]}
        self.assertTrue(a_keys.isdisjoint(b_keys))


if __name__ == "__main__":
    unittest.main(verbosity=2)
