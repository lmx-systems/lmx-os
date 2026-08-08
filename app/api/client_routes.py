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

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.clustering import miles_between
from app.batch_queue.store import HoldQueueStore
from app.billing.invoice_pdf import render_invoice_pdf
from app.billing.service import invoice_detail_view, invoice_summary_view
from app.geocoding import get_geocoder
from app.gig_platform.economics import minutes_for_miles
from app.ingestion.service import (
    DestinationUnresolvableError,
    OriginUnresolvableError,
    ShopNotFoundError,
    ingest_lmx_order,
)
from app.client_auth.dependencies import AuthedClient, get_current_client, require_client_admin
from app.client_auth.login_rate_limit import LoginRateLimitExceeded, LoginRateLimiter
from app.client_auth.passwords import hash_password, verify_password
from app.client_auth.tokens import issue_token
from app.db import get_db
from app.models.client import Client
from app.models.client_user import CLIENT_ADMIN_ROLE, CLIENT_USER_ROLES, ClientUser
from app.models.invoice import Invoice
from app.models.order import Order
from app.models.return_item import ReturnItem
from app.models.shop import Shop
from app.returns.service import AWAITING_STATUSES, return_views
from app.schemas.billing import InvoiceDetailView, InvoiceSummaryView
from app.schemas.client_order import (
    ClientOrderBatchBody,
    ClientOrderBatchResult,
    ClientOrderBatchRowResult,
    ClientOrderBody,
    ClientOrderResult,
    deadline_payload_flags,
)
from app.schemas.lmx_order import LMXOrder, LineItem
from app.schemas.returns import ReturnFlagBody, ReturnItemView
from app.schemas.client_auth import (
    ClientAuthToken,
    ClientLoginBody,
    ClientOrderDetailView,
    ClientOrderSummaryView,
    ClientProfileView,
    ClientShopView,
    ClientUserCreateBody,
    ClientUserUpdateBody,
    ClientUserView,
)

