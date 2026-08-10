"""
Rate limiting the public tracking endpoint (docs/ROADMAP.md F3).

Same "Redis counter with an NX-guarded TTL" shape as
`app/client_auth/signup_rate_limit.py` and `login_rate_limit.py`.

**Why a read endpoint needs one at all.** The token in the URL is the page's only
credential, so the endpoint is a guessing target - and unlike login there is no
account to lock. 43 url-safe characters make blind guessing hopeless on its own;
what this actually buys is that the *cost* of trying stays with the guesser rather
than with our database, since every attempt is an indexed lookup plus, on a hit,
several more queries and a Redis read.

**The limit has to tolerate polling.** The page refreshes while a driver is
inbound, and a recipient may open the link on their phone and their laptop, on a
mobile network where several customers share one address. So the ceiling is set
for "a few people watching a delivery closely", not for a single reader - a limit
tight enough to catch a guesser would break the feature for a family waiting on a
part.

Keyed on IP via `app/client_ip.py::client_ip` (L15), so behind a load balancer
this throttles the real caller rather than putting every recipient in the
balancer's single bucket.
"""
from __future__ import annotations

from app.redis_client import get_client, timed_operation

# ~1 request every 3 seconds sustained over the window. The page polls far slower
# than that; a script walking the token space cannot get anywhere near a 43-char
# space at this rate.
MAX_TRACKING_REQUESTS = 120
TRACKING_RATE_LIMIT_WINDOW_SECONDS = 6 * 60


def _key(client_ip: str) -> str:
    return f"public_tracking:requests:{client_ip}"


class TrackingRateLimitExceeded(Exception):
    pass


class TrackingRateLimiter:
    def __init__(self) -> None:
        self._redis = get_client()

    async def check_and_increment(self, client_ip: str) -> None:
        """Raises once this IP has hit the cap within the window.

        Charged BEFORE the token is looked up, for the same reason signup charges
        before its duplicate-email check: doing it afterwards would let an
        attacker probe tokens for free and only pay for the ones that hit.
        """
        async with timed_operation("public_tracking.rate_limit"):
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(_key(client_ip))
            pipe.expire(_key(client_ip), TRACKING_RATE_LIMIT_WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        if count > MAX_TRACKING_REQUESTS:
            raise TrackingRateLimitExceeded("Too many tracking requests from this address")
