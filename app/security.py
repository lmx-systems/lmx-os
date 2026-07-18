"""
Shared-secret API auth - the interim stopgap from docs/ARCHITECTURE.md's
"Recommended next steps" item 0, not real per-user auth. Every request
must carry the configured secret in an X-API-Key header, checked in
constant time to avoid leaking it via response-time comparison.

Deliberately fails open (logs a warning, lets everything through) when
API_SHARED_SECRET isn't set, matching how the rest of the codebase treats
unconfigured third-party credentials (see get_route_optimization_client) -
this keeps local dev and tests working without extra setup.
"""
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

API_KEY_HEADER = "X-API-Key"

# Health checks and API introspection aren't the surface this stopgap is
# meant to protect, and gating them just breaks docker healthchecks/tooling.
EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


class SharedSecretAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        if not settings.api_shared_secret:
            logger.warning(
                "api_auth_disabled",
                reason="API_SHARED_SECRET not set - every endpoint is open",
            )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.api_shared_secret or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get(API_KEY_HEADER)
        if not provided or not secrets.compare_digest(provided, settings.api_shared_secret):
            return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)

        return await call_next(request)
