"""Transactional email (Day 22, §7.4) — stdlib ``smtplib``, no vendor SDK.

The project keeps everything past the parsing core optional and, where a
capability *is* needed, prefers the stdlib over a third-party client (same
reasoning as Day 21's password hashing). Transactional providers
(Resend / Postmark / Amazon SES / …) all expose a plain **SMTP** interface, so
``smtplib`` reaches every one of them with zero new dependencies — you supply
host/port/user/password/from via env and it just works.

**Content-free w.r.t. report data by construction.** An email this system
ever sends contains only an auth link (verify / reset) and the recipient's
own address — never a fragment of a customer's model. The bodies are built
from fixed templates here; no report metadata is in scope of this module at
all.

Backend selected by ``PBICOMPASS_EMAIL_BACKEND``:
  - ``console`` (default) — logs that an email *would* be sent, including the
    link, so a self-host / dev operator can complete verify/reset flows with
    no provider configured at all. (The link is auth data, not report data,
    so logging it here is consistent with the content-free-*report*-logging
    convention.)
  - ``smtp`` — sends for real via ``PBICOMPASS_SMTP_*`` (any provider's SMTP).
  - ``memory`` is available for tests (records sent messages in a list); it
    is never selected by env, only injected explicitly.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as _StdlibEmailMessage

log = logging.getLogger("pbicompass.service.email")


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str


class EmailBackend:
    """Interface: ``send(msg)``. Subclasses must not raise on a delivery
    problem in a way that fails the caller's request — an auth flow should
    still succeed (the user account is created / the reset is recorded) even
    if the notification email can't be delivered right now."""

    def send(self, msg: OutboundEmail) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class ConsoleEmailBackend(EmailBackend):
    """Default: log the email instead of sending it. Lets the whole
    verify/reset flow work end-to-end on a fresh self-host with no provider
    — the operator reads the link out of the logs."""

    def send(self, msg: OutboundEmail) -> None:
        log.info("email (console backend) to=%s subject=%r body=%r",
                 msg.to, msg.subject, msg.body)


class MemoryEmailBackend(EmailBackend):
    """Test backend: record every sent message. Never selected by env."""

    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    def send(self, msg: OutboundEmail) -> None:
        self.sent.append(msg)


class SMTPEmailBackend(EmailBackend):
    def __init__(self, host: str, port: int, username: str | None, password: str | None,
                 sender: str, use_tls: bool = True) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.use_tls = use_tls

    def send(self, msg: OutboundEmail) -> None:
        email_msg = _StdlibEmailMessage()
        email_msg["From"] = self.sender
        email_msg["To"] = msg.to
        email_msg["Subject"] = msg.subject
        email_msg.set_content(msg.body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=15) as server:
                if self.use_tls:
                    server.starttls(context=ssl.create_default_context())
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(email_msg)
        except Exception as exc:
            # Don't fail the auth request over a mail hiccup — log the type
            # (content-free) and move on. A verify email the user never got
            # is recoverable (they can re-request); a signup that 500s
            # because SMTP was briefly down is not a good trade.
            log.warning("smtp send failed (%s); email not delivered", type(exc).__name__)


def build_email_backend() -> EmailBackend:
    backend = (os.environ.get("PBICOMPASS_EMAIL_BACKEND") or "console").strip().lower()
    if backend == "smtp":
        host = os.environ.get("PBICOMPASS_SMTP_HOST")
        sender = os.environ.get("PBICOMPASS_SMTP_FROM")
        if not host or not sender:
            log.warning("PBICOMPASS_EMAIL_BACKEND=smtp but SMTP host/from not set; "
                        "falling back to the console backend")
            return ConsoleEmailBackend()
        return SMTPEmailBackend(
            host=host,
            port=int(os.environ.get("PBICOMPASS_SMTP_PORT", "587")),
            username=os.environ.get("PBICOMPASS_SMTP_USER") or None,
            password=os.environ.get("PBICOMPASS_SMTP_PASSWORD") or None,
            sender=sender,
            use_tls=os.environ.get("PBICOMPASS_SMTP_TLS", "1").strip().lower() not in ("0", "false", "no"),
        )
    return ConsoleEmailBackend()


def public_url() -> str:
    """Base URL the emailed links point at (e.g. ``https://docs.example.com``).
    Falls back to empty so a link degrades to a bare path — still usable by an
    operator reading the console backend, and correct once set in prod."""
    return (os.environ.get("PBICOMPASS_PUBLIC_URL") or "").rstrip("/")


def verification_email(to: str, link: str) -> OutboundEmail:
    return OutboundEmail(
        to=to,
        subject="Verify your PBICompass email",
        body=(
            "Welcome to PBICompass.\n\n"
            "Confirm your email address by opening this link:\n"
            f"{link}\n\n"
            "If you didn't create an account, you can ignore this message.\n"
        ),
    )


def password_reset_email(to: str, link: str) -> OutboundEmail:
    return OutboundEmail(
        to=to,
        subject="Reset your PBICompass password",
        body=(
            "We received a request to reset your PBICompass password.\n\n"
            "Set a new password by opening this link (it expires in 1 hour):\n"
            f"{link}\n\n"
            "If you didn't request this, you can ignore this message — your "
            "password won't change.\n"
        ),
    )
