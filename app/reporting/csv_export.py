"""
Getting a client's own delivery record out as CSV (docs/ROADMAP.md F7).

Two things make this less trivial than writing rows, and both are the kind of defect
that only shows up in someone else's spreadsheet.

**Formula injection.** A cell beginning `=`, `+`, `-`, `@`, a tab or a carriage return is
executed as a formula by Excel and Sheets when the file is opened. Several columns here
are free text somebody else typed: a delivery address, access notes, a contact name -
and, since `F13`, a **rating comment written by an unauthenticated recipient**. That last
one is the sharp case: a stranger holding a tracking link types
`=HYPERLINK("http://…","CLICK")` into a rating, and it runs on the distributor's machine
when they open their export. `safe_cell` neutralises it.

The guard applies to **text only**. Prefixing a negative number would turn `-15.5` into
something a spreadsheet reads as a string, which breaks the arithmetic the export exists
to enable - so numbers are formatted by us, never user-controlled, and pass through.

**The session outlives the endpoint.** A streaming response's generator runs *after* the
handler returns, by which point a request-scoped `Depends(get_db)` session is closed. The
generator therefore opens its own session through `session_scope()` and owns it for its
lifetime. Materialising the whole file instead would sidestep that and reintroduce the
problem `W5` fixed on the list endpoint: a client's order history only grows, and holding
all of it in memory to serialise it is the same unbounded read wearing a different hat.

Timestamps are ISO 8601 in UTC. A spreadsheet re-parsing a locale-formatted date is how
an export silently changes the data it was supposed to preserve.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import structlog
from sqlalchemy import select

from app.db import session_scope
from app.models.delivery_rating import RECIPIENT, DeliveryRating
from app.models.order import Order
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder

logger = structlog.get_logger(__name__)

# Leading characters a spreadsheet treats as the start of a formula. Tab and carriage
# return are included because they are stripped on parse and can re-expose the character
# behind them.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

COLUMNS = [
    "your_reference",
    "lmx_reference",
    "status",
    "service_tier",
    "shop",
    "delivery_address",
    "delivery_contact",
    "requested_at_utc",
    "collect_by_utc",
    "collected_at_utc",
    "promised_delivery_by_utc",
    "delivered_at_utc",
    "delivery_attempts",
    "failure_reason",
    "fee_cents",
    "recipient_rating",
    "recipient_comment",
]


def safe_cell(value: str | None) -> str:
    """Neutralise a text cell that a spreadsheet would execute as a formula.

    A leading apostrophe is the conventional fix: Excel and Sheets both treat it as "this
    is text", strip it on display, and leave the value readable. The alternative -
    dropping or escaping the character - changes what the client typed, and an export
    that quietly alters an address is worse than one that shows a stray quote.

    Text only. See the module docstring on why numbers must not be touched.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _row(
    order: Order,
    shop_name: str | None,
    rating: DeliveryRating | None,
    promised: datetime | None,
    collected_at: datetime | None,
) -> list[str]:
    return [
        safe_cell(order.source_order_ref),
        safe_cell(order.external_order_ref),
        safe_cell(order.status.value if hasattr(order.status, "value") else order.status),
        safe_cell(getattr(order.sla_tier, "value", order.sla_tier)),
        safe_cell(shop_name),
        safe_cell(order.delivery_address),
        safe_cell(order.delivery_contact_name),
        _iso(order.requested_at),
        _iso(order.hold_deadline),
        _iso(collected_at),
        _iso(promised),
        _iso(order.delivered_at),
        # Numbers, so no guard - see safe_cell.
        str(order.delivery_attempts),
        safe_cell(order.failure_reason),
        "" if order.fee_cents is None else str(order.fee_cents),
        "" if rating is None else str(rating.score),
        safe_cell(rating.comment if rating is not None else None),
    ]


async def stream_client_orders_csv(client_id: uuid.UUID) -> AsyncIterator[str]:
    """Yield a client's whole order history as CSV, a chunk at a time.

    Opens its own session rather than borrowing the request's - see the module docstring.
    Ordered oldest first, because an export is read as a ledger rather than as a feed and
    appending to a previous export should line up.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def _drain() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return text

    writer.writerow(COLUMNS)
    yield _drain()

    exported = 0
    async with session_scope() as session:
        # Imported here rather than at module scope: `app/sla/commitment.py` is what makes
        # the promised column the same figure billing credits against, and importing it
        # lazily keeps this module free of a cycle if reporting ever moves.
        from app.sla.commitment import delivery_commitment, terms_for_client

        terms = await terms_for_client(session, client_id)

        shops = dict(
            (
                await session.execute(select(Shop.id, Shop.name).where(Shop.client_id == client_id))
            ).all()
        )

        # When we actually collected, from the pickup stop - the same source the portal's
        # order views read, so an export and a screen cannot disagree. Newest pickup per
        # order wins, since a re-plan can leave more than one.
        collected: dict[uuid.UUID, datetime] = {}
        for order_id, completed_at in (
            await session.execute(
                select(StopOrder.order_id, Stop.completed_at)
                .join(Stop, Stop.id == StopOrder.stop_id)
                .join(Order, Order.id == StopOrder.order_id)
                .where(
                    Order.client_id == client_id,
                    Stop.stop_type == "pickup",
                    Stop.completed_at.is_not(None),
                )
                .order_by(StopOrder.order_id, Stop.created_at.desc())
            )
        ).all():
            collected.setdefault(order_id, completed_at)

        ratings = {
            row.order_id: row
            for row in (
                await session.execute(
                    select(DeliveryRating)
                    .join(Order, Order.id == DeliveryRating.order_id)
                    .where(Order.client_id == client_id, DeliveryRating.rated_by == RECIPIENT)
                )
            )
            .scalars()
            .all()
        }

        # Server-side streaming: rows arrive in batches rather than the whole history
        # landing in memory at once.
        result = await session.stream(
            select(Order)
            .where(Order.client_id == client_id)
            .order_by(Order.requested_at.asc())
            .execution_options(yield_per=500)
        )
        async for order in result.scalars():
            commitment = delivery_commitment(order, terms.get(order.sla_tier))
            writer.writerow(
                _row(
                    order,
                    shops.get(order.shop_id),
                    ratings.get(order.id),
                    commitment.promised_delivery_by,
                    collected.get(order.id),
                )
            )
            exported += 1
            if exported % 500 == 0:
                yield _drain()

    remainder = _drain()
    if remainder:
        yield remainder
    logger.info("client_orders_exported", client_id=str(client_id), rows=exported)
