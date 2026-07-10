"""Transactional email backend mechanics (originally Day 22).

Identity email (verification/password reset) is Supabase Auth's job now
(Day 26-32) -- the ``/auth/verify``/``/auth/reset*`` endpoint tests that used
to live here were retired along with the rest of the hand-rolled auth system.
``email.py`` itself is kept (currently unused pending the billing work's
payment-failure/receipt notices) and is pure stdlib, so its backend mechanics
are still worth guarding on their own.

No live SMTP server is used anywhere here: the SMTP-backend test drives a
fake ``smtplib.SMTP`` -- the same "stand in for the network edge" technique
the Postgres/Celery tests already use.
"""

from __future__ import annotations

import unittest
from unittest import mock

from pbicompass.service import email as email_mod
from pbicompass.service.email import (ConsoleEmailBackend, MemoryEmailBackend,
                                      OutboundEmail, SMTPEmailBackend,
                                      build_email_backend)


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
            # must NOT raise -- a mail hiccup can't be allowed to fail the caller
            backend.send(OutboundEmail(to="to@example.com", subject="s", body="b"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
