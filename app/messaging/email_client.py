"""
Transactional email (docs/ROADMAP.md; LMX Link's first real gap in production).

Same "unconfigured third-party credential -> stub" shape as
`app/messaging/sms_client.py` and `app/storage/photo_upload_client.py`:
`SmtpEmailClient` is real and used once SMTP settings exist, and until then
`StubEmailClient` logs the message so every flow that sends mail is fully
buildable and testable without an account.

**Why this exists at all.** LMX Link's signup page tells an applicant "we'll be
in touch", approval silently activates their login, and a client who forgets
their password has no way back in. None of those work without email - the
self-serve premise collapses into phone calls. It was the largest functional hole
left in the feature.

**Why SMTP rather than a vendor API.** Deliberately provider-agnostic: the same
credentials point at SES, Postmark, Resend or anything else, so the choice of
provider stays a config decision rather than a code one. That matters here
because the provider hasn't been chosen yet - the same reasoning that put the
geocoder behind an interface.

**Why stdlib `smtplib` in a thread rather than an async SMTP library.** Volume is
a handful of transactional messages a day, every send is best-effort and off any
hot path, and `asyncio.to_thread` is entirely adequate for that. Adding a
dependency to send three kinds of email would be the wrong trade.
"""
from __future__ import annotations

import asyncio
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


class EmailClient(ABC):
    engine_name: str

    @abstractmethod
    async def send(self, *, to: str, subject: str, body: str) -> bool:
        """Send one plain-text email. Returns whether it went.

        Must not raise. Every caller is best-effort - an approval must succeed
        even when mail is down, because blocking onboarding on a mail outage is
        worse than a client who has to be phoned.
        """
        raise NotImplementedError


class SmtpEmailClient(EmailClient):
    engine_name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_address: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_address
        self._use_tls = use_tls

    def _send_blocking(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._from
        message["To"] = to
        message["Subject"] = subject
        # Plain text only. Transactional mail in text form is the most reliably
        # deliverable thing there is, and an HTML template system for three
        # messages would be scope for its own sake.
        message.set_content(body)

        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username and self._password:
                smtp.login(self._username, self._password)
            smtp.send_message(message)

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        try:
            await asyncio.to_thread(self._send_blocking, to=to, subject=subject, body=body)
        except Exception as exc:  # noqa: BLE001 - see EmailClient.send's contract
            # Not `to=to`: the recipient is a real person's address and this log
            # goes to Sentry once configured. The caller logs what it needs with
            # an id instead.
            logger.warning("email_send_failed", engine=self.engine_name, error=str(exc))
            return False
        logger.info("email_sent", engine=self.engine_name, subject=subject)
        return True


class StubEmailClient(EmailClient):
    """Logs instead of sending.

    Logs the full body deliberately. An email nobody receives is invisible, and
    during development the body is the only way to check that an approval
    notification actually says something useful - the same reason the driver OTP
    stub returns its code.
    """

    engine_name = "stub"

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        logger.warning(
            "email_not_sent_stub_mode",
            to=to,
            subject=subject,
            body=body,
            reason="SMTP_HOST/SMTP_FROM_ADDRESS not configured - no mail was sent",
        )
        return False


def get_email_client() -> EmailClient:
    if settings.smtp_host and settings.smtp_from_address:
        return SmtpEmailClient(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_address=settings.smtp_from_address,
            use_tls=settings.smtp_use_tls,
        )
    logger.warning(
        "email_client_selected",
        engine="stub",
        reason="SMTP_HOST/SMTP_FROM_ADDRESS not configured - running in stub mode",
    )
    return StubEmailClient()
