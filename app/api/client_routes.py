"""
Client-facing API (Phase 8, see docs/ROADMAP.md) - the backend for
client-portal/, a separate web app from the internal dashboard/ since the
audience, auth, and data scope all differ. Logins are per-user now
(multi-user client accounts, docs/ROADMAP.md C4) - see
app/models/client_user.py - so a client company can have an accounts-
payable contact and an operations contact as separate accounts, one of
whom is an admin who manages the others.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.service import invoice_detail_view, invoice_summary_view
from app.client_auth.dependencies import AuthedClient, get_current_client, require_client_admin
from app.client_auth.login_rate_limit import LoginRateLimitExceeded, LoginRateLimiter
from app.client_auth.passwords import hash_password, verify_password
from app.client_auth.tokens import issue_token
from app.db import get_db
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, CLIENT_USER_ROLES, ClientUser
from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.billing import InvoiceDetailView, InvoiceSummaryView
from app.schemas.client_auth import (
    ClientAuthToken,
    ClientLoginBody,
    ClientOrderDetailView,
    ClientOrderSummaryView,
    ClientProfileView,
    ClientUserCreateBody,
    ClientUserUpdateBody,
    ClientUserView,
)

router = APIRouter(prefix="/client", tags=["client"])


@router.post("/auth/login", response_model=ClientAuthToken)
async def login(body: ClientLoginBody, session: AsyncSession = Depends(get_db)) -> ClientAuthToken:
    limiter = LoginRateLimiter()
    try:
        await limiter.check_and_increment(body.email)
    except LoginRateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    result = await session.execute(select(ClientUser).where(ClientUser.email == body.email))
    user = result.scalar_one_or_none()

    # Same error either way (unknown email vs. wrong password vs.
    # deactivated) - don't leak which part was wrong to an unauthenticated
    # caller. A deactivated user fails here too, not just mid-session.
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await limiter.reset(body.email)
    return ClientAuthToken(
        access_token=issue_token(str(user.id), str(user.client_id), user.role)
    )


@router.get("/me", response_model=ClientProfileView)
async def get_my_profile(
    client: AuthedClient = Depends(get_current_client), session: AsyncSession = Depends(get_db)
) -> ClientProfileView:
    company = await session.get(Client, uuid.UUID(client.client_id))
    if company is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return ClientProfileView(
        client_id=client.client_id,
        name=company.name,
        email=client.email,
        user_name=client.name,
        role=client.role,
    )


def _client_user_view(user: ClientUser) -> ClientUserView:
    return ClientUserView(
        client_user_id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
    )


@router.get("/users", response_model=list[ClientUserView])
async def list_my_client_users(
    client: AuthedClient = Depends(require_client_admin), session: AsyncSession = Depends(get_db)
) -> list[ClientUserView]:
    result = await session.execute(
        select(ClientUser)
        .where(ClientUser.client_id == uuid.UUID(client.client_id))
        .order_by(ClientUser.created_at.asc())
    )
    return [_client_user_view(u) for u in result.scalars().all()]


@router.post("/users", response_model=ClientUserView, status_code=201)
async def create_my_client_user(
    body: ClientUserCreateBody,
    client: AuthedClient = Depends(require_client_admin),
    session: AsyncSession = Depends(get_db),
) -> ClientUserView:
    if body.role not in CLIENT_USER_ROLES:
        raise HTTPException(
            status_code=422, detail=f"Unknown role {body.role!r}. Valid roles: {sorted(CLIENT_USER_ROLES)}"
        )

    # Email is globally unique (app/models/client_user.py) - check before
    # inserting so a collision is a clean 409, not a raw IntegrityError.
    # Intentionally not scoped to this client: an admin shouldn't be able
    # to probe whether an address is in use at *another* client either, so
    # the message stays generic.
    existing = await session.execute(select(ClientUser.id).where(ClientUser.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A user already exists with this email")

    user = ClientUser(
        client_id=uuid.UUID(client.client_id),
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role=body.role,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    return _client_user_view(user)


async def _count_other_active_admins(session: AsyncSession, client_id: str, excluding_user_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ClientUser)
        .where(
            ClientUser.client_id == uuid.UUID(client_id),
            ClientUser.role == CLIENT_ADMIN_ROLE,
            ClientUser.is_active.is_(True),
            ClientUser.id != uuid.UUID(excluding_user_id),
        )
    )
    return int(result.scalar_one())


@router.patch("/users/{user_id}", response_model=ClientUserView)
async def update_my_client_user(
    user_id: str,
    body: ClientUserUpdateBody,
    client: AuthedClient = Depends(require_client_admin),
    session: AsyncSession = Depends(get_db),
) -> ClientUserView:
    user = await session.get(ClientUser, uuid.UUID(user_id))
    # 404 (not 403) for a user at another client - same "don't confirm
    # existence to an unauthorized caller" convention as get_my_order.
    if user is None or str(user.client_id) != client.client_id:
        raise HTTPException(status_code=404, detail="User not found")

    if body.role is not None and body.role not in CLIENT_USER_ROLES:
        raise HTTPException(
            status_code=422, detail=f"Unknown role {body.role!r}. Valid roles: {sorted(CLIENT_USER_ROLES)}"
        )

    # Lockout guard: never let the last active admin at a client be
    # deactivated or demoted - that would leave the account with no one
    # able to manage users, unrecoverable except by ops
    # (scripts/create_client_user.py). Checked against *other* admins, so
    # an admin editing a second admin is fine.
    demoting = body.role is not None and body.role != CLIENT_ADMIN_ROLE and user.role == CLIENT_ADMIN_ROLE
    deactivating = body.is_active is False and user.is_active
    if (demoting or deactivating) and user.role == CLIENT_ADMIN_ROLE and user.is_active:
        if await _count_other_active_admins(session, client.client_id, user_id) == 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove the last active admin - promote another user to admin first",
            )

    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.new_password is not None:
        user.password_hash = hash_password(body.new_password)

    await session.commit()
    return _client_user_view(user)


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


@router.get("/invoices", response_model=list[InvoiceSummaryView])
async def list_my_invoices(
    client: AuthedClient = Depends(get_current_client), session: AsyncSession = Depends(get_db)
) -> list[InvoiceSummaryView]:
    result = await session.execute(
        select(Invoice)
        .where(Invoice.client_id == uuid.UUID(client.client_id))
        .order_by(Invoice.period_start.desc())
    )
    invoices = list(result.scalars().all())
    return [await invoice_summary_view(session, invoice) for invoice in invoices]


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetailView)
async def get_my_invoice(
    invoice_id: str,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> InvoiceDetailView:
    invoice = await session.get(Invoice, uuid.UUID(invoice_id))
    # 404, not 403, for an invoice that exists but belongs to another
    # client - same convention as get_my_order above.
    if invoice is None or str(invoice.client_id) != client.client_id:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return await invoice_detail_view(session, invoice)
