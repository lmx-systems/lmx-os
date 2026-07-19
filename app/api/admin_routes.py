"""
Internal/admin-only endpoints. Not client-facing, not driver-facing - gated
by the existing SharedSecretAuthMiddleware (X-API-Key), same as the rest of
app/api/routes.py's ops tooling. No new auth scheme needed here since
whoever calls this is LMX ops, not a client or a driver.

Phase 8 (docs/ROADMAP.md): a minimal client onboarding endpoint. There's no
admin UI yet to onboard a client's shops/rates/portal login as separate
steps, so this does all of it in one request - see
app/schemas/admin.py's ClientOnboardingBody docstring.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.passwords import hash_password
from app.db import get_db
from app.models.client import Client
from app.models.client_rate import ClientRate
from app.models.shop import Shop
from app.schemas.admin import ClientOnboardingBody, ClientOnboardingResult

router = APIRouter(prefix="/admin", tags=["admin"])

# Kept in sync by hand with app.models.order.SLATier's string values - not
# imported directly since ClientRate.sla_tier is deliberately a plain
# string, decoupled from that enum (see app/models/client_rate.py's
# docstring on why a future tier shouldn't need an enum migration first).
# This endpoint still validates against the tiers that exist *today* so a
# typo'd tier name doesn't silently create a rate nothing will ever match.
VALID_SLA_TIERS = {"HOT_SHOT", "T1", "T2", "T3"}


@router.post("/clients", response_model=ClientOnboardingResult)
async def onboard_client(
    body: ClientOnboardingBody, session: AsyncSession = Depends(get_db)
) -> ClientOnboardingResult:
    existing = await session.execute(select(Client.id).where(Client.portal_email == body.portal_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A client already uses this portal email")

    bad_tiers = [r.sla_tier for r in body.rates if r.sla_tier not in VALID_SLA_TIERS]
    if bad_tiers:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sla_tier(s) in rates: {bad_tiers}. Valid tiers: {sorted(VALID_SLA_TIERS)}",
        )
    if not body.shops:
        raise HTTPException(status_code=422, detail="At least one shop is required to onboard a client")

    client = Client(
        hub_id=uuid.UUID(body.hub_id),
        name=body.name,
        pos_system=body.pos_system,
        portal_email=body.portal_email,
        portal_password_hash=hash_password(body.portal_password),
    )
    session.add(client)
    await session.flush()  # need client.id for shops/rates below

    shop_ids: list[uuid.UUID] = []
    for shop_input in body.shops:
        shop = Shop(
            client_id=client.id,
            name=shop_input.name,
            address=shop_input.address,
            lat=shop_input.lat,
            lng=shop_input.lng,
            external_ref=shop_input.external_ref,
            phone=shop_input.phone,
        )
        session.add(shop)
        await session.flush()
        shop_ids.append(shop.id)

    for rate_input in body.rates:
        session.add(
            ClientRate(
                client_id=client.id,
                sla_tier=rate_input.sla_tier,
                rate_per_drop_cents=rate_input.rate_per_drop_cents,
            )
        )

    await session.commit()
    return ClientOnboardingResult(client_id=str(client.id), shop_ids=[str(sid) for sid in shop_ids])
