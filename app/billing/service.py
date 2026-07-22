"""
Statement generation (docs/ROADMAP.md C3) - sweeps a client's delivered,
priced, not-yet-billed orders in a date range into a new Invoice. Called
from the admin-triggered POST /admin/clients/{client_id}/invoices/generate
(app/api/admin_routes.py) - same "manual trigger today, a real scheduler's
hook later" shape as run_payroll_for_hub/run_learning_loop_nightly_job,
since there's no billing-cycle scheduler yet either.

Payment collection is explicitly out of scope here - this only produces a
statement of what's owed, the same class of deliberate scope cut as
Rippling being gated on a real account (B4) before real money moves for
driver payroll.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.schemas.billing import InvoiceDetailView, InvoiceLineItem, InvoiceSummaryView

logger = structlog.get_logger(__name__)


class NoBillableOrdersError(Exception):
    pass


async def generate_invoice(
    session: AsyncSession, client_id: uuid.UUID, period_start: date, period_end: date
) -> Invoice:
    """period_end is exclusive - a delivery that lands exactly on it belongs
    to the *next* statement, not this one, so consecutive periods never
    overlap or double-count a drop."""
    period_start_dt = datetime.combine(period_start, datetime.min.time(), tzinfo=timezone.utc)
    period_end_dt = datetime.combine(period_end, datetime.min.time(), tzinfo=timezone.utc)

    # updated_at as a "delivered at" proxy - no dedicated column exists yet,
    # same convention app/api/client_routes.py's _order_summary_view already
    # uses for the same reason (docs/NEXT_STEPS.md's gap list).
    candidates_result = await session.execute(
        select(Order).where(
            Order.client_id == client_id,
            Order.status == OrderStatus.delivered,
            Order.invoice_id.is_(None),
            Order.updated_at >= period_start_dt,
            Order.updated_at < period_end_dt,
        )
    )
    candidates = list(candidates_result.scalars().all())

    # A null fee_cents means no ClientRate was configured for that order's
    # tier at ingestion time (see Order.fee_cents's docstring) - excluded
    # here rather than billed as $0, and logged loudly since an order
    # silently never being billed is exactly the kind of thing that should
    # be visible, not just quietly correct.
    billable = [o for o in candidates if o.fee_cents is not None]
    unpriced = [o for o in candidates if o.fee_cents is None]
    if unpriced:
        logger.warning(
            "invoice_generation_skipped_unpriced_orders",
            client_id=str(client_id),
            order_ids=[str(o.id) for o in unpriced],
        )

    if not billable:
        raise NoBillableOrdersError(
            f"No delivered, priced orders for client {client_id} between {period_start} and {period_end}"
        )

    invoice = Invoice(
        # invoice_number deliberately unset here - the migration's
        # server_default nextval('invoice_number_seq') assigns it on
        # INSERT. Setting it explicitly (even to a placeholder) would
        # include the column in the INSERT and bypass that default.
        client_id=client_id,
        period_start=period_start,
        period_end=period_end,
        total_cents=sum(o.fee_cents for o in billable),
    )
    session.add(invoice)
    await session.flush()  # need invoice.id to attach orders below

    # A plain ORM attribute assignment (order.invoice_id = invoice.id) won't
    # do here: re-asserting order.updated_at to its own already-loaded value
    # in the same flush doesn't count as a "real" change to SQLAlchemy's
    # dirty-tracking (old == new), so it gets left out of the UPDATE's SET
    # clause and the column's onupdate=func.now() default fires anyway -
    # silently replacing this order's real delivered-at proxy with
    # "whenever this invoice happened to be generated." A Core-level
    # update() has no such dirty-tracking - every value passed to .values()
    # is included in the SET clause unconditionally, which is exactly what's
    # needed to keep updated_at pinned to what it already was.
    for order in billable:
        await session.execute(
            sa_update(Order).where(Order.id == order.id).values(invoice_id=invoice.id, updated_at=order.updated_at)
        )

    await session.commit()
    await session.refresh(invoice)
    return invoice


async def invoice_line_items(session: AsyncSession, invoice: Invoice) -> list[tuple[Order, str | None]]:
    """Every order this invoice billed, paired with its shop name - the
    line items for both the admin and client-facing detail views."""
    orders_result = await session.execute(select(Order).where(Order.invoice_id == invoice.id))
    orders = list(orders_result.scalars().all())
    if not orders:
        return []

    shop_ids = {o.shop_id for o in orders}
    shops_result = await session.execute(select(Shop).where(Shop.id.in_(shop_ids)))
    shop_names = {s.id: s.name for s in shops_result.scalars().all()}
    return [(order, shop_names.get(order.shop_id)) for order in orders]


def _summary_view(invoice: Invoice, order_count: int) -> InvoiceSummaryView:
    return InvoiceSummaryView(
        invoice_id=str(invoice.id),
        invoice_number=invoice.invoice_number,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        generated_at=invoice.created_at.isoformat(),
        total_cents=invoice.total_cents,
        order_count=order_count,
    )


async def invoice_summary_view(session: AsyncSession, invoice: Invoice) -> InvoiceSummaryView:
    count_result = await session.execute(select(Order.id).where(Order.invoice_id == invoice.id))
    return _summary_view(invoice, order_count=len(count_result.all()))


async def invoice_detail_view(session: AsyncSession, invoice: Invoice) -> InvoiceDetailView:
    """The one place both app/api/admin_routes.py and app/api/client_routes.py
    build an invoice's full detail view - an invoice looks identical to
    both audiences, only who's allowed to request which one differs, so
    this shouldn't be written twice."""
    items = await invoice_line_items(session, invoice)
    return InvoiceDetailView(
        **_summary_view(invoice, order_count=len(items)).model_dump(),
        line_items=[
            InvoiceLineItem(
                order_id=str(order.id),
                external_order_ref=order.external_order_ref,
                shop_name=shop_name,
                sla_tier=order.sla_tier,
                delivered_at=order.updated_at.isoformat(),
                fee_cents=order.fee_cents,
            )
            for order, shop_name in items
        ],
    )
