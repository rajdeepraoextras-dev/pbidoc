"""Day 22: email verification + password reset (§7.4/§7.5).

``email.py`` and the ``AccountStore`` token methods are pure stdlib and
always run. The ``/auth/*`` flow tests need the service extras and skip
cleanly without them.

No live SMTP server is used anywhere here: the endpoint tests inject a
``MemoryEmailBackend`` (records sent messages, so a test can pull the emailed
link back out exactly as a user would from their inbox), and the SMTP-backend
test drives a fake ``smtplib.SMTP`` — the same "stand in for the network
edge" technique the Postgres/Celery tests already use.
"""

from __future__ import annotations

import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from pbicompass.service import email as email_mod
from pbicompass.service.accounts import (RESET_TOKEN_TTL_SECONDS,
                                         VERIFY_TOKEN_TTL_SECONDS, AccountStore)
from pbicompass.service.email import (ConsoleEmailBackend, MemoryEmailBackend,
                                      OutboundEmail, SMTPEmailBackend,
                                      build_email_backend)

try:
    from fastapi.testclient import TestClient

    from pbicompass.service import JobStore, create_app
    _HAVE_SERVICE = True
except Exception:  # pragma: no cover
    _HAVE_SERVICE = False


def _token_from_link(body: str) -> str:
    """Pull the ?token=... out of the single link in an email body — exactly
    what a user's mail client does when they click it."""
    for word in body.split():
        if "token=" in word:
            return parse_qs(urlparse(word).query)["token"][0]
    raise AssertionError(f"no token link found in email body: {body!r}")


class EmailBackendTest(unittest.TestCase):
    def test_memory_backend_records_messages(self):
        backend = MemoryEmailBackend()
        backend.send(OutboundEmail(to="a@example.com", subject="Hi", body="body"))
        self.assertEqual(len(backend.sent), 1)
        self.assertEqual(backend.sent[0].to, "a@example.com")

    def test_console_backend_does_not_raise(self):
        ConsoleEmailBackend().send(OutboundEmail(to="a@example.com", subject="s", body="b"))

    def test_build_backend_defaults_to_console(self):
        with mock.patch.dict("os.environ", {"PBICOMPASS_EMAIL_BACKEND": ""}, clear=False):
            self.assertIsInstance(build_email_backend(), ConsoleEmailBackend)

    def test_build_smtp_backend_falls_back_to_console_without_config(self):
        with mock.patch.dict("os.environ", {"PBICOMPASS_EMAIL_BACKEND": "smtp"}, clear=False):
            # no SMTP host/from configured -> safe fallback, not a crash
            self.assertIsInstance(build_email_backend(), ConsoleEmailBackend)

    def test_smtp_backend_builds_and_sends_a_message_via_fake_smtplib(self):
        captured = {}

        class _FakeSMTP:
            def __init__(self, host, port, timeout=0):
                captured["host"] = host
                captured["port"] = port

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self, context=None):
                captured["starttls"] = True

            def login(self, user, password):
                captured["login"] = (user, password)

            def send_message(self, msg):
                captured["to"] = msg["To"]
                captured["from"] = msg["From"]
                captured["subject"] = msg["Subject"]

        backend = SMTPEmailBackend("smtp.example.com", 587, "user", "pw", "from@example.com", use_tls=True)
        with mock.patch.object(email_mod.smtplib, "SMTP", _FakeSMTP):
            backend.send(OutboundEmail(to="to@example.com", subject="Hello", body="Body"))
        self.assertEqual(captured["host"], "smtp.example.com")
        self.assertTrue(captured["starttls"])
        self.assertEqual(captured["login"], ("user", "pw"))
        self.assertEqual(captured["to"], "to@example.com")
        self.assertEqual(captured["subject"], "Hello")

    def test_smtp_backend_swallows_delivery_errors(self):
        class _BoomSMTP:
            def __init__(self, *a, **k):
                raise OSError("connection refused")

        backend = SMTPEmailBackend("smtp.example.com", 587, None, None, "from@example.com")
        with mock.patch.object(email_mod.smtplib, "SMTP", _BoomSMTP):
            # must NOT raise -- a mail hiccup can't be allowed to fail the auth request
            backend.send(OutboundEmail(to="to@example.com", subject="s", body="b"))


