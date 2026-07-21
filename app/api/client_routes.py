"""
Client-facing API (Phase 8, see docs/ROADMAP.md) - the backend for
client-portal/, a separate web app from the internal dashboard/ since the
audience, auth, and data scope all differ. One login per client company,
not per-user - see Client.portal_email/portal_password_hash's docstring.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.invoice_pdf import render_invoice_pdf
from app.billing.statements import ClientNotFoundError, build_statement
from app.client_auth.dependencies import AuthedClient, get_current_client
from app.client_auth.login_rate_limit import LoginRateLimitExceeded, LoginRateLimiter
from app.client_auth.passwords import verify_password
from app.client_auth.tokens import issue_token
from app.db import get_db
from app.models.client import Client
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.billing import StatementLineView, StatementView
from app.schemas.client_auth import (
    ClientAuthToken,
    ClientLoginBody,
    ClientOrderDetailView,
    ClientOrderSummaryView,
    ClientProfileView,
)


def _validate_period(year: int, month: int) -> None:
    if not (1 <= month <= 12) or not (2020 <= year <= 2100):
        raise HTTPException(status_code=422, detail="Invalid statement period")

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


@router.get("/billing/statements/{year}/{month}", response_model=StatementView)
async def get_my_statement(
    year: int,
    month: int,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> StatementView:
    """Monthly billing statement (roadmap item C3) - delivered orders only,
    grouped by tier/rate; orders with no configured rate surface as an
    explicit unbilled count, never $0."""
    _validate_period(year, month)
    try:
        statement = await build_statement(session, client.client_id, year, month)
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    return StatementView(
        client_id=statement.client_id,
        client_name=statement.client_name,
        year=statement.year,
        month=statement.month,
        lines=[StatementLineView(**vars(line)) for line in statement.lines],
        total_cents=statement.total_cents,
        delivered_order_count=statement.delivered_order_count,
        unbilled_order_count=statement.unbilled_order_count,
    )


@router.get("/billing/statements/{year}/{month}/invoice.pdf")
async def get_my_invoice_pdf(
    year: int,
    month: int,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> Response:
    _validate_period(year, month)
    try:
        statement = await build_statement(session, client.client_id, year, month)
    except ClientNotFoundError:
        raise HTTPException(status_code=404, detail="Client not found")
    pdf_bytes = render_invoice_pdf(statement)
    filename = f"lmx-invoice-{statement.year}-{statement.month:02d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
