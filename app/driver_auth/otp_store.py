"""
Redis-backed one-time-code store for driver phone login.

Same "small Redis snapshot with TTL" shape as app/optimizer/last_cycle_store.py
- one hash per phone number, self-expiring, overwritten on every new
request. A code is single-use: verify() deletes the key on success, and
caps failed attempts so a stolen/guessed 4-digit code can't be brute-forced
before it expires.

No real SMS delivery exists yet (Twilio config in app/config.py is unwired
everywhere in this codebase - see docs/ARCHITECTURE.md). Sending mirrors
the existing pattern for unconfigured third-party creds (e.g.
get_route_optimization_client falling back to a stub): if Twilio isn't
configured, the code is logged server-side and returned in the response
body so the app is fully testable end-to-end without real SMS. This is
loud in the response (debug_code) precisely so it's obvious this needs
real Twilio wiring before going anywhere near production traffic.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import structlog

from app.config import settings
from app.redis_client import get_client, timed_operation

logger = structlog.get_logger(__name__)

OTP_TTL_SECONDS = 5 * 60
MAX_VERIFY_ATTEMPTS = 5


def _key(phone: str) -> str:
    return f"driver_auth:otp:{phone}"


@dataclass(frozen=True)
class OtpIssueResult:
    code: str
    sent_via_sms: bool


class OtpStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def issue(self, phone: str) -> OtpIssueResult:
        code = f"{random.randint(0, 9999):04d}"
        async with timed_operation("driver_auth.otp.issue"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(_key(phone), mapping={"code": code, "attempts": 0})
            pipe.expire(_key(phone), OTP_TTL_SECONDS)
            await pipe.execute()

        sent_via_sms = bool(
            settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number
        )
        if sent_via_sms:
            # Real send would go here (twilio.rest.Client(...).messages.create).
            # Not implemented - see this module's docstring.
            logger.info("driver_otp_sms_send_not_implemented", phone=phone)
        else:
            logger.info("driver_otp_issued_dev_mode", phone=phone, code=code)
        return OtpIssueResult(code=code, sent_via_sms=False)

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
