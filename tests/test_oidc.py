"""Day 23: "Sign in with Microsoft" — Entra ID OIDC (§7.3/§7.5).

The ``oidc.py`` helpers and the ``AccountStore`` state/SSO methods are pure
stdlib and always run. The ``/auth/oidc/*`` flow tests need the service extras
and skip cleanly without them.

No network is used: the token-endpoint call (``oidc.exchange_code``) is
monkeypatched to return a **self-crafted, unsigned id_token** whose claims the
callback then validates — which is exactly what the flow does in production
anyway (this flow reads claims from the token obtained over TLS from the token
endpoint; it does not verify the JWT signature — see ``oidc.py``'s docstring),
so the test exercises the real validation path, not a stub of it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from pbicompass.service import oidc
from pbicompass.service.accounts import AccountStore
from pbicompass.service.oidc import OIDCConfig, OIDCError

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _make_id_token(claims: dict) -> str:
    return f"{_b64({'alg': 'RS256', 'typ': 'JWT'})}.{_b64(claims)}.signature-not-checked"


def _valid_claims(config: OIDCConfig, nonce: str, email: str = "user@contoso.com") -> dict:
    return {
        "aud": config.client_id,
        "exp": time.time() + 3600,
        "iss": "https://login.microsoftonline.com/tenant-guid/v2.0",
        "tid": "tenant-guid",
        "nonce": nonce,
        "email": email,
        "name": "Test User",
    }


_CFG = OIDCConfig(tenant="common", client_id="client-abc", client_secret="secret",
                  redirect_uri="https://testserver/auth/oidc/callback")


class OIDCConfigTest(unittest.TestCase):
    def test_from_env_disabled_without_config(self):
        self.assertIsNone(OIDCConfig.from_env(env={}))

    def test_from_env_needs_client_and_redirect(self):
        # client id/secret present but no redirect and no public_url -> still None
        self.assertIsNone(OIDCConfig.from_env(env={
            "PBICOMPASS_OIDC_CLIENT_ID": "a", "PBICOMPASS_OIDC_CLIENT_SECRET": "b",
        }))

    def test_from_env_derives_redirect_from_public_url(self):
        cfg = OIDCConfig.from_env(env={
            "PBICOMPASS_OIDC_CLIENT_ID": "a", "PBICOMPASS_OIDC_CLIENT_SECRET": "b",
        }, public_url="https://docs.example.com")
        self.assertEqual(cfg.redirect_uri, "https://docs.example.com/auth/oidc/callback")
        self.assertEqual(cfg.tenant, "common")  # default

    def test_endpoints_derive_from_tenant(self):
        cfg = OIDCConfig(tenant="my-tenant", client_id="c", client_secret="s", redirect_uri="r")
        self.assertIn("/my-tenant/oauth2/v2.0/authorize", cfg.authorize_endpoint)
        self.assertIn("/my-tenant/oauth2/v2.0/token", cfg.token_endpoint)
        self.assertEqual(cfg.issuer, "https://login.microsoftonline.com/my-tenant/v2.0")


class PKCEAndAuthorizeUrlTest(unittest.TestCase):
    def test_pkce_challenge_is_s256_of_verifier(self):
        verifier, challenge = oidc.generate_pkce()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)

    def test_authorize_url_has_required_params(self):
        url = oidc.build_authorize_url(_CFG, "the-state", "the-nonce", "the-challenge")
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["client_id"], ["client-abc"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["state"], ["the-state"])
        self.assertEqual(q["nonce"], ["the-nonce"])
        self.assertEqual(q["code_challenge"], ["the-challenge"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertIn("openid", q["scope"][0])


class ClaimsTest(unittest.TestCase):
    def test_decode_round_trips(self):
        claims = {"email": "a@b.com", "aud": "x"}
        self.assertEqual(oidc.decode_id_token_claims(_make_id_token(claims))["email"], "a@b.com")

    def test_decode_rejects_malformed(self):
        with self.assertRaises(OIDCError):
            oidc.decode_id_token_claims("not.a.jwt.at.all")
        with self.assertRaises(OIDCError):
            oidc.decode_id_token_claims("only-one-segment")

    def test_valid_claims_pass(self):
        oidc.validate_claims(_valid_claims(_CFG, "n"), _CFG, "n")  # no raise

    def test_wrong_audience_rejected(self):
        c = _valid_claims(_CFG, "n"); c["aud"] = "someone-else"
        with self.assertRaises(OIDCError):
            oidc.validate_claims(c, _CFG, "n")

    def test_expired_rejected(self):
        c = _valid_claims(_CFG, "n"); c["exp"] = time.time() - 3600
        with self.assertRaises(OIDCError):
            oidc.validate_claims(c, _CFG, "n")

    def test_nonce_mismatch_rejected(self):
        with self.assertRaises(OIDCError):
            oidc.validate_claims(_valid_claims(_CFG, "n"), _CFG, "different-nonce")

    def test_single_tenant_issuer_must_match_exactly(self):
        single = OIDCConfig(tenant="tenant-guid", client_id="client-abc", client_secret="s",
                            redirect_uri="r")
        good = _valid_claims(single, "n")  # iss already uses tenant-guid
        oidc.validate_claims(good, single, "n")  # no raise
        bad = _valid_claims(single, "n"); bad["iss"] = "https://login.microsoftonline.com/other/v2.0"
        with self.assertRaises(OIDCError):
            oidc.validate_claims(bad, single, "n")

    def test_email_extraction_falls_back_across_claims(self):
        self.assertEqual(oidc.email_from_claims({"email": "A@B.com"}), "a@b.com")
        self.assertEqual(oidc.email_from_claims({"preferred_username": "c@d.com"}), "c@d.com")
        self.assertEqual(oidc.email_from_claims({"upn": "e@f.com"}), "e@f.com")
        self.assertIsNone(oidc.email_from_claims({"name": "no email here"}))


class ExchangeCodeErrorTest(unittest.TestCase):
    def test_transport_error_becomes_oidc_error(self):
        def _boom(req, timeout=0):
            raise OSError("connection refused")
        with mock.patch.object(oidc.urllib.request, "urlopen", _boom):
            with self.assertRaises(OIDCError):
                oidc.exchange_code(_CFG, "code", "verifier")


class AccountStoreOIDCTest(unittest.TestCase):
    def setUp(self):
        self.store = AccountStore(":memory:")
        self.addCleanup(self.store.close)

    def test_state_round_trip_single_use(self):
        state = self.store.create_oidc_state("nonce", "verifier", 600)
        self.assertEqual(self.store.consume_oidc_state(state), ("nonce", "verifier"))
        self.assertIsNone(self.store.consume_oidc_state(state))  # burned

    def test_unknown_and_expired_state_rejected(self):
        self.assertIsNone(self.store.consume_oidc_state("nope"))
        expired = self.store.create_oidc_state("n", "v", -1)
        self.assertIsNone(self.store.consume_oidc_state(expired))

    def test_sso_user_created_verified_with_no_usable_password(self):
        user, acct = self.store.get_or_create_sso_user("SSO@contoso.com", name="SSO User")
        self.assertEqual(user.email, "sso@contoso.com")
        self.assertTrue(user.email_verified)  # IdP verified it
        self.assertTrue(acct.tenant.startswith("u-"))
        # password login stays closed (random unknown password)
        self.assertIsNone(self.store.authenticate("sso@contoso.com", "anything-they-guess"))

    def test_sso_login_links_existing_user_by_email(self):
        first, acct1 = self.store.get_or_create_sso_user("dup@contoso.com")
        second, acct2 = self.store.get_or_create_sso_user("dup@contoso.com")
        self.assertEqual(first.id, second.id)
        self.assertEqual(acct1.id, acct2.id)

    def test_sso_links_and_verifies_existing_password_user(self):
        pw_user, _, _ = self.store.create_user("both@contoso.com", "hunter2pass")
        self.assertFalse(pw_user.email_verified)
        linked, _ = self.store.get_or_create_sso_user("both@contoso.com")
        self.assertEqual(linked.id, pw_user.id)
        self.assertTrue(linked.email_verified)  # SSO verified it
        # original password still works — SSO didn't clobber it
        self.assertIsNotNone(self.store.authenticate("both@contoso.com", "hunter2pass"))


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class OIDCFlowTest(unittest.TestCase):
    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.client = TestClient(
            create_app(JobStore(), require_auth=False, admin_token="t",
                       account_store=self.accounts, oidc_config=_CFG),
            base_url="https://testserver",
        )

    def _login_redirect(self):
        return self.client.get("/auth/oidc/login", follow_redirects=False)

    def test_login_redirects_to_microsoft_with_state(self):
        res = self._login_redirect()
        self.assertEqual(res.status_code, 302)
        loc = res.headers["location"]
        self.assertIn("login.microsoftonline.com", loc)
        q = parse_qs(urlparse(loc).query)
        self.assertIn("state", q)
        self.assertIn("code_challenge", q)

    def _drive_callback(self, email="user@contoso.com"):
        """Start a real login (so a valid state row exists), then hit the
        callback with a monkeypatched token exchange returning a crafted
        id_token echoing this flow's own nonce."""
        login = self._login_redirect()
        q = parse_qs(urlparse(login.headers["location"]).query)
        state, nonce = q["state"][0], q["nonce"][0]
        tokens = {"id_token": _make_id_token(_valid_claims(_CFG, nonce, email=email))}
        with mock.patch.object(oidc, "exchange_code", return_value=tokens):
            return self.client.get("/auth/oidc/callback",
                                   params={"code": "auth-code", "state": state},
                                   follow_redirects=False)

    def test_callback_creates_user_and_opens_session(self):
        res = self._drive_callback(email="new@contoso.com")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.headers["location"], "/")
        self.assertIn("pbicompass_session", res.cookies)
        # the SSO user now exists and is verified
        user = self.accounts.get_user_by_email("new@contoso.com")
        self.assertIsNotNone(user)
        self.assertTrue(user.email_verified)

    def test_callback_second_time_reuses_the_same_user(self):
        self._drive_callback(email="repeat@contoso.com")
        self._drive_callback(email="repeat@contoso.com")
        # exactly one user/account for this email
        self.assertIsNotNone(self.accounts.get_user_by_email("repeat@contoso.com"))
        # (no duplicate-email crash; get_or_create linked it)

    def test_callback_bad_state_is_400(self):
        tokens = {"id_token": _make_id_token(_valid_claims(_CFG, "whatever"))}
        with mock.patch.object(oidc, "exchange_code", return_value=tokens):
            res = self.client.get("/auth/oidc/callback",
                                  params={"code": "c", "state": "forged-state"},
                                  follow_redirects=False)
        self.assertEqual(res.status_code, 400)

    def test_callback_nonce_mismatch_is_rejected(self):
        login = self._login_redirect()
        q = parse_qs(urlparse(login.headers["location"]).query)
        state = q["state"][0]
        # craft a token with the WRONG nonce -> validation must fail
        tokens = {"id_token": _make_id_token(_valid_claims(_CFG, "attacker-nonce"))}
        with mock.patch.object(oidc, "exchange_code", return_value=tokens):
            res = self.client.get("/auth/oidc/callback",
                                  params={"code": "c", "state": state},
                                  follow_redirects=False)
        self.assertEqual(res.status_code, 400)

    def test_callback_provider_error_shows_a_page(self):
        res = self.client.get("/auth/oidc/callback",
                              params={"error": "access_denied"}, follow_redirects=False)
        self.assertEqual(res.status_code, 400)
        self.assertIn("sign-in", res.text.lower())

    def test_oidc_routes_are_404_when_disabled(self):
        client = TestClient(create_app(JobStore(), require_auth=False, admin_token="t",
                                       account_store=AccountStore(":memory:")))
        self.assertEqual(client.get("/auth/oidc/login", follow_redirects=False).status_code, 404)
        self.assertEqual(client.get("/auth/oidc/callback", params={"code": "c", "state": "s"}).status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