logger = structlog.get_logger(__name__)

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

    # Email is globally unique (app/models/client_user.py). The upfront
    # check gives the common case a clean 409; the unique constraint is the
    # real backstop, so two concurrent creates of the same email still
    # resolve to a 409 (the loser's commit raises IntegrityError) rather
    # than a 500. Intentionally not scoped to this client: an admin
    # shouldn't be able to probe whether an address is in use at *another*
    # client either, so the message stays generic.
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
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A user already exists with this email") from exc
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
    # Known limitation: this read-then-write isn't serialized, so two
    # concurrent PATCHes each removing one of the last two admins could
    # both pass and strand the client with zero. Acceptable for a
    # manually-driven admin action with a documented ops recovery path; if
    # it ever needs to be airtight, take a row lock on the client's admin
    # rows (SELECT ... FOR UPDATE) around this check.
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
    # Real delivery timestamp now (docs/ROADMAP.md I1) - was an updated_at
    # proxy until Order.delivered_at existed. Historical delivered orders
    # were backfilled (migration 0022), so this is populated for them too.
    delivered_at = order.delivered_at.isoformat() if order.delivered_at else None
    return ClientOrderSummaryView(
        order_id=str(order.id),
        external_order_ref=order.external_order_ref,
        sla_tier=order.sla_tier,
        status=order.status.value,
        shop_name=shop_name,
        requested_at=order.requested_at.isoformat(),
        delivered_at=delivered_at,
        fee_cents=order.fee_cents,
        failure_reason=order.failure_reason,
        delivery_attempts=order.delivery_attempts,
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


@router.get("/invoices/{invoice_id}/pdf")
async def get_my_invoice_pdf(
    invoice_id: str,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Server-side PDF of an invoice (docs/ROADMAP.md C3). Same ownership
    check as get_my_invoice - 404 (not 403) for another client's invoice.
    Rendered from the same InvoiceDetailView the JSON endpoint returns, so
    the PDF and the on-screen invoice can never drift apart."""
    invoice = await session.get(Invoice, uuid.UUID(invoice_id))
    if invoice is None or str(invoice.client_id) != client.client_id:
        raise HTTPException(status_code=404, detail="Invoice not found")

    detail = await invoice_detail_view(session, invoice)
    company = await session.get(Client, uuid.UUID(client.client_id))
    pdf = render_invoice_pdf(detail, company.name if company else "")
    filename = f"lmx-invoice-{detail.invoice_number}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Returns & core pickups (docs/ROADMAP.md W1 slice 2) - the shop-flag half of
# the model: a client flags accumulated cores at one of their shops as ready
# for a standalone pickup (no delivery to piggyback on), and sees their
# returns' status. Any client-user can flag (it's operational, not account
# management); the dedicated counter-person surface is W5.
# ---------------------------------------------------------------------------
@router.post("/shops/{shop_id}/returns", response_model=ReturnItemView, status_code=201)
async def flag_shop_returns_ready(
    shop_id: str,
    body: ReturnFlagBody,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> ReturnItemView:
    shop = await session.get(Shop, uuid.UUID(shop_id))
    # 404 (not 403) for a shop that isn't this client's - same
    # don't-confirm-existence convention as get_my_order.
    if shop is None or str(shop.client_id) != client.client_id:
        raise HTTPException(status_code=404, detail="Shop not found")

    company = await session.get(Client, uuid.UUID(client.client_id))
    item = ReturnItem(
        hub_id=company.hub_id, shop_id=shop.id, origin_order_id=None,
        manifest=body.manifest, status="ready_for_pickup",
    )
    session.add(item)
    await session.commit()
    return (await return_views(session, [item]))[0]


@router.get("/shops", response_model=list[ClientShopView])
async def list_my_shops(
    client: AuthedClient = Depends(get_current_client), session: AsyncSession = Depends(get_db)
) -> list[ClientShopView]:
    """The caller company's shops (docs/ROADMAP.md W1 slice 4) - drives the
    portal's flag-cores-ready shop picker."""
    result = await session.execute(
        select(Shop).where(Shop.client_id == uuid.UUID(client.client_id)).order_by(Shop.name)
    )
    return [
        ClientShopView(
            shop_id=str(s.id), name=s.name, external_ref=s.external_ref, address=s.address
        )
        for s in result.scalars().all()
    ]


@router.get("/returns", response_model=list[ReturnItemView])
async def list_my_returns(
    awaiting: bool = False,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> list[ReturnItemView]:
    """Every return across this client's shops, newest first. Pass
    `awaiting=true` for just the ones still waiting on a pickup - the
    counter-facing cut (docs/ROADMAP.md W1 slice 4); each row carries
    `age_hours` so a stale core is obvious."""
    query = (
        select(ReturnItem)
        .join(Shop, ReturnItem.shop_id == Shop.id)
        .where(Shop.client_id == uuid.UUID(client.client_id))
    )
    if awaiting:
        query = query.where(ReturnItem.status.in_(AWAITING_STATUSES))
    query = query.order_by(ReturnItem.created_at.desc())
    result = await session.execute(query)
    return await return_views(session, list(result.scalars().all()))


# ---------------------------------------------------------------------------
# Order submission (docs/LMX_LINK_PLAN.md §2.2)
#
# The first order-CREATING endpoint a client has ever had. Everything else on
# this router reads what LMX already knows; this is the front door.
# ---------------------------------------------------------------------------


async def _require_approved_client(session: AsyncSession, client: AuthedClient) -> Client:
    """Load the client and refuse if they aren't approved yet.

    Belt and braces rather than redundancy. A pending client's user is created
    inactive, so `get_current_client` should already have rejected them at the
    token check - but that is one boolean on a different table, and the
    consequence of it being wrong is a stranger putting work into the dispatch
    queue. This checks the fact that actually matters: has a human approved this
    company.
    """
    row = await session.get(Client, uuid.UUID(client.client_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if row.signup_status != "active":
        raise HTTPException(
            status_code=403,
            detail="Your account is still being reviewed - you'll be able to send orders once it's approved.",
        )
    return row


def _generated_reference() -> str:
    """A reference for a client who didn't give one.

    Optional on purpose - one more mandatory field is one more reason not to
    finish the form - so something has to fill the gap, and it has to be
    findable when they ring up about it.
    """
    return f"LMX-{secrets.token_hex(4).upper()}"


def _address_error_detail(exc: Exception) -> str:
    """Which address we couldn't place, in words a counter person can act on."""
    which = "pickup" if isinstance(exc, OriginUnresolvableError) else "delivery"
    return f"We couldn't find that {which} address - please check it and try again."


async def _resolve_pickup(
    session: AsyncSession,
    client_row: Client,
    *,
    shop_id: str | None,
    typed_address: str | None,
) -> tuple[str | None, str | None]:
    """Turn a client's chosen pickup into (address, shop_external_ref).

    Shared by the single-order and batch paths so the ownership check can't drift
    between them - a shop id from another client must be a 404 on both, never a
    usable pickup.
    """
    address = (typed_address or "").strip() or None
    if shop_id is None:
        return address, None

    shop = await session.get(Shop, uuid.UUID(shop_id))
    # Scoped, not merely existence-checked.
    if shop is None or shop.client_id != client_row.id:
        raise HTTPException(status_code=404, detail="Pickup location not found")

    # A remembered shop with no external_ref predates this path; fall back to its
    # stored address so it still resolves rather than failing.
    if shop.external_ref is None:
        return shop.address, None
    return address, shop.external_ref


@router.post("/orders/batch", response_model=ClientOrderBatchResult)
async def submit_orders_batch(
    body: ClientOrderBatchBody,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> ClientOrderBatchResult:
    """Several orders at once - the paste path (§2.2 principle 5).

    "A dispatcher with six orders pastes six lines. Parse them, show what was
    understood, let them fix it."

    **Not all-or-nothing, deliberately.** Each row is ingested independently and
    reported on independently, so one unfindable address among six does not
    discard the five that were fine - the dispatcher fixes that line and
    resubmits it alone. The CSV adapter has the same requirement written as
    "never silently drop a row", and this is that rule applied a step earlier.

    Rows share a pickup and a deadline, which is what makes this fast: a
    dispatcher sending six deliveries is almost always sending them from one
    place with one urgency. Anything genuinely per-order stays on the row.

    Latency worth knowing about: every genuinely NEW address costs a geocoder
    call, and the pilot provider allows one per second (app/geocoding/nominatim.py).
    A paste of previously-seen addresses returns immediately from cache; a paste
    of entirely new ones takes roughly a second per row. That ceiling is a real
    argument for a keyed provider, not a reason to cap lower than 25.
    """
    client_row = await _require_approved_client(session, client)
    pickup_address, shop_external_ref = await _resolve_pickup(
        session, client_row, shop_id=body.pickup_shop_id, typed_address=body.pickup_address
    )

    queue = HoldQueueStore()
    geocoder = get_geocoder()
    results: list[ClientOrderBatchRowResult] = []

    for index, row in enumerate(body.rows):
        reference = (row.reference or "").strip() or _generated_reference()
        lmx = LMXOrder(
            source_system="client_portal",
            source_order_ref=reference,
            hub_id=str(client_row.hub_id),
            client_id=str(client_row.id),
            shop_external_ref=shop_external_ref,
            pickup_address=pickup_address,
            drop_address_raw=row.drop_address,
            drop_contact_name=row.drop_contact_name,
            sla_owner="LMX",
            received_at=datetime.now(timezone.utc),
            raw_payload=deadline_payload_flags(body.deadline),
        )

        try:
            order = await ingest_lmx_order(session, queue, lmx, geocoder=geocoder)
        except (OriginUnresolvableError, DestinationUnresolvableError) as exc:
            # A bad pickup fails every row identically, which is correct - the
            # pickup is shared - and the dispatcher sees it on every line rather
            # than having to infer it.
            results.append(
                ClientOrderBatchRowResult(
                    index=index, drop_address=row.drop_address, error=_address_error_detail(exc)
                )
            )
            continue
        except ShopNotFoundError:
            raise HTTPException(status_code=404, detail="Pickup location not found") from None
        except Exception:  # noqa: BLE001
            # One malformed row must not take down the rest of a dispatcher's
            # paste. Logged with the index so the cause is findable, reported
            # generically because whatever it was isn't the client's to debug.
            logger.exception("batch_order_row_failed", row_index=index)
            results.append(
                ClientOrderBatchRowResult(
                    index=index,
                    drop_address=row.drop_address,
                    error="Something went wrong with this line - please try it on its own.",
                )
            )
            continue

        results.append(
            ClientOrderBatchRowResult(
                index=index,
                drop_address=row.drop_address,
                order=ClientOrderResult(
                    order_id=str(order.id),
                    reference=reference,
                    status=order.status.value,
                    sla_tier=order.sla_tier,
                    collect_by=order.hold_deadline,
                    estimated_delivery_by=await _estimate_delivery_by(session, order),
                    fee_cents=order.fee_cents,
                    dispatchable=order.delivery_lat is not None and order.delivery_lng is not None,
                ),
            )
        )

    accepted = sum(1 for r in results if r.order is not None)
    logger.info(
        "client_orders_batch_submitted",
        client_id=str(client_row.id),
        rows=len(body.rows),
        accepted=accepted,
        failed=len(results) - accepted,
        deadline_choice=body.deadline,
        used_remembered_shop=body.pickup_shop_id is not None,
        entry_seconds=body.entry_seconds,
    )
    return ClientOrderBatchResult(
        accepted=accepted, failed=len(results) - accepted, results=results
    )


@router.post("/orders", response_model=ClientOrderResult, status_code=201)
async def submit_order(
    body: ClientOrderBody,
    client: AuthedClient = Depends(get_current_client),
    session: AsyncSession = Depends(get_db),
) -> ClientOrderResult:
    """Submit an order.

    Runs the same ingestion path as an Epicor webhook - normalize, resolve or
    create the pickup, classify, land in the batch-hold queue. Nothing
    downstream knows this came from a person rather than a POS, which is §1.1's
    rule working as intended.

    The client's deadline choice becomes an urgency flag on the payload and is
    classified by the existing SLA engine, rather than letting them name a tier.
    LMX owns the commitment (§1.3): a customer says how urgent it is, we decide
    what that means.
    """
    client_row = await _require_approved_client(session, client)

    pickup_address, shop_external_ref = await _resolve_pickup(
        session, client_row, shop_id=body.pickup_shop_id, typed_address=body.pickup_address
    )

    reference = (body.reference or "").strip() or _generated_reference()

    lmx = LMXOrder(
        source_system="client_portal",
        source_order_ref=reference,
        hub_id=str(client_row.hub_id),
        client_id=str(client_row.id),
        shop_external_ref=shop_external_ref,
        pickup_address=pickup_address,
        pickup_contact_name=body.pickup_contact_name,
        pickup_contact_phone=body.pickup_contact_phone,
        drop_address_raw=body.drop_address,
        drop_contact_name=body.drop_contact_name,
        drop_contact_phone=body.drop_contact_phone,
        access_notes=body.access_notes,
        sla_owner="LMX",
        total_weight_units=body.total_weight_units,
        line_items=[
            LineItem(description=li.description, quantity=li.quantity) for li in body.line_items
        ],
        received_at=datetime.now(timezone.utc),
        # The deadline choice, expressed as the urgency flags the SLA engine
        # already reads. Not a tier the client picked.
        raw_payload=deadline_payload_flags(body.deadline),
    )

    try:
        order = await ingest_lmx_order(session, HoldQueueStore(), lmx, geocoder=get_geocoder())
    except (OriginUnresolvableError, DestinationUnresolvableError) as exc:
        # Refusing beats accepting something undeliverable - and the client can
        # fix a typo immediately, which nobody can do once a driver is standing
        # in the wrong street. The message names WHICH address, because "we
        # couldn't find that address" is useless if it doesn't say.
        raise HTTPException(status_code=422, detail=_address_error_detail(exc)) from exc
    except ShopNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Pickup location not found") from exc

    # The §3.4 entry-time target, measured rather than asserted. Logged as
    # structured data so "under 30 seconds from the second order onward" is
    # answerable from real counter use instead of a demo stopwatch.
    logger.info(
        "client_order_submitted",
        order_id=str(order.id),
        client_id=str(client_row.id),
        deadline_choice=body.deadline,
        sla_tier=order.sla_tier,
        used_remembered_shop=body.pickup_shop_id is not None,
        entry_seconds=body.entry_seconds,
    )

    return ClientOrderResult(
        order_id=str(order.id),
        reference=reference,
        status=order.status.value,
        sla_tier=order.sla_tier,
        collect_by=order.hold_deadline,
        estimated_delivery_by=await _estimate_delivery_by(session, order),
        fee_cents=order.fee_cents,
        dispatchable=order.delivery_lat is not None and order.delivery_lng is not None,
    )


async def _estimate_delivery_by(session: AsyncSession, order: Order) -> datetime | None:
    """A rough delivery time for the confirmation - an ESTIMATE, not a promise.

    §2.2 principle 6 wants the confirmation to show a commitment rather than a
    spinner, and a collect-by time alone reads as half an answer. But there is
    no verified travel-time model here: the real routing integration has never
    made a live call (E1, blocked on a Google Cloud account). So this is
    straight-line distance at the same placeholder average speed the gig
    accept-gate uses, and the field it populates is named
    `estimated_delivery_by` rather than `delivery_by` on purpose.

    Returns None when the drop hasn't been geocoded - guessing without a
    destination would be inventing twice over.
    """
    if order.hold_deadline is None or order.delivery_lat is None or order.delivery_lng is None:
        return None
    # Pickup coordinates live on the Shop, not the Order - that Shop-dependency
    # is the same one documented in app/ingestion/service.py.
    shop = await session.get(Shop, order.shop_id) if order.shop_id else None
    if shop is None:
        return None
    miles = miles_between(shop.lat, shop.lng, float(order.delivery_lat), float(order.delivery_lng))
    return order.hold_deadline + timedelta(minutes=minutes_for_miles(miles))
