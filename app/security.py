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

# The driver app (app/api/driver_routes.py) has its own real per-driver auth
# now (JWT via app/driver_auth/) - it shouldn't also need the internal
# ops-tooling shared secret this middleware exists for. See docs/
# NEXT_STEPS.md item 12 and this module's own docstring ("a client-facing
# dashboard or driver app needs the real thing").
#
# Matched as whole path segments (see _is_exempt), not a bare string
# prefix - a future route that merely starts with these characters (e.g.
# /drivers-report) must NOT silently inherit this exemption.
#
# /webhooks: Twilio calls these directly (app/api/webhooks.py) and can't
# carry our X-API-Key - see that module's own docstring for the real gap
# this leaves (no request-signature verification yet either).
#
# /client: the client portal (Phase 8) has its own real per-client auth
# now (JWT via app/client_auth/), same reasoning as /driver above. Note
# that /admin (app/api/admin_routes.py) is deliberately NOT exempt here -
# onboarding a client is an internal ops action and should still require
# the shared secret.
EXEMPT_PREFIXES = ("/driver", "/webhooks", "/client")


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in EXEMPT_PREFIXES)


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
        if not settings.api_shared_secret or _is_exempt(request.url.path):
            return await call_next(request)

        provided = request.headers.get(API_KEY_HEADER)
        if not provided or not secrets.compare_digest(provided, settings.api_shared_secret):
            return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)

        return await call_next(request)
