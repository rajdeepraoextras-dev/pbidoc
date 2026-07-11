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


class OnboardingProfileStoreTest(unittest.TestCase):
    """Day 33: company/role columns, set_plan(), and the v4 snapshot — all
    pure-stdlib AccountStore behavior, no service/auth extras needed."""

    def setUp(self):
        self.store = AccountStore(":memory:")
        self.addCleanup(self.store.close)

    def test_create_account_persists_company_and_role(self):
        acct, _ = self.store.create_account("acme", name="Dana", plan="pro",
                                            company="Acme Analytics", role="Head of BI")
        self.assertEqual(acct.company, "Acme Analytics")
        self.assertEqual(acct.role, "Head of BI")
        # round-trips through verify() (a fresh row read, not the in-memory obj)
        reread = self.store.verify(_)
        self.assertEqual(reread.company, "Acme Analytics")
        self.assertEqual(reread.role, "Head of BI")

    def test_profile_fields_default_to_empty(self):
        acct, _ = self.store.create_account("plain")
        self.assertEqual(acct.company, "")
        self.assertEqual(acct.role, "")

    def test_get_or_create_seeds_profile_and_plan_on_first_call_only(self):
        first = self.store.get_or_create_account_for_supabase_user(
            "sub-1", "a@x.com", name="A", company="Co", role="Analyst", plan="pro")
        self.assertEqual(first.plan, "pro")
        self.assertEqual(first.company, "Co")
        # second call with different values returns the SAME account unchanged
        second = self.store.get_or_create_account_for_supabase_user(
            "sub-1", "a@x.com", company="Different", plan="free")
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.plan, "pro")
        self.assertEqual(second.company, "Co")

    def test_get_or_create_unknown_plan_falls_back_to_free(self):
        acct = self.store.get_or_create_account_for_supabase_user(
            "sub-2", "b@x.com", plan="enterprise-plus-unknown")
        self.assertEqual(acct.plan, "free")

    def test_set_plan_changes_the_plan(self):
        acct, _ = self.store.create_account("acme")
        self.assertTrue(self.store.set_plan(acct.id, "pro"))
        self.assertEqual(self.store.verify(_).plan, "pro")

    def test_set_plan_rejects_unknown_plan(self):
        acct, _ = self.store.create_account("acme")
        with self.assertRaises(ValueError):
            self.store.set_plan(acct.id, "not-a-plan")

    def test_set_plan_on_missing_account_returns_false(self):
        self.assertFalse(self.store.set_plan("nonexistent", "pro"))

    def test_v5_snapshot_round_trips_profile_and_admin_fields(self):
        acct, _ = self.store.create_account("acme", company="Acme", role="Lead",
                                            email="ops@acme.com")
        self.store.set_blocked(acct.id, True)
        snap = self.store.dump()
        self.assertEqual(snap["version"], 5)
        restored = AccountStore(":memory:")
        self.addCleanup(restored.close)
        restored.restore(snap)
        got = restored.list_accounts()[0]
        self.assertEqual(got.company, "Acme")
        self.assertEqual(got.role, "Lead")
        self.assertEqual(got.email, "ops@acme.com")
        self.assertTrue(got.blocked)

    def test_restore_tolerates_a_v3_snapshot_without_profile_fields(self):
        # A snapshot taken before Day 33 has no company/role keys — restore
        # must default them rather than KeyError.
        legacy = {
            "version": 3,
            "accounts": [{"id": "a1", "tenant": "t1", "name": "n", "key_hash": "h",
                          "plan": "free", "created_at": 1.0, "quota_override": None}],
        }
        restored = AccountStore(":memory:")
        self.addCleanup(restored.close)
        restored.restore(legacy)
        got = restored.list_accounts()[0]
        self.assertEqual(got.company, "")
        self.assertEqual(got.role, "")


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

    def _token_for(self, email: str, sub: str | None = None,
                   user_metadata: dict | None = None) -> str:
        """Mint a fresh, fully-signed Supabase-shaped access token for a
        (new or existing) user -- the sub claim is the stable identity
        get_or_create_account_for_supabase_user() JIT-provisions an account
        against, exactly as a real Supabase-issued token would.

        ``user_metadata`` mirrors the ``options.data`` a real signUp() call
        stashes (name/company/role/plan, Day 33) — Supabase copies it into
        the issued JWT's ``user_metadata`` claim, which is where app.py's
        ``_onboarding_fields`` reads it from."""
        claims = {
            "sub": sub or uuid.uuid4().hex, "email": email, "aud": "authenticated",
            "iss": self.cfg.issuer, "exp": time.time() + 3600,
        }
        if user_metadata is not None:
            claims["user_metadata"] = user_metadata
        return jwt.encode(claims, self._private_pem, algorithm="RS256", headers={"kid": self.KID})

    def _headers(self, email="dash@example.com", sub=None, user_metadata=None):
        return {"Authorization": "Bearer " + self._token_for(email, sub=sub, user_metadata=user_metadata)}

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
        # Day 33: the plan picker reads real quota numbers from here
        self.assertEqual(body["plan_limits"]["free"], 10)
        self.assertIn("pro", body["plan_limits"])

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
        # Day 33: profile fields are always present (empty when not supplied)
        self.assertEqual(body["company"], "")
        self.assertEqual(body["role"], "")

    # -- onboarding profile + plan-at-signup (Day 33) -----------------------
    def test_signup_metadata_seeds_company_role_and_plan(self):
        headers = self._headers(
            "founder@acme.com", sub="u-onboard-1",
            user_metadata={"name": "Dana Founder", "company": "Acme Analytics",
                           "role": "Head of BI", "plan": "pro"},
        )
        body = self.client.get("/app/api/me", headers=headers).json()
        self.assertEqual(body["company"], "Acme Analytics")
        self.assertEqual(body["role"], "Head of BI")
        self.assertEqual(body["plan"], "pro")  # trust-based plan grant, no billing
        # Pro quota is reflected in the usage limit, not just the label
        self.assertEqual(body["daily_limit"], 200)

    def test_unknown_plan_in_metadata_falls_back_to_free(self):
        headers = self._headers(
            "weird@example.com", sub="u-onboard-2",
            user_metadata={"plan": "platinum-deluxe"},  # not a real plan
        )
        body = self.client.get("/app/api/me", headers=headers).json()
        self.assertEqual(body["plan"], "free")  # never blocks account creation

    def test_metadata_only_applied_on_first_creation_not_re_upserted(self):
        # First request creates the account on the "pro" plan from metadata.
        sub = "u-onboard-3"
        first = self.client.get("/app/api/me", headers=self._headers(
            "p@example.com", sub=sub, user_metadata={"plan": "pro"})).json()
        self.assertEqual(first["plan"], "pro")
        # A later token from the same user carrying different metadata must
        # NOT silently overwrite the account (JIT-create, not upsert-always).
        second = self.client.get("/app/api/me", headers=self._headers(
            "p@example.com", sub=sub, user_metadata={"plan": "free"})).json()
        self.assertEqual(second["plan"], "pro")

    # -- self-serve plan change (Day 33) ------------------------------------
    def test_change_plan_endpoint_updates_the_account(self):
        headers = self._headers("switch@example.com", sub="u-plan-1")
        self.assertEqual(self.client.get("/app/api/me", headers=headers).json()["plan"], "free")
        res = self.client.post("/app/api/plan", json={"plan": "pro"}, headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["plan"], "pro")
        after = self.client.get("/app/api/me", headers=headers).json()
        self.assertEqual(after["plan"], "pro")
        self.assertEqual(after["daily_limit"], 200)

    def test_change_plan_rejects_unknown_plan(self):
        headers = self._headers("bad@example.com", sub="u-plan-2")
        res = self.client.post("/app/api/plan", json={"plan": "not-a-plan"}, headers=headers)
        self.assertEqual(res.status_code, 400)
        # account is untouched, still free
        self.assertEqual(self.client.get("/app/api/me", headers=headers).json()["plan"], "free")

    def test_change_plan_requires_auth(self):
        self.assertEqual(self.client.post("/app/api/plan", json={"plan": "pro"}).status_code, 401)

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

    # -- in-app admin panel (Day 34) ----------------------------------------
    def _make_admin(self, email="admin@example.com", sub="admin-1"):
        """Provision the account (first /me call), then grant it admin — the
        session-based admin the in-app panel uses, not the ops token."""
        headers = self._headers(email, sub=sub)
        self.client.get("/app/api/me", headers=headers)  # JIT-create the account
        self.accounts.grant_admin(sub)
        return headers

    def test_me_reports_is_admin_flag(self):
        headers = self._headers("plain@example.com", sub="plain-1")
        self.assertFalse(self.client.get("/app/api/me", headers=headers).json()["is_admin"])
        self.accounts.grant_admin("plain-1")
        self.assertTrue(self.client.get("/app/api/me", headers=headers).json()["is_admin"])

    def test_admin_routes_are_forbidden_for_a_signed_in_non_admin(self):
        headers = self._headers("nonadmin@example.com", sub="na-1")
        self.client.get("/app/api/me", headers=headers)  # provision
        self.assertEqual(self.client.get("/app/api/admin/accounts", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/app/api/admin/jobs", headers=headers).status_code, 403)

    def test_admin_routes_require_sign_in(self):
        self.assertEqual(self.client.get("/app/api/admin/accounts").status_code, 401)

    def test_admin_lists_all_accounts_with_usage(self):
        self.client.get("/app/api/me", headers=self._headers("u1@example.com", sub="u1"))
        self.client.get("/app/api/me", headers=self._headers("u2@example.com", sub="u2"))
        admin = self._make_admin()
        accounts = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        self.assertGreaterEqual(len(accounts), 3)  # u1, u2, admin
        self.assertIn("used_today", accounts[0])
        self.assertIn("daily_limit", accounts[0])

    def test_admin_changes_another_accounts_plan(self):
        me = self.client.get("/app/api/me", headers=self._headers("target@example.com", sub="tgt")).json()
        admin = self._make_admin()
        accounts = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        target = next(a for a in accounts if a["tenant"] == me["tenant"])
        self.assertEqual(target["plan"], "free")
        res = self.client.post(f"/app/api/admin/accounts/{target['id']}/plan",
                               json={"plan": "enterprise"}, headers=admin)
        self.assertEqual(res.status_code, 200, res.text)
        after = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        changed = next(a for a in after if a["id"] == target["id"])
        self.assertEqual(changed["plan"], "enterprise")
        self.assertEqual(changed["daily_limit"], 100000)

    def test_admin_plan_change_rejects_unknown_plan_and_missing_account(self):
        admin = self._make_admin()
        acct = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"][0]
        self.assertEqual(self.client.post(f"/app/api/admin/accounts/{acct['id']}/plan",
                                          json={"plan": "unicorn"}, headers=admin).status_code, 400)
        self.assertEqual(self.client.post("/app/api/admin/accounts/nonexistent/plan",
                                          json={"plan": "pro"}, headers=admin).status_code, 404)

    def test_admin_sets_and_clears_quota_override(self):
        admin = self._make_admin()
        acct = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"][0]
        self.client.post(f"/app/api/admin/accounts/{acct['id']}/quota",
                         json={"quota_override": 5}, headers=admin)
        after = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        self.assertEqual(next(a for a in after if a["id"] == acct["id"])["daily_limit"], 5)
        # clearing reverts to the plan default
        self.client.post(f"/app/api/admin/accounts/{acct['id']}/quota",
                         json={"quota_override": None}, headers=admin)
        cleared = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        self.assertIsNone(next(a for a in cleared if a["id"] == acct["id"])["quota_override"])

    def test_admin_rejects_negative_quota(self):
        admin = self._make_admin()
        acct = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"][0]
        self.assertEqual(self.client.post(f"/app/api/admin/accounts/{acct['id']}/quota",
                                          json={"quota_override": -1}, headers=admin).status_code, 400)

    def test_admin_sees_cross_tenant_jobs(self):
        self.store.create("a.pbix", tenant="tenant-a")
        self.store.create("b.pbix", tenant="tenant-b")
        admin = self._make_admin()
        jobs = self.client.get("/app/api/admin/jobs", headers=admin).json()["jobs"]
        tenants = {j["tenant"] for j in jobs}
        self.assertIn("tenant-a", tenants)
        self.assertIn("tenant-b", tenants)

    def test_bootstrap_admin_email_grants_admin_on_first_signin(self):
        with mock.patch.dict("os.environ", {"PBICOMPASS_BOOTSTRAP_ADMIN_EMAIL": "Boss@Example.com"}):
            app = create_app(self.store, require_auth=False, admin_token="t",
                             account_store=self.accounts, supabase_config=self.cfg)
            client = TestClient(app, base_url="https://testserver")
            # case-insensitive match, granted on the first authenticated request
            me = client.get("/app/api/me", headers=self._headers("boss@example.com", sub="boss-1")).json()
            self.assertTrue(me["is_admin"])
            # a different email is never auto-promoted
            other = client.get("/app/api/me", headers=self._headers("other@example.com", sub="other-1")).json()
            self.assertFalse(other["is_admin"])

    # -- full SaaS admin portal (Day 35) ------------------------------------
    def test_admin_accounts_list_includes_email_and_price(self):
        self.client.get("/app/api/me", headers=self._headers("who@acme.com", sub="who-1"))
        admin = self._make_admin()
        accounts = self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
        target = next(a for a in accounts if a["email"] == "who@acme.com")
        self.assertIn("monthly_price", target)
        self.assertIn("blocked", target)
        self.assertIn("user_id", target)
        self.assertFalse(target["blocked"])

    def test_admin_stats_reports_counts_and_estimated_mrr(self):
        # one pro, one enterprise, plus the admin (free)
        p = self.client.get("/app/api/me", headers=self._headers("pro@x.com", sub="pro-1",
                            user_metadata={"plan": "pro"})).json()
        self.client.get("/app/api/me", headers=self._headers("ent@x.com", sub="ent-1",
                        user_metadata={"plan": "enterprise"}))
        admin = self._make_admin()
        stats = self.client.get("/app/api/admin/stats", headers=admin).json()
        self.assertEqual(stats["total_accounts"], 3)
        self.assertEqual(stats["by_plan"]["pro"], 1)
        self.assertEqual(stats["by_plan"]["enterprise"], 1)
        # MRR = pro price + enterprise price (admin is free = 0)
        self.assertEqual(stats["estimated_mrr"],
                         stats["plan_prices"]["pro"] + stats["plan_prices"]["enterprise"])

    def test_admin_can_block_and_it_reflects_in_target_me(self):
        me = self.client.get("/app/api/me", headers=self._headers("target@x.com", sub="tgt-1")).json()
        self.assertFalse(me["blocked"])
        admin = self._make_admin()
        acct = next(a for a in self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
                    if a["tenant"] == me["tenant"])
        res = self.client.post(f"/app/api/admin/accounts/{acct['id']}/block",
                               json={"blocked": True}, headers=admin)
        self.assertEqual(res.status_code, 200, res.text)
        # the target's own dashboard now shows suspended
        after = self.client.get("/app/api/me", headers=self._headers("target@x.com", sub="tgt-1")).json()
        self.assertTrue(after["blocked"])
        # unblock restores it
        self.client.post(f"/app/api/admin/accounts/{acct['id']}/block", json={"blocked": False}, headers=admin)
        self.assertFalse(self.client.get("/app/api/me", headers=self._headers("target@x.com", sub="tgt-1")).json()["blocked"])

    def test_admin_can_grant_and_revoke_admin_on_another_account(self):
        me = self.client.get("/app/api/me", headers=self._headers("promote@x.com", sub="promote-1")).json()
        admin = self._make_admin()
        acct = next(a for a in self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
                    if a["tenant"] == me["tenant"])
        self.assertFalse(acct["is_admin"])
        self.client.post(f"/app/api/admin/accounts/{acct['id']}/admin", json={"is_admin": True}, headers=admin)
        self.assertTrue(self.client.get("/app/api/me", headers=self._headers("promote@x.com", sub="promote-1")).json()["is_admin"])
        self.client.post(f"/app/api/admin/accounts/{acct['id']}/admin", json={"is_admin": False}, headers=admin)
        self.assertFalse(self.client.get("/app/api/me", headers=self._headers("promote@x.com", sub="promote-1")).json()["is_admin"])

    def test_admin_cannot_revoke_own_admin_or_delete_own_account(self):
        admin = self._make_admin(email="self@x.com", sub="self-1")
        own = next(a for a in self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
                   if a["user_id"] == "self-1")
        self.assertEqual(self.client.post(f"/app/api/admin/accounts/{own['id']}/admin",
                                          json={"is_admin": False}, headers=admin).status_code, 400)
        self.assertEqual(self.client.delete(f"/app/api/admin/accounts/{own['id']}", headers=admin).status_code, 400)

    def test_admin_can_delete_another_account(self):
        me = self.client.get("/app/api/me", headers=self._headers("doomed@x.com", sub="doomed-1")).json()
        admin = self._make_admin()
        acct = next(a for a in self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]
                    if a["tenant"] == me["tenant"])
        res = self.client.delete(f"/app/api/admin/accounts/{acct['id']}", headers=admin)
        self.assertEqual(res.status_code, 200, res.text)
        remaining = [a["tenant"] for a in self.client.get("/app/api/admin/accounts", headers=admin).json()["accounts"]]
        self.assertNotIn(me["tenant"], remaining)

    def test_admin_routes_forbidden_for_non_admin_extended(self):
        headers = self._headers("na2@x.com", sub="na2-1")
        self.client.get("/app/api/me", headers=headers)
        for path in ("/app/api/admin/stats",):
            self.assertEqual(self.client.get(path, headers=headers).status_code, 403, path)
        self.assertEqual(self.client.delete("/app/api/admin/accounts/whatever", headers=headers).status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