class AccountStoreEmailTokenTest(unittest.TestCase):
    def setUp(self):
        self.store = AccountStore(":memory:")
        self.addCleanup(self.store.close)
        self.user, _, _ = self.store.create_user("token@example.com", "hunter2pass")

    def test_verify_token_round_trip_and_single_use(self):
        token = self.store.create_email_token(self.user.id, "verify", VERIFY_TOKEN_TTL_SECONDS)
        self.assertEqual(self.store.consume_email_token(token, "verify"), self.user.id)
        self.assertIsNone(self.store.consume_email_token(token, "verify"))  # burned

    def test_wrong_purpose_is_rejected(self):
        token = self.store.create_email_token(self.user.id, "verify", VERIFY_TOKEN_TTL_SECONDS)
        self.assertIsNone(self.store.consume_email_token(token, "reset"))

    def test_expired_token_is_rejected(self):
        token = self.store.create_email_token(self.user.id, "verify", -1)
        self.assertIsNone(self.store.consume_email_token(token, "verify"))

    def test_new_token_invalidates_the_previous_one(self):
        first = self.store.create_email_token(self.user.id, "reset", RESET_TOKEN_TTL_SECONDS)
        second = self.store.create_email_token(self.user.id, "reset", RESET_TOKEN_TTL_SECONDS)
        self.assertIsNone(self.store.consume_email_token(first, "reset"))
        self.assertEqual(self.store.consume_email_token(second, "reset"), self.user.id)

    def test_unknown_purpose_raises(self):
        with self.assertRaises(ValueError):
            self.store.create_email_token(self.user.id, "bogus", 60)

    def test_mark_verified(self):
        self.assertFalse(self.store.get_user(self.user.id).email_verified)
        self.store.mark_email_verified(self.user.id)
        self.assertTrue(self.store.get_user(self.user.id).email_verified)

    def test_set_password_changes_hash_and_kills_sessions(self):
        raw_session, _ = self.store.create_session(self.user.id)
        self.assertIsNotNone(self.store.verify_session(raw_session))
        self.store.set_password(self.user.id, "brandnewpass")
        self.assertIsNone(self.store.authenticate("token@example.com", "hunter2pass"))
        self.assertIsNotNone(self.store.authenticate("token@example.com", "brandnewpass"))
        self.assertIsNone(self.store.verify_session(raw_session))  # invalidated

    def test_set_password_rejects_short(self):
        with self.assertRaises(ValueError):
            self.store.set_password(self.user.id, "short")


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class EmailAuthFlowTest(unittest.TestCase):
    def setUp(self):
        self.accounts = AccountStore(":memory:")
        self.addCleanup(self.accounts.close)
        self.mail = MemoryEmailBackend()
        self.client = TestClient(
            create_app(JobStore(), require_auth=False, admin_token="t",
                       account_store=self.accounts, email_backend=self.mail),
            base_url="https://testserver",
        )

    def _signup(self, email="user@example.com", password="hunter2pass"):
        return self.client.post("/auth/signup", json={"email": email, "password": password})

    def test_signup_sends_a_verification_email(self):
        res = self._signup()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["verification_email_sent"])
        self.assertEqual(len(self.mail.sent), 1)
        self.assertIn("Verify", self.mail.sent[0].subject)
        self.assertEqual(self.mail.sent[0].to, "user@example.com")

    def test_verify_link_marks_the_user_verified(self):
        self._signup()
        token = _token_from_link(self.mail.sent[0].body)
        res = self.client.get("/auth/verify", params={"token": token})
        self.assertEqual(res.status_code, 200)
        self.assertIn("verified", res.text.lower())
        self.assertTrue(self.accounts.get_user_by_email("user@example.com").email_verified)

    def test_verify_link_is_single_use(self):
        self._signup()
        token = _token_from_link(self.mail.sent[0].body)
        self.client.get("/auth/verify", params={"token": token})
        second = self.client.get("/auth/verify", params={"token": token})
        self.assertEqual(second.status_code, 400)
        self.assertIn("invalid or expired", second.text.lower())

    def test_verify_bad_token_is_400(self):
        res = self.client.get("/auth/verify", params={"token": "not-a-real-token"})
        self.assertEqual(res.status_code, 400)

    def test_reset_request_for_unknown_email_returns_200_and_sends_nothing(self):
        res = self.client.post("/auth/reset-request", json={"email": "nobody@example.com"})
        self.assertEqual(res.status_code, 200)  # enumeration-safe
        self.assertEqual(len(self.mail.sent), 0)

    def test_full_password_reset_flow(self):
        self._signup(email="reset@example.com", password="originalpass")
        self.mail.sent.clear()  # drop the signup verification email

        req = self.client.post("/auth/reset-request", json={"email": "reset@example.com"})
        self.assertEqual(req.status_code, 200)
        self.assertEqual(len(self.mail.sent), 1)
        self.assertIn("Reset", self.mail.sent[0].subject)

        token = _token_from_link(self.mail.sent[0].body)
        done = self.client.post("/auth/reset", json={"token": token, "password": "newsecurepass"})
        self.assertEqual(done.status_code, 200, done.text)

        # old password no longer works, new one does
        self.assertEqual(
            self.client.post("/auth/login", json={"email": "reset@example.com", "password": "originalpass"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/auth/login", json={"email": "reset@example.com", "password": "newsecurepass"}).status_code,
            200,
        )

    def test_reset_token_is_single_use(self):
        self._signup(email="single@example.com")
        self.mail.sent.clear()
        self.client.post("/auth/reset-request", json={"email": "single@example.com"})
        token = _token_from_link(self.mail.sent[0].body)
        self.client.post("/auth/reset", json={"token": token, "password": "newsecurepass"})
        again = self.client.post("/auth/reset", json={"token": token, "password": "anotherpass1"})
        self.assertEqual(again.status_code, 400)

    def test_reset_via_form_post_works(self):
        self._signup(email="form@example.com", password="originalpass")
        self.mail.sent.clear()
        self.client.post("/auth/reset-request", json={"email": "form@example.com"})
        token = _token_from_link(self.mail.sent[0].body)

        # the landing page serves a form...
        form_page = self.client.get("/auth/reset", params={"token": token})
        self.assertEqual(form_page.status_code, 200)
        self.assertIn("token", form_page.text)

        # ...which POSTs as a classic form (not JSON)
        done = self.client.post("/auth/reset", data={"token": token, "password": "formnewpass"})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(
            self.client.post("/auth/login", json={"email": "form@example.com", "password": "formnewpass"}).status_code,
            200,
        )

    def test_reset_short_password_is_400(self):
        self._signup(email="shortpw@example.com")
        self.mail.sent.clear()
        self.client.post("/auth/reset-request", json={"email": "shortpw@example.com"})
        token = _token_from_link(self.mail.sent[0].body)
        res = self.client.post("/auth/reset", json={"token": token, "password": "short"})
        self.assertEqual(res.status_code, 400)


@unittest.skipUnless(_HAVE_SERVICE, "service extras not installed")
class UnverifiedLoginGateTest(unittest.TestCase):
    def _make_client(self):
        accounts = AccountStore(":memory:")
        self.addCleanup(accounts.close)
        mail = MemoryEmailBackend()
        with mock.patch.dict("os.environ", {"PBICOMPASS_REQUIRE_EMAIL_VERIFICATION": "1"}):
            client = TestClient(
                create_app(JobStore(), require_auth=False, admin_token="t",
                           account_store=accounts, email_backend=mail),
                base_url="https://testserver",
            )
        return client, accounts, mail

    def test_unverified_login_is_gated_and_resends_verification(self):
        client, accounts, mail = self._make_client()
        client.post("/auth/signup", json={"email": "gated@example.com", "password": "hunter2pass"})
        mail.sent.clear()

        blocked = client.post("/auth/login", json={"email": "gated@example.com", "password": "hunter2pass"})
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("verify", blocked.text.lower())
        self.assertEqual(len(mail.sent), 1)  # a fresh verification link was re-sent

        # verify, then login succeeds
        token = _token_from_link(mail.sent[0].body)
        client.get("/auth/verify", params={"token": token})
        ok = client.post("/auth/login", json={"email": "gated@example.com", "password": "hunter2pass"})
        self.assertEqual(ok.status_code, 200)

    def test_gate_off_by_default_allows_unverified_login(self):
        accounts = AccountStore(":memory:")
        self.addCleanup(accounts.close)
        client = TestClient(
            create_app(JobStore(), require_auth=False, admin_token="t",
                       account_store=accounts, email_backend=MemoryEmailBackend()),
            base_url="https://testserver",
        )
        client.post("/auth/signup", json={"email": "ungated@example.com", "password": "hunter2pass"})
        ok = client.post("/auth/login", json={"email": "ungated@example.com", "password": "hunter2pass"})
        self.assertEqual(ok.status_code, 200)  # no gate -> unverified login allowed


if __name__ == "__main__":
    unittest.main(verbosity=2)
