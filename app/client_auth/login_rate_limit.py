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


class LoginRateLimitExceeded(Exception):
    pass


class LoginRateLimiter:
    """key_prefix separates surfaces: the client portal (default) and the
    ops dashboard (app/ops_auth/) each get an independent per-email budget
    - locking someone out of one must not lock them out of the other."""

    def __init__(self, key_prefix: str = "client_auth") -> None:
        self._redis = get_client()
        self._key_prefix = key_prefix

    def _key(self, email: str) -> str:
        return f"{self._key_prefix}:login_attempts:{email}"

    async def check_and_increment(self, email: str) -> None:
        """Raises LoginRateLimitExceeded once `email` has hit the cap
        within the current window. Call before verifying the password,
        same ordering app/driver_auth/otp_store.py uses for issuance."""
        async with timed_operation(f"{self._key_prefix}.login_rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(self._key(email))
            pipe.expire(self._key(email), LOGIN_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_LOGIN_ATTEMPTS:
            raise LoginRateLimitExceeded(
                f"Too many login attempts - try again in {LOGIN_RATE_LIMIT_WINDOW_SECONDS // 60} minutes"
            )

    async def reset(self, email: str) -> None:
        """Clears the counter on a successful login so someone who
        mistyped their password a few times isn't penalized on their next
        legitimate session."""
        await self._redis.delete(self._key(email))
