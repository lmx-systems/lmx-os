"""
Redis-backed one-time-code store for driver phone login.

Same "small Redis snapshot with TTL" shape as app/optimizer/last_cycle_store.py
- one hash per phone number, self-expiring, overwritten on every new
request. A code is single-use: verify() deletes the key on success, and
caps failed attempts so a stolen/guessed 4-digit code can't be brute-forced
before it expires.

Sends via the real app.messaging.sms_client.TwilioSmsClient once Twilio is
configured. Without Twilio a code reaches nobody, except in local
development: there it's logged here, and app/api/driver_routes.py's
request_otp returns it in the response (debug_code), so the app can be
signed into end to end without a phone. Anywhere else, handing the code
back would let anyone who knows a driver's number sign in as them. So
outside development request_otp refuses rather than issue a code it
can't deliver, and this module never logs the code.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

import structlog

from app.config import settings
from app.messaging.sms_client import get_sms_client, twilio_sms_configured
from app.redis_client import get_client, timed_operation

logger = structlog.get_logger(__name__)

OTP_TTL_SECONDS = 5 * 60
MAX_VERIFY_ATTEMPTS = 5

# Nothing throttled issuance itself - without this, anyone who can reach the
# API could hammer a phone number to enumerate valid drivers (404 vs a fresh
# code) or just spam issuance. Deliberately separate from the OTP key/TTL
# above so a burst of requests can't reset MAX_VERIFY_ATTEMPTS's window.
MAX_ISSUE_ATTEMPTS = 3
ISSUE_RATE_LIMIT_WINDOW_SECONDS = OTP_TTL_SECONDS


def _key(phone: str) -> str:
    return f"driver_auth:otp:{phone}"


def _rate_limit_key(phone: str) -> str:
    return f"driver_auth:otp:issue_count:{phone}"


class OtpRateLimitExceeded(Exception):
    pass


@dataclass(frozen=True)
class OtpIssueResult:
    code: str
    sent_via_sms: bool


class OtpStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_rate_limit(self, phone: str) -> None:
        """Split out from issue() so a caller (app/api/driver_routes.py's
        request_otp) can charge this limiter *before* checking whether the
        phone number even belongs to a real driver - otherwise the 404
        existence check is an unthrottled phone-number-enumeration oracle."""
        async with timed_operation("driver_auth.otp.rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_rate_limit_key(phone))
            pipe.expire(_rate_limit_key(phone), ISSUE_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_ISSUE_ATTEMPTS:
            raise OtpRateLimitExceeded(
                f"Too many codes requested for this number - try again in "
                f"{ISSUE_RATE_LIMIT_WINDOW_SECONDS // 60} minutes"
            )

    async def issue(self, phone: str, *, skip_rate_limit_check: bool = False) -> OtpIssueResult:
        if not skip_rate_limit_check:
            await self.check_rate_limit(phone)

        code = f"{secrets.randbelow(10000):04d}"
        async with timed_operation("driver_auth.otp.issue"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(_key(phone), mapping={"code": code, "attempts": 0})
            pipe.expire(_key(phone), OTP_TTL_SECONDS)
            await pipe.execute()

        sent_via_sms = twilio_sms_configured()
        if sent_via_sms:
            await get_sms_client().send(phone, f"Your LMX driver login code is {code}")
            logger.info("driver_otp_sms_sent", phone=phone)
        elif settings.environment == "development":
            logger.info("driver_otp_issued_dev_mode", phone=phone, code=code)
        else:
            # Not the code: anyone who can read a deployed log could sign in with it.
            logger.warning("driver_otp_not_sent", phone=phone, reason="Twilio SMS not configured")
        return OtpIssueResult(code=code, sent_via_sms=sent_via_sms)

    async def verify(self, phone: str, submitted_code: str) -> bool:
        async with timed_operation("driver_auth.otp.verify"):
            data = await self._redis.hgetall(_key(phone))
            if not data:
                return False

            if int(data.get("attempts", 0)) >= MAX_VERIFY_ATTEMPTS:
                await self._redis.delete(_key(phone))
                return False

            if data.get("code") != submitted_code:
                await self._redis.hincrby(_key(phone), "attempts", 1)
                return False

            # Single-use - delete on success so a replayed code never works twice.
            await self._redis.delete(_key(phone))
            return True
