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
EXEMPT_PATHS = frozenset(
    {"/health", "/docs", "/redoc", "/openapi.json", "/ops/auth/login", "/metrics"}
)

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
#
# /public: the client signup form (app/api/public_routes.py). Genuinely
# unauthenticated - a prospective client has no credential of any kind yet, by
# design. It is the only write surface here with no auth at all, so it carries
# its own IP rate limiting and creates nothing that can act: the client lands in
# `pending` and its first user is created inactive. Add routes under this prefix
# only when they are safe for anyone on the internet to call.
#
# /internal: scheduler-callable dispatch/learning-loop triggers
# (app/api/internal_routes.py). Exempt from OPS-USER auth, not from auth: every
# route there requires a shared secret and the whole router 404s when none is
# configured. A platform scheduler carries an OIDC token or a static secret, not
# an LMX ops session, so minting a long-lived ops account for a robot was the
# alternative - and a robot with an ops session can do considerably more than run
# a dispatch cycle.
#
# /api/v1: the public order API (app/api/public_api_routes.py). Exempt from
# OPS-USER auth, not from auth: every route requires a per-client API key
# (app/client_api/dependencies.py), and the client is derived FROM that key rather
# than from the request - which is exactly why it is a new prefix instead of opening
# up /ingestion, whose hub_id and client_id are path parameters. Versioned because it
# is the first contract in this system that someone outside it writes code against.
#
# ADD NOTHING HERE THAT DOES NOT AUTHENTICATE ITSELF. That warning applies to every
# entry above, and this prefix is the one most likely to tempt a future route that
# "just needs to be reachable".
EXEMPT_PREFIXES = ("/driver", "/client", "/webhooks", "/public", "/internal", "/api/v1")


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
