"""
Password reset for client portal users (docs/ROADMAP.md L14).

Why this is not optional. A client who forgets their password currently has no
way back in: only an admin at their own company can reset it, so a company with
one admin - which is every company on the day they sign up - is locked out
permanently until someone at LMX runs `scripts/create_client_user.py` by hand.
That is not a support process, it is an outage.

Mirrors `app/driver_auth/otp_store.py`'s shape: a short-lived secret in Redis,
with issuance throttled separately from consumption so a burst of requests can't
widen the window it is trying to close.

Four properties, each deliberate:

**Tokens are stored hashed.** Redis holds SHA-256 of the token, never the token
itself, so a Redis dump or an errant `KEYS *` yields nothing usable. The raw
token exists only in the email. Same reasoning as never storing a password.

**Single use.** Consumed atomically - the delete is what authorises the reset, so
two requests racing the same token cannot both succeed.

**Issuance is throttled per email**, not only per IP. The IP limit stops a
scripted sweep; this stops one address being mail-bombed from many IPs, which is
harassment rather than an attack on us.

**KNOWN LIMITATION, and it is not fixed here.** Portal sessions are stateless
JWTs (`app/client_auth/tokens.py`) with no denylist, so resetting a password does
NOT invalidate sessions already issued. Someone holding a stolen token keeps it
until it expires. That is pre-existing - an admin-initiated reset has the same
hole - and closing it needs a token denylist or a per-user token version, which
is a bigger change than this. Worth naming rather than leaving to be discovered.
"""
from __future__ import annotations

import hashlib
import secrets

import structlog

from app.redis_client import get_client, timed_operation

logger = structlog.get_logger(__name__)

# Long enough that a link mailed at 5pm still works after dinner, short enough
# that a forwarded or archived email stops being a credential the same evening.
PASSWORD_RESET_TTL_SECONDS = 60 * 60

# Per-email issuance cap. Generous enough for someone genuinely confused about
# whether the first email arrived; tight enough that an inbox can't be buried.
MAX_RESET_REQUESTS = 3
RESET_REQUEST_WINDOW_SECONDS = 60 * 60

# 32 bytes of urandom, url-safe. Overwhelmingly more entropy than a guessing
# attack against a one-hour window could search, which is why there is no
# attempt counter on consumption the way the OTP has one.
_TOKEN_BYTES = 32


def _token_key(token: str) -> str:
    """Redis key for a token, derived from its hash rather than its value."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"client_auth:pwreset:{digest}"


def _request_count_key(email: str) -> str:
    return f"client_auth:pwreset:requests:{email.lower()}"


class ResetRequestRateLimitExceeded(Exception):
    pass


class PasswordResetStore:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_request_allowed(self, email: str) -> None:
        """Charge the per-email issuance limit.

        Called before deciding whether the address even exists, so the limit
        applies identically to a real and an unknown address - otherwise the
        throttle itself becomes an enumeration oracle.
        """
        async with timed_operation("client_auth.password_reset_rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_request_count_key(email))
            pipe.expire(_request_count_key(email), RESET_REQUEST_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_RESET_REQUESTS:
            raise ResetRequestRateLimitExceeded(
                "Too many reset requests for this address - try again later"
            )

    async def issue(self, client_user_id: str) -> str:
        """Mint a reset token and return it. Only the hash is stored."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        async with timed_operation("client_auth.password_reset_issue"):
            await self._redis.set(
                _token_key(token), client_user_id, ex=PASSWORD_RESET_TTL_SECONDS
            )
        return token

    async def consume(self, token: str) -> str | None:
        """Redeem a token, returning the client_user_id it was issued for.

        Atomic single-use: GETDEL means two requests racing the same token cannot
        both come back with an id, so a reset link cannot be replayed even if it
        is forwarded or sitting in a proxy log.
        """
        async with timed_operation("client_auth.password_reset_consume"):
            value = await self._redis.getdel(_token_key(token))
        return value if value else None

    async def invalidate_all_for_user(self, client_user_id: str) -> None:
        """No-op today, and deliberately shaped rather than silently absent.

        Ideally a successful reset would also kill any *other* outstanding tokens
        for that user. Doing it needs a user -> tokens index, and tokens are
        keyed by hash precisely so they can't be enumerated - so the two goals
        pull against each other. The exposure is small: any other token was
        issued to the same mailbox within the same hour and each is single-use.
        Named here so the gap is a decision rather than an oversight.
        """
        return None
