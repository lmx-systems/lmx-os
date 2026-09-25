"""Rate limiting for the public Dock Log (`DRV-7`).

Same "Redis counter with an NX-guarded TTL" shape as
`app/client_auth/signup_rate_limit.py`, `login_rate_limit.py` and
`app/driver_auth/otp_store.py`.

**This surface deserves it for a reason neither of those does.** Signup fills a
review queue with rows a person has to dismiss; that is expensive and visible.
An unthrottled Dock Log fills `M5`'s training data with rows a person may *not*
dismiss, because a hundred plausible surveys of real docks look exactly like a
hundred real surveys. Volume is the attack: nothing here is individually
detectable, and the cost lands months later in a model rather than today in a
queue.

The cap is higher than signup's five, because the legitimate pattern is
different. A courier who has agreed to map docks for us covers several in an
afternoon and submits each from the same phone on the same network, which is a
behaviour signup never has. Twenty an hour is more than anybody surveys
honestly and far less than a flood.

Keyed on the caller's IP via `app/client_ip.py::client_ip`, which reads
`X-Forwarded-For` according to `TRUSTED_PROXY_COUNT` — so behind a load balancer
this throttles the actual caller rather than lumping every submitter into the
balancer's single bucket. Nothing else is available to key on: a submitter has
no account, and anything they type is theirs to choose.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

# Generous for a person mapping a street, tight against a script. See above for
# why this is four times signup's cap rather than the same number.
MAX_DOCK_LOG_SUBMISSIONS = 20
DOCK_LOG_RATE_LIMIT_WINDOW_SECONDS = 60 * 60


def _key(client_ip: str) -> str:
    return f"dock_log:submissions:{client_ip}"


class DockLogRateLimitExceeded(Exception):
    pass


class DockLogRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, client_ip: str) -> None:
        """Raises once this IP has hit the cap within the window.

        Charged **before** anything is validated or written, matching the
        ordering the S6 pass applied to driver OTP issuance and public signup.
        Charging after validation would let an attacker probe our vocabularies
        for free: submit a junk value, read the 422 naming the field, and learn
        the shape of `M5`'s label space without ever spending a write.
        """
        async with timed_operation("dock_log.rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(client_ip))
            pipe.expire(_key(client_ip), DOCK_LOG_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_DOCK_LOG_SUBMISSIONS:
            raise DockLogRateLimitExceeded(
                "Too many submissions from this address - try again later"
            )
