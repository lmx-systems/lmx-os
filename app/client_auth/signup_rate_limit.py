"""
Rate limiting for the public signup form (docs/LMX_LINK_PLAN.md).

Same "Redis counter with an NX-guarded TTL" shape as
`app/client_auth/login_rate_limit.py` and `app/driver_auth/otp_store.py`.

This surface deserves it for a different reason than login does. Login is a
brute-force target; signup is a *write* endpoint that anyone on the internet can
reach and that creates rows in `clients` and `client_users`. Unthrottled, it is
a way to fill those tables and bury real applicants in the ops review queue -
which is exactly the queue that gates whether anyone can dispatch our vans.

Keyed on the caller's IP rather than the submitted email, because the email is
attacker-chosen and a per-email limit stops nothing. The caller's IP comes from
`app/client_ip.py::client_ip` (L15), which reads X-Forwarded-For according to
TRUSTED_PROXY_COUNT - so behind a load balancer this throttles the actual caller
rather than lumping every applicant into the balancer's single bucket.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

# Deliberately generous. A distributor filling in a form, mistyping something and
# resubmitting is normal; nobody legitimately signs up five companies in an hour
# from one address.
MAX_SIGNUP_ATTEMPTS = 5
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = 60 * 60


def _key(client_ip: str) -> str:
    return f"public_signup:attempts:{client_ip}"


class SignupRateLimitExceeded(Exception):
    pass


class SignupRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, client_ip: str) -> None:
        """Raises once this IP has hit the cap within the window.

        Charged BEFORE the duplicate-email check in the endpoint, deliberately -
        the same ordering the S6 pass applied to driver OTP issuance. Charging
        afterwards would leave the endpoint as an enumeration oracle: an
        unthrottled attacker could discover which companies already have an
        account by watching which submissions come back as conflicts.
        """
        async with timed_operation("public_signup.rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(client_ip))
            pipe.expire(_key(client_ip), SIGNUP_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_SIGNUP_ATTEMPTS:
            raise SignupRateLimitExceeded(
                "Too many signup attempts from this address - try again later"
            )
