"""Day 21: user model + password auth (§7.1/§7.2/§7.5).

``passwords.py`` and the ``AccountStore`` user/session methods are pure
stdlib and always run. The ``/auth/*`` endpoint wiring tests need the
service extras and skip cleanly without them.
"""

from __future__ import annotations

import unittest
from unittest import mock

from pbicompass.service import passwords
from pbicompass.service.accounts import AccountStore

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


class PasswordHashingTest(unittest.TestCase):
    def test_hash_then_verify_round_trips(self):
        encoded = passwords.hash_password("correct horse battery staple")
        self.assertTrue(passwords.verify_password("correct horse battery staple", encoded))

    def test_wrong_password_is_rejected(self):
        encoded = passwords.hash_password("correct horse battery staple")
        self.assertFalse(passwords.verify_password("wrong password", encoded))

    def test_two_hashes_of_the_same_password_differ(self):
        # different random salts -> different encodings, even for the same input
        a = passwords.hash_password("same-password")
        b = passwords.hash_password("same-password")
        self.assertNotEqual(a, b)
        self.assertTrue(passwords.verify_password("same-password", a))
        self.assertTrue(passwords.verify_password("same-password", b))

    def test_malformed_or_foreign_encoding_is_rejected_not_raised(self):
        self.assertFalse(passwords.verify_password("x", "not-a-real-hash"))
        self.assertFalse(passwords.verify_password("x", "bcrypt$2b$12$abcdefg"))
        self.assertFalse(passwords.verify_password("x", ""))

    def test_encoding_carries_its_own_cost_parameters(self):
        encoded = passwords.hash_password("p")
        algo, n, r, p, salt_hex, hash_hex = encoded.split("$")
        self.assertEqual(algo, "scrypt")
        int(n), int(r), int(p)  # must parse as integers
        bytes.fromhex(salt_hex)
        bytes.fromhex(hash_hex)


