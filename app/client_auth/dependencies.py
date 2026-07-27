"""FastAPI dependencies that authenticate a client-portal request via Bearer JWT.

Unlike the pure JWT-decode this used to be, it now also loads the
ClientUser row and checks is_active on every request (the same tradeoff
app/ops_auth/dependencies.py makes) - so deactivating a client user, a
capability multi-user accounts introduced (docs/ROADMAP.md C4), takes
effect immediately instead of waiting for their JWT to expire. client_id
is taken from the row, not the token, so it stays authoritative even if a
user is ever moved between clients.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.tokens import InvalidClientToken, decode_token
from app.db import get_db
from app.models.client_user import CLIENT_ADMIN_ROLE, ClientUser


@dataclass(frozen=True)
class AuthedClient:
    client_id: str
    client_user_id: str
    email: str
    name: str
    role: str


async def get_current_client(
    authorization: str | None = Header(default=None), session: AsyncSession = Depends(get_db)
) -> AuthedClient:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = decode_token(token)
    except InvalidClientToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    row = await session.get(ClientUser, uuid.UUID(claims.client_user_id))
    if row is None or not row.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return AuthedClient(
        client_id=str(row.client_id),
        client_user_id=str(row.id),
        email=row.email,
        name=row.name,
        role=row.role,
    )


async def require_client_admin(client: AuthedClient = Depends(get_current_client)) -> AuthedClient:
    """Gates the client-side user-management endpoints (list/create/update
    the other users at this client) - a member has read-only access to
    their company's orders/invoices but can't manage the account itself,
    the same admin/viewer line app/ops_auth/dependencies.py draws for ops."""
    if client.role != CLIENT_ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="This action requires a client admin role")
    return client
