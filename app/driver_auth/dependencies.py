"""FastAPI dependency that authenticates a driver-app request via Bearer JWT."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.driver_auth.tokens import InvalidDriverToken, decode_token


@dataclass(frozen=True)
class AuthedDriver:
    driver_id: str
    hub_id: str


async def get_current_driver(authorization: str | None = Header(default=None)) -> AuthedDriver:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        driver_id, hub_id = decode_token(token)
    except InvalidDriverToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return AuthedDriver(driver_id=driver_id, hub_id=hub_id)
