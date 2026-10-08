"""FastAPI dependency that authenticates a driver-app request via Bearer JWT."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Header, HTTPException
from sqlalchemy import and_, select

from app.db import session_scope
from app.driver_auth.tokens import InvalidDriverToken, decode_token
from app.models.driver import Driver
from app.models.driver_device import DriverDevice


@dataclass(frozen=True)
class AuthedDriver:
    driver_id: str
    hub_id: str
    device_id: str


async def get_current_driver(authorization: str | None = Header(default=None)) -> AuthedDriver:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        driver_id, hub_id, device_id = decode_token(token)
    except InvalidDriverToken:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    await ensure_session_is_live(driver_id, device_id)
    return AuthedDriver(driver_id=driver_id, hub_id=hub_id, device_id=device_id)


async def ensure_session_is_live(driver_id: str, device_id: str) -> None:
    """Refuse a token whose driver was switched off or whose device was revoked.

    Checked on every request, not just at refresh: a token lasts about a month
    and renews itself on every open, so a revocation that waited for expiry
    would never take effect. The database is the record. Revocation used to
    live only in a Redis set, which a flush or a lost key silently undid.

    Its own short session rather than the request's: the driver's live route
    stream depends on this too, and a request-scoped session would hold a
    connection for as long as the stream is open.

    A device with no row is allowed. Every sign-in writes one, so only tokens
    minted outside sign-in (tests, the demo) lack it, and there is nothing to
    have revoked.
    """
    try:
        driver_uuid = uuid.UUID(driver_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    async with session_scope() as session:
        row = (
            await session.execute(
                select(Driver.is_active, DriverDevice.revoked_at)
                .select_from(Driver)
                .outerjoin(
                    DriverDevice,
                    and_(DriverDevice.driver_id == Driver.id, DriverDevice.device_id == device_id),
                )
                .where(Driver.id == driver_uuid)
            )
        ).one_or_none()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    is_active, revoked_at = row
    if not is_active:
        raise HTTPException(status_code=401, detail="This driver account has been switched off")
    if revoked_at is not None:
        raise HTTPException(status_code=401, detail="This device's session was revoked")
