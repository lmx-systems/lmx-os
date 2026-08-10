"""
Turning an API key into "which client is this, and may they act"
(docs/ORDER_API.md, docs/ROADMAP.md LMX Link T5).

**The security property this file exists for: the client is derived from the
credential, never from the request.** The pre-existing ingestion endpoint takes
`client_id` in the path, which is fine when the caller is an LMX ops user but would
be a hole the moment the caller is a client - one client's key could submit orders
billed to, and delivered for, another. There is deliberately no way to pass a client
id to the external endpoint at all; the only source is the key.

Everything else here is the ordinary checklist for a machine credential, but two
items are worth naming:

  - **The client must be `active`.** A pending applicant or a rejected one holding
    an old key must not be able to dispatch a van. Rechecked per request rather than
    captured at key creation, the same way `ClientUser.is_active` is - a
    deactivation has to take effect immediately, not at next key rotation.
  - **Every rejection is the same 401.** Unknown key, revoked key, deactivated
    client - one response. Distinguishing them tells a prober which of their
    guesses was once real.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_api.rate_limit import ApiKeyRateLimiter, ApiRateLimitExceeded
from app.db import get_db
from app.models.client import Client
from app.models.client_api_key import ClientApiKey, hash_api_key

logger = structlog.get_logger(__name__)

# One message for every failure mode. See the module docstring.
_UNAUTHORIZED = "Invalid API key"


@dataclass(frozen=True)
class AuthedApiClient:
    """A client's system, authenticated. `client_id` comes from the key alone."""

    client_id: str
    hub_id: str
    api_key_id: str


async def get_api_client(
    x_lmx_api_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> AuthedApiClient:
    if not x_lmx_api_key:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    result = await session.execute(
        select(ClientApiKey, Client)
        .join(Client, Client.id == ClientApiKey.client_id)
        .where(
            # Looked up BY HASH. The plaintext is never stored, so a database
            # disclosure leaks no usable credential - see the model docstring on why
            # this differs from the outbound webhook secret.
            ClientApiKey.token_hash == hash_api_key(x_lmx_api_key),
            ClientApiKey.is_active.is_(True),
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    api_key, client = row
    if client.signup_status != "active" or not client.active:
        # A pending applicant or a deactivated client holding a live key must not be
        # able to dispatch a van. Same 401 as an unknown key.
        logger.warning(
            "api_key_rejected_inactive_client",
            client_id=str(client.id),
            signup_status=client.signup_status,
        )
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)

    try:
        # Charged after authentication, unlike the public signup limiter. There is no
        # enumeration concern here - an unknown key is rejected before this and
        # cannot consume anyone's budget - and a per-key limit is meaningless until
        # you know which key it is.
        await ApiKeyRateLimiter().check_and_increment(str(api_key.id))
    except ApiRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    await _touch(session, api_key.id)

    return AuthedApiClient(
        client_id=str(client.id),
        # The hub comes from the client too. An external caller has no business
        # naming a hub, and letting them would be a way to place orders at a
        # location they have no relationship with.
        hub_id=str(client.hub_id),
        api_key_id=str(api_key.id),
    )


async def _touch(session: AsyncSession, api_key_id) -> None:
    """Record that this key was used, so rotation is possible.

    A separate UPDATE rather than mutating the loaded row, because the request
    handler owns that transaction and may roll it back - a rejected order still
    proves the key is live, and losing that would leave a client guessing which of
    two keys their system actually uses.

    Best-effort: failing to record a timestamp must never fail an order.
    """
    try:
        await session.execute(
            update(ClientApiKey)
            .where(ClientApiKey.id == api_key_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
    except Exception:  # noqa: BLE001
        logger.warning("api_key_touch_failed", api_key_id=str(api_key_id))
