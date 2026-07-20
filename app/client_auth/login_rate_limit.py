"""
Login-attempt rate limiting for the client portal (Phase 8).

Same "Redis counter with an NX-guarded TTL" shape as
app/driver_auth/otp_store.py's issuance limiter - a client's portal_email
is a fixed, guessable target (unlike a driver's rotating 4-digit OTP),
which makes unthrottled login a more attractive brute-force surface than
the one that limiter already closes.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

MAX_LOGIN_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60


def _key(email: str) -> str:
    return f"client_auth:login_attempts:{email}"


class LoginRateLimitExceeded(Exception):
    pass


class LoginRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, email: str) -> None:
        """Raises LoginRateLimitExceeded once `email` has hit the cap
        within the current window. Call before verifying the password,
        same ordering app/driver_auth/otp_store.py uses for issuance."""
        async with timed_operation("client_auth.login_rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(email))
            pipe.expire(_key(email), LOGIN_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_LOGIN_ATTEMPTS:
            raise LoginRateLimitExceeded(
                f"Too many login attempts - try again in {LOGIN_RATE_LIMIT_WINDOW_SECONDS // 60} minutes"
            )

    async def reset(self, email: str) -> None:
        """Clears the counter on a successful login so a client who
        mistyped their password a few times isn't penalized on their next
        legitimate session."""
        await self._redis.delete(_key(email))