class AccountStoreUserSessionTest(unittest.TestCase):
    def setUp(self):
        self.store = AccountStore(":memory:")
        self.addCleanup(self.store.close)

    def test_create_user_makes_a_user_account_and_membership(self):
        user, acct, key = self.store.create_user("Test@Example.com", "hunter2pass", name="Test User")
        self.assertEqual(user.email, "test@example.com")  # normalized to lowercase
        self.assertFalse(user.email_verified)
        self.assertTrue(key.startswith("pbicompass_sk_"))
        self.assertEqual(self.store.account_for_user(user.id).id, acct.id)

    def test_duplicate_email_is_rejected(self):
        self.store.create_user("dup@example.com", "hunter2pass")
        with self.assertRaises(ValueError):
            self.store.create_user("DUP@example.com", "anotherpass1")

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_user("short@example.com", "short")

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.create_user("not-an-email", "hunter2pass")

    def test_authenticate_success_and_failure_modes(self):
        user, _, _ = self.store.create_user("auth@example.com", "correcthorse")
        self.assertEqual(self.store.authenticate("auth@example.com", "correcthorse").id, user.id)
        self.assertIsNone(self.store.authenticate("auth@example.com", "wrongpassword"))
        self.assertIsNone(self.store.authenticate("nosuchuser@example.com", "whatever1"))

    def test_session_create_verify_and_delete(self):
        user, _, _ = self.store.create_user("session@example.com", "hunter2pass")
        raw_token, csrf_token = self.store.create_session(user.id)
        info = self.store.verify_session(raw_token)
        self.assertIsNotNone(info)
        self.assertEqual(info.user.id, user.id)
        self.assertEqual(info.csrf_token, csrf_token)

        self.store.delete_session(raw_token)
        self.assertIsNone(self.store.verify_session(raw_token))

    def test_session_expires(self):
        user, _, _ = self.store.create_user("expiry@example.com", "hunter2pass")
        raw_token, _ = self.store.create_session(user.id, ttl_seconds=-1)  # already expired
        self.assertIsNone(self.store.verify_session(raw_token))

    def test_verify_session_rejects_garbage_token(self):
        self.assertIsNone(self.store.verify_session("not-a-real-token"))
        self.assertIsNone(self.store.verify_session(""))


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class AuthApiWiringTest(unittest.TestCase):
    def setUp(self):
        # Explicit in-memory account store -- matching test_auth.py's own
        # convention -- rather than create_app()'s default, which would
        # otherwise open the real file at $PBICOMPASS_DB (a persistent
        # ".../pbicompass.db" in the working directory), leaking accounts
        # created by one test into every other test/run.
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        # base_url="https://testserver": cookies set with Secure=True (the
        # production default -- see _cookie_secure()) are, correctly, never
        # re-sent by an httpx cookie jar on a plain http:// connection. This
        # doesn't touch real TLS (it's still the in-process ASGI transport)
        # -- it only makes the test client's cookie jar treat the session as
        # same-origin-secure, matching how a real browser behind the TLS
        # termination every DEPLOYMENT.md option puts in front of this app
        # would actually see it.
        self.client = TestClient(
            create_app(JobStore(), require_auth=False, admin_token="t", account_store=self.accounts),
            base_url="https://testserver",
        )

    def test_accounts_not_configured_signup_returns_503(self):
        client = TestClient(create_app(JobStore(), require_auth=False))
        res = client.post("/auth/signup", json={"email": "a@example.com", "password": "hunter2pass"})
        self.assertEqual(res.status_code, 503)

    def test_signup_creates_account_and_sets_cookies(self):
        res = self.client.post("/auth/signup", json={
            "email": "new@example.com", "password": "hunter2pass", "name": "New User",
        })
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["user"]["email"], "new@example.com")
        self.assertEqual(body["plan"], "free")
        self.assertTrue(body["api_key"].startswith("pbicompass_sk_"))
        self.assertIn("pbicompass_session", res.cookies)
        self.assertIn("pbicompass_csrf", res.cookies)

    def test_signup_duplicate_email_is_400(self):
        self.client.post("/auth/signup", json={"email": "dup@example.com", "password": "hunter2pass"})
        res = self.client.post("/auth/signup", json={"email": "dup@example.com", "password": "anotherpass1"})
        self.assertEqual(res.status_code, 400)

    def test_signup_short_password_is_400(self):
        res = self.client.post("/auth/signup", json={"email": "short@example.com", "password": "short"})
        self.assertEqual(res.status_code, 400)

    def test_login_success_and_wrong_password(self):
        self.client.post("/auth/signup", json={"email": "login@example.com", "password": "hunter2pass"})
        good = self.client.post("/auth/login", json={"email": "login@example.com", "password": "hunter2pass"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["user"]["email"], "login@example.com")

        bad = self.client.post("/auth/login", json={"email": "login@example.com", "password": "wrongpass"})
        self.assertEqual(bad.status_code, 401)

    def test_login_unknown_user_is_401_same_as_wrong_password(self):
        res = self.client.post("/auth/login", json={"email": "nope@example.com", "password": "whatever1"})
        self.assertEqual(res.status_code, 401)

    def test_logout_requires_csrf_header(self):
        self.client.post("/auth/signup", json={"email": "logout@example.com", "password": "hunter2pass"})
        no_csrf = self.client.post("/auth/logout")
        self.assertEqual(no_csrf.status_code, 403)

        csrf = self.client.cookies.get("pbicompass_csrf")
        ok = self.client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
        self.assertEqual(ok.status_code, 200)

    def test_logout_wrong_csrf_token_is_403(self):
        self.client.post("/auth/signup", json={"email": "logout2@example.com", "password": "hunter2pass"})
        res = self.client.post("/auth/logout", headers={"X-CSRF-Token": "not-the-real-token"})
        self.assertEqual(res.status_code, 403)

    def test_logout_without_a_session_is_401(self):
        res = self.client.post("/auth/logout")
        self.assertEqual(res.status_code, 401)

    def test_login_lockout_after_repeated_failures(self):
        accounts = AccountStore(":memory:")
        self.addCleanup(accounts.close)
        client = TestClient(
            create_app(JobStore(), require_auth=False, admin_token="t", account_store=accounts),
            base_url="https://testserver",
        )
        client.post("/auth/signup", json={"email": "lockout@example.com", "password": "hunter2pass"})
        for _ in range(8):
            client.post("/auth/login", json={"email": "lockout@example.com", "password": "wrongpass"})
        locked = client.post("/auth/login", json={"email": "lockout@example.com", "password": "hunter2pass"})
        self.assertEqual(locked.status_code, 429)

    def test_auth_routes_are_rate_limited_per_ip(self):
        accounts = AccountStore(":memory:")
        self.addCleanup(accounts.close)
        with mock.patch.dict("os.environ", {
            "PBICOMPASS_AUTH_RATE_LIMIT": "2",
            "PBICOMPASS_AUTH_RATE_WINDOW_SECONDS": "60",
        }):
            client = TestClient(
                create_app(JobStore(), require_auth=False, admin_token="t", account_store=accounts),
                base_url="https://testserver",
            )
            r1 = client.post("/auth/login", json={"email": "x@example.com", "password": "whatever1"})
            r2 = client.post("/auth/login", json={"email": "x@example.com", "password": "whatever1"})
            r3 = client.post("/auth/login", json={"email": "x@example.com", "password": "whatever1"})
        self.assertEqual(r1.status_code, 401)
        self.assertEqual(r2.status_code, 401)
        self.assertEqual(r3.status_code, 429)

    def test_api_key_path_is_completely_unchanged(self):
        # Day 21's own done-when: the existing Bearer-API-key flow must be
        # untouched by any of the new user/session machinery.
        acct = self.client.post("/auth/signup", json={
            "email": "apikey@example.com", "password": "hunter2pass",
        }).json()
        res = self.client.get("/me", headers={"Authorization": "Bearer " + acct["api_key"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["tenant"], acct["tenant"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
