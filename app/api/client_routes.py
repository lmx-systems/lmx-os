"""
Client-facing API (Phase 8, see docs/ROADMAP.md) - the backend for
client-portal/, a separate web app from the internal dashboard/ since the
audience, auth, and data scope all differ. One login per client company,
not per-user - see Client.portal_email/portal_password_hash's docstring.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.client_auth.dependencies import AuthedClient, get_current_client
from app.client_auth.login_rate_limit import LoginRateLimitExceeded, LoginRateLimiter
from app.client_auth.passwords import verify_password
from app.client_auth.tokens import issue_token
from app.db import get_db
from app.models.client import Client
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.client_auth import (
    ClientAuthToken,
    ClientLoginBody,
    ClientOrderDetailView,
    ClientOrderSummaryView,
    ClientProfileView,
)

router = APIRouter(prefix="/client", tags=["client"])


@router.post("/auth/login", response_model=ClientAuthToken)
async def login(body: ClientLoginBody, session: AsyncSession = Depends(get_db)) -> ClientAuthToken:
    limiter = LoginRateLimiter()
    try:
        await limiter.check_and_increment(body.email)
    except LoginRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(select(Client).where(Client.portal_email == body.email))
    client = result.scalar_one_or_none()

    # Same error either way (unknown email vs. wrong password) - don't leak
    # which part was wrong to an unauthenticated caller.
    if client is None or not client.portal_password_hash or not verify_password(
        body.password, client.portal_password_hash
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await limiter.reset(body.email)
    return ClientAuthToken(access_token=issue_token(str(client.id)))


async def _get_authed_client_row(session: AsyncSession, client: AuthedClient) -> Client:
    row = await session.get(Client, uuid.UUID(client.client_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return row


@router.get("/me", response_model=ClientProfileView)
async def get_my_profile(
    client: AuthedClient = Depends(get_current_client), session: AsyncSession = Depends(get_db)
) -> ClientProfileView:
    row = await _get_authed_client_row(session, client)
    return ClientProfileView(client_id=str(row.id), name=row.name, portal_email=row.portal_email or "")


def _order_summary_view(order: Order, shop_name: str | None) -> ClientOrderSummaryView:
    # No dedicated "delivered at" timestamp exists on Order yet (see
    # docs/NEXT_STEPS.md's gap list) - updated_at is a reasonable proxy
    # once status is actually "delivered", same pattern
    # app/api/driver_routes.py's _route_hours already uses for Route.
    delivered_at = order.updated_at.isoformat() if order.status == OrderStatus.delivered else None
    return ClientOrderSummaryView(
        order_id=str(order.id),
        external_order_ref=order.external_order_ref,
        sla_tier=order.sla_tier,
        status=order.status.value,
        shop_name=shop_name,
        requested_at=order.requested_at.isoformat(),
        delivered_at=delivered_at,
        fee_cents=order.fee_cents,
    )


@router.get("/orders", response_model=list[ClientOrderSummaryView])
async def list_my_orders(
    client: AuthedClient = Depends(get_current_client), session: AsyncSession = Depends(get_db)
) -> list[ClientOrderSummaryView]:
    result = await session.execute(
        select(Order)
        .where(Order.client_id == uuid.UUID(client.client_id))
        .order_by(Order.requested_at.desc())
    )
    orders = list(result.scalars().all())
    if not orders:
        return []

    shop_ids = {o.shop_id for o in orders}
    shops_result = await session.execute(select(Shop).where(Shop.id.in_(shop_ids)))
    shop_names = {s.id: s.name for s in shops_result.scalars().all()}

    return [_order_summary_view(o, shop_names.get(o.shop_id)) for o in orders]


@router.get("/orders/{order_id}", response_model=ClientOrderDetailView)
async def get_my_order(
    order_id: str,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> ClientOrderDetailView:
    order = await session.get(Order, uuid.UUID(order_id))
    # 404, not 403, for an order that exists but belongs to another client -
    # same "don't confirm existence to an unauthorized caller" reasoning as
    # the driver app's _get_owned_offer/_get_owned_stop.
    if order is None or str(order.client_id) != client.client_id:
        raise HTTPException(status_code=404, detail="Order not found")

    shop = await session.get(Shop, order.shop_id)
    summary = _order_summary_view(order, shop.name if shop else None)
    return ClientOrderDetailView(
        **summary.model_dump(),
        delivery_address=order.delivery_address,
        delivery_contact_name=order.delivery_contact_name,
    )
