"""
Login-attempt rate limiting for the ops dashboard (docs/ROADMAP.md S1).

Same "Redis counter with an NX-guarded TTL" shape as
app/client_auth/login_rate_limit.py / app/driver_auth/otp_store.py - an
ops user's email is a fixed, guessable target, and an ops session
authorizes fleet-wide read/write across every hub, making unthrottled
login here at least as attractive a brute-force target as the client
portal's.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

MAX_LOGIN_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60


def _key(email: str) -> str:
    return f"ops_auth:login_attempts:{email}"


class LoginRateLimitExceeded(Exception):
    pass


class LoginRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, email: str) -> None:
        """Raises LoginRateLimitExceeded once `email` has hit the cap
        within the current window. Call before verifying the password,
        same ordering app/client_auth/login_rate_limit.py uses."""
        async with timed_operation("ops_auth.login_rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(email))
            pipe.expire(_key(email), LOGIN_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_LOGIN_ATTEMPTS:
            raise LoginRateLimitExceeded(
                f"Too many login attempts - try again in {LOGIN_RATE_LIMIT_WINDOW_SECONDS // 60} minutes"
            )

    async def reset(self, email: str) -> None:
        """Clears the counter on a successful login so an ops user who
        mistyped their password a few times isn't penalized on their next
        legitimate session."""
        await self._redis.delete(_key(email))
