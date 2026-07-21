"""
General API rate limiting (roadmap item S5).

The same Redis "counter + NX-guarded TTL" shape as the two targeted
limiters that came before it (app/driver_auth/otp_store.py's OTP issuance,
app/client_auth/login_rate_limit.py's login attempts), generalized to a
middleware covering the whole API: a fixed per-minute request budget per
caller identity, keyed by client IP.

Deliberate choices:
- Keyed by IP, not by authenticated identity - this runs as middleware
  before any auth is evaluated, so it also shields the auth checks
  themselves (API-key guessing, JWT probing) rather than only what's
  behind them. The targeted per-email/per-phone limiters still exist for
  the surfaces where identity-keyed budgets matter.
- Fails open on Redis errors: a Redis outage should degrade to "no rate
  limiting" (logged), not take the whole API down with it. Same posture
  as SharedSecretAuthMiddleware's unconfigured mode.
- X-Forwarded-For's first hop is trusted when present. Behind the real
  load balancer (roadmap item S3) this is correct; exposed directly to
  the internet it would be spoofable - revisit at S3 time.
- Disabled when rate_limit_requests_per_minute is 0, and always skips
  /health + API docs (same exemptions as the shared-secret middleware) -
  a rate-limited docker healthcheck is an outage generator, not security.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings
from app.logging_config import get_logger
from app.redis_client import get_client

logger = get_logger(__name__)

WINDOW_SECONDS = 60

EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _key(ip: str) -> str:
    return f"rate_limit:api:{ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        if settings.rate_limit_requests_per_minute <= 0:
            logger.warning(
                "api_rate_limiting_disabled",
                reason="RATE_LIMIT_REQUESTS_PER_MINUTE is 0/unset",
            )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        limit = settings.rate_limit_requests_per_minute
        if limit <= 0 or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        ip = _client_ip(request)
        try:
            redis = get_client()
            pipe = redis.pipeline(transaction=True)
            pipe.incr(_key(ip))
            pipe.expire(_key(ip), WINDOW_SECONDS, nx=True)
            count, _ = await pipe.execute()
        except Exception:  # noqa: BLE001 - fail open by design, see module docstring
            logger.warning("rate_limit_check_failed_failing_open", exc_info=True)
            return await call_next(request)

        if count > limit:
            logger.warning("rate_limit_exceeded", ip=ip, count=count, limit=limit)
            return JSONResponse(
                {"detail": "Too many requests - try again shortly"},
                status_code=429,
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )

        return await call_next(request)
