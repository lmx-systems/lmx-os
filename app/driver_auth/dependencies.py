"""FastAPI dependency that authenticates a driver-app request via Bearer JWT."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.driver_auth.tokens import InvalidDriverToken, decode_token
from app.redis_client import get_client


@dataclass(frozen=True)
class AuthedDriver:
    driver_id: str
    hub_id: str
    device_id: str


def revoked_devices_key(driver_id: str) -> str:
    return f"driver_auth:revoked_devices:{driver_id}"


async def get_current_driver(authorization: str | None = Header(default=None)) -> AuthedDriver:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        driver_id, hub_id, device_id = decode_token(token)
    except InvalidDriverToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Checked on every request, not just at refresh - a revoked device's
    # existing (still-unexpired) token must stop working immediately, e.g.
    # a driver reporting their phone stolen shouldn't have to wait out the
    # token's ~month-long expiry.
    if await get_client().sismember(revoked_devices_key(driver_id), device_id):
        raise HTTPException(status_code=401, detail="This device's session was revoked")

    return AuthedDriver(driver_id=driver_id, hub_id=hub_id, device_id=device_id)
