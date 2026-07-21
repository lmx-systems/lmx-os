"""
Real per-account ops auth (docs/ROADMAP.md S1) - replaces the old shared
X-API-Key stopgap (previously app/security.py's
SharedSecretAuthMiddleware) with a Bearer JWT tied to a real OpsUser row,
the same shape app/client_auth/ already uses for the client portal. Every
ops user can still do everything any other ops user can - there's no
role model yet, a real gap this doesn't attempt to close.
"""
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.logging_config import get_logger
from app.ops_auth.dependencies import InvalidOpsSession, authenticate_token

logger = get_logger(__name__)

# Health checks and API introspection aren't the surface this protects,
# and gating them just breaks docker healthchecks/tooling.
EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/ops/auth/login"})

# Matched as whole path segments (see _is_exempt), not a bare string
# prefix - a future route that merely starts with these characters (e.g.
# /drivers-report) must NOT silently inherit this exemption.
#
# /driver, /client: each has its own real per-account auth already
# (app/driver_auth/, app/client_auth/) - this middleware exists for
# everything else (the internal ops surface: /fleet, /hubs, /optimizer,
# /batch-queue, /orders, /learning-loop, /admin, /ops/me).
#
# /webhooks: Twilio calls these directly (app/api/webhooks.py) and can't
# carry an ops Bearer token - that endpoint has its own request-signature
# verification instead (app/messaging/twilio_signature.py).
EXEMPT_PREFIXES = ("/driver", "/client", "/webhooks")


def _is_exempt(path: str) -> bool:
    if path in EXEMPT_PATHS:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in EXEMPT_PREFIXES)


class OpsUserAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if _is_exempt(request.url.path):
            return await call_next(request)

        authorization = request.headers.get("Authorization")
        if not authorization or not authorization.startswith("Bearer "):
            return JSONResponse({"detail": "Missing bearer token"}, status_code=401)

        token = authorization.removeprefix("Bearer ").strip()
        try:
            await authenticate_token(token)
        except InvalidOpsSession:
            return JSONResponse({"detail": "Invalid or expired session"}, status_code=401)

        return await call_next(request)
