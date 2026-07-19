"""FastAPI dependency that authenticates a client-portal request via Bearer JWT.
Mirrors app/driver_auth/dependencies.py."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.client_auth.tokens import InvalidClientToken, decode_token


@dataclass(frozen=True)
class AuthedClient:
    client_id: str


async def get_current_client(authorization: str | None = Header(default=None)) -> AuthedClient:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        client_id = decode_token(token)
    except InvalidClientToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return AuthedClient(client_id=client_id)
