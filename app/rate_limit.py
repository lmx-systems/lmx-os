"""
General per-IP API rate limiting (docs/ROADMAP.md S5) - the rate limiting
that existed before this (driver OTP issuance, client portal login,
app/driver_auth/otp_store.py / app/client_auth/login_rate_limit.py) only
ever targeted specific brute-forceable actions; every other endpoint had
none at all. Same "Redis counter with an NX-guarded TTL" shape as both.

Deliberately generous, not fine-grained throttling: this system leans
heavily on client-side polling (dashboard, client portal, and driver app
all poll every few seconds - see each app's own POLL_INTERVAL_MS), and
multiple legitimate users/apps can share one originating IP (an office
network, a shared VPN egress). The goal here is bounding genuine abuse -
a runaway retry loop, a scraping attempt - not limiting normal usage.
Tune GENERAL_RATE_LIMIT_MAX_REQUESTS down only with real traffic data in
hand, not a guess.

Proxy-aware since L15: the caller's address comes from
app/client_ip.py::client_ip, which reads X-Forwarded-For according to
TRUSTED_PROXY_COUNT rather than trusting the direct TCP peer. Behind a load
balancer, keying on the peer would have made every request look like it came
from the balancer - one shared bucket throttling the whole internet as a
single caller. See that module for why naively taking the first
X-Forwarded-For entry would have been worse than the original bug.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import metrics
from app.client_ip import client_ip
from app.config import settings
from app.logging_config import get_logger
from app.redis_client import get_client, timed_operation

logger = get_logger(__name__)

# Health checks and API introspection aren't the surface this protects,
# and gating them just risks breaking monitoring/tooling that polls them
# on its own fast, fixed schedule - same reasoning as
# app/ops_auth/middleware.py's OpsUserAuthMiddleware.
EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def _key(caller_ip: str) -> str:
    return f"rate_limit:general:{caller_ip}"


class GeneralRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        caller_ip = client_ip(request)
        redis = get_client()
        async with timed_operation("general_rate_limit.check"):
            pipe = redis.pipeline(transaction=True)
            pipe.incr(_key(caller_ip))
            pipe.expire(_key(caller_ip), settings.general_rate_limit_window_seconds, nx=True)
            count, _ = await pipe.execute()

        if count > settings.general_rate_limit_max_requests:
            metrics.RATE_LIMIT_REJECTIONS.inc()
            logger.warning("general_rate_limit_exceeded", client_ip=caller_ip, path=request.url.path)
            return JSONResponse(
                {"detail": "Too many requests - slow down and try again shortly"},
                status_code=429,
                headers={"Retry-After": str(settings.general_rate_limit_window_seconds)},
            )

        return await call_next(request)
