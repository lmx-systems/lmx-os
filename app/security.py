"""
Internal API auth for the ops surface (roadmap items 0/S1).

Two accepted credentials, checked in this order:
1. A per-user ops JWT (Authorization: Bearer ..., app/ops_auth/) - the
   real thing (roadmap item S1). Role-gated: /admin/* requires the
   "admin" role; everything else accepts any active role's token.
2. The shared X-API-Key (the original interim stopgap) - retained both
   for backward compatibility and as the bootstrap path: creating the
   very first admin user (POST /admin/ops-users) has to be possible
   before any ops user exists to log in as.

Deliberately fails open (logs a warning, lets everything through) when
API_SHARED_SECRET isn't set AND no ops-token is presented, matching how
the rest of the codebase treats unconfigured credentials - this keeps
local dev and tests working without extra setup.
"""
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings
from app.logging_config import get_logger
from app.ops_auth.tokens import InvalidOpsToken, decode_token as decode_ops_token

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
# the shared secret or an admin ops token.
#
# /ops: the ops-auth surface itself (login must be reachable without a
# credential; /ops/me carries its own Bearer check in its dependency).
EXEMPT_PREFIXES = ("/driver", "/webhooks", "/client", "/ops")


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in EXEMPT_PREFIXES)


def _is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


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
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        # Per-user ops token first (roadmap item S1) - a valid token is
        # authoritative even when the shared secret is also configured, and
        # its role gates /admin/* regardless of open-mode below.
        authorization = request.headers.get("Authorization")
        if authorization and authorization.startswith("Bearer "):
            try:
                user_id, role = decode_ops_token(authorization.removeprefix("Bearer ").strip())
            except InvalidOpsToken:
                return JSONResponse({"detail": "Invalid or expired session"}, status_code=401)
            if _is_admin_path(path) and role != "admin":
                logger.warning("ops_user_forbidden_admin_path", user_id=user_id, path=path)
                return JSONResponse({"detail": "Admin role required"}, status_code=403)
            return await call_next(request)

        if not settings.api_shared_secret:
            # Open mode - unchanged legacy behavior for local dev.
            return await call_next(request)

        provided = request.headers.get(API_KEY_HEADER)
        if not provided or not secrets.compare_digest(provided, settings.api_shared_secret):
            return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)

        return await call_next(request)
