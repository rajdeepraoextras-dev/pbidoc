"""Day 19: Sentry error tracking wiring (§9).

The real ``sentry_sdk`` package (installed into this environment for this
work) is exercised directly — with a custom in-memory ``Transport`` subclass
standing in for the real HTTP transport, so no network call to Sentry's
servers happens and no real DSN/project is needed. This proves the actual
SDK's event-capture and ``before_send`` scrubbing pipeline runs, not just
that a call was made — a stronger check than faking the whole SDK.
"""

from __future__ import annotations

import unittest

try:
    import sentry_sdk
    from sentry_sdk.transport import Transport

    from pbicompass.service.sentry_config import init_sentry
    _HAVE_SENTRY = True
except Exception:  # pragma: no cover
    _HAVE_SENTRY = False

_FAKE_DSN = "http://public@localhost/1"


if _HAVE_SENTRY:
    class _CapturingTransport(Transport):
        def __init__(self, options=None):
            super().__init__(options)
            self.events: list[dict] = []

        def capture_envelope(self, envelope):
            event = envelope.get_event()
            if event is not None:
                self.events.append(event)

        def flush(self, timeout, callback=None):
            return None


@unittest.skipUnless(_HAVE_SENTRY, "sentry-sdk not installed")
class SentryOffByDefaultTest(unittest.TestCase):
    def test_no_dsn_means_not_initialized(self):
        self.assertFalse(init_sentry(dsn=None))
        self.assertFalse(init_sentry(dsn=""))


@unittest.skipUnless(_HAVE_SENTRY, "sentry-sdk not installed")
class SentryScrubbingTest(unittest.TestCase):
    def setUp(self):
        self.transport = _CapturingTransport({"dsn": _FAKE_DSN})
        initialized = init_sentry(dsn=_FAKE_DSN, transport=self.transport)
        self.assertTrue(initialized)
        self.addCleanup(lambda: sentry_sdk.get_global_scope().set_client(None))

    def test_exception_message_text_is_scrubbed_to_just_the_type_name(self):
        secret = "TABLE[SecretColumnName]=42"
        try:
            raise ValueError(secret)
        except ValueError:
            sentry_sdk.capture_exception()
        sentry_sdk.get_global_scope().client.flush()

        self.assertEqual(len(self.transport.events), 1)
        event = self.transport.events[0]
        values = event["exception"]["values"]
        self.assertEqual(values[0]["value"], "ValueError")
        # The raw secret must not survive anywhere in the captured event.
        self.assertNotIn(secret, str(event))

    def test_request_data_is_never_attached(self):
        sentry_sdk.capture_message("job failed")
        sentry_sdk.get_global_scope().client.flush()
        self.assertEqual(len(self.transport.events), 1)
        self.assertNotIn("request", self.transport.events[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
