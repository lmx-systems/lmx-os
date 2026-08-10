"""
Rate limiting inbound order submission (docs/ORDER_API.md).

Same Redis counter shape as every other limiter here, with one deliberate
difference: **keyed on the API KEY, not the caller's IP.**

Two reasons, and the first is about fairness rather than abuse. A client's
integration usually runs from one server behind one address, and several clients
can sit behind the same NAT or the same cloud egress - so an IP bucket either
throttles unrelated clients together or, when a client scales out, fails to throttle
one at all. The key IS the identity here; there is no reason to guess at it from
the network.

Second: a per-key budget means one client's runaway nightly job cannot spend the
capacity every other client's orders need. That is the failure this actually
protects against - not a stranger guessing keys, which 32 random bytes already
handles.

Generous, because this is a machine interface: a manifest import legitimately fires
hundreds of orders in a burst, and a limit tuned for a human filling in a form would
break the integration it exists to enable.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

MAX_REQUESTS = 600
WINDOW_SECONDS = 60


def _key(api_key_id: str) -> str:
    return f"client_api:requests:{api_key_id}"


class ApiRateLimitExceeded(Exception):
    pass


class ApiKeyRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, api_key_id: str) -> None:
        async with timed_operation("client_api.rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(api_key_id))
            pipe.expire(_key(api_key_id), WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_REQUESTS:
            raise ApiRateLimitExceeded(
                f"Too many requests - the limit is {MAX_REQUESTS} per minute"
            )
