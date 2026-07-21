"""
Monthly billing statements (roadmap item C3, first slice).

Assembles a client's statement for one calendar month from the per-order
fee_cents Phase 8 already computes at ingestion. Scope is deliberate:
statement assembly + invoice PDF only - no payment collection (needs a
processor decision) and no statement persistence (statements are derived
data; regenerating from orders is always correct, and a `statements`
table would just be a cache to keep consistent).

Two rules carried over from Phase 8's billing design:
- Only DELIVERED orders bill. Cancelled/failed/in-flight orders never
  appear on a statement.
- fee_cents NULL (no rate configured for that tier when the order was
  ingested) is surfaced as an explicit "unbilled" count - never silently
  treated as $0, so a rate-configuration gap looks like a problem to fix,
  not free deliveries.

"Delivered in month M" uses Order.updated_at as the delivery-time proxy -
same explicit trade-off as the client portal's delivered_at field
(app/api/client_routes.py); a dedicated delivered_at column is a known
follow-up that slots in here with a one-line change.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client
from app.models.order import Order, OrderStatus

SLA_TIER_DISPLAY_ORDER = ["HOT_SHOT", "T1", "T2", "T3"]


@dataclass
class StatementLine:
    sla_tier: str
    rate_per_drop_cents: int
    order_count: int
    subtotal_cents: int


@dataclass
class Statement:
    client_id: str
    client_name: str
    year: int
    month: int
    lines: list[StatementLine] = field(default_factory=list)
    total_cents: int = 0
    delivered_order_count: int = 0
    # Delivered orders with no fee configured - flagged, never $0.
    unbilled_order_count: int = 0


class ClientNotFoundError(Exception):
    pass


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(
        year, month + 1, 1, tzinfo=timezone.utc
    )
    return start, end


def build_lines(orders: list[Order]) -> tuple[list[StatementLine], int, int]:
    """Pure grouping: (tier, rate) -> line. Returns (lines, total_cents,
    unbilled_count). Grouped by rate as well as tier so a mid-month rate
    change shows as two honest lines instead of one wrong average."""
    grouped: dict[tuple[str, int], int] = {}
    unbilled = 0
    for order in orders:
        if order.fee_cents is None:
            unbilled += 1
            continue
        key = (order.sla_tier, order.fee_cents)
        grouped[key] = grouped.get(key, 0) + 1

    lines = [
        StatementLine(
            sla_tier=tier,
            rate_per_drop_cents=rate,
            order_count=count,
            subtotal_cents=rate * count,
        )
        for (tier, rate), count in grouped.items()
    ]
    lines.sort(
        key=lambda line: (
            SLA_TIER_DISPLAY_ORDER.index(line.sla_tier)
            if line.sla_tier in SLA_TIER_DISPLAY_ORDER
            else len(SLA_TIER_DISPLAY_ORDER),
            -line.rate_per_drop_cents,
        )
    )
    total = sum(line.subtotal_cents for line in lines)
    return lines, total, unbilled


async def build_statement(
    session: AsyncSession, client_id: str, year: int, month: int
) -> Statement:
    client = await session.get(Client, uuid.UUID(client_id))
    if client is None:
        raise ClientNotFoundError(client_id)

    start, end = month_bounds(year, month)
    result = await session.execute(
        select(Order).where(
            Order.client_id == uuid.UUID(client_id),
            Order.status == OrderStatus.delivered,
            Order.updated_at >= start,
            Order.updated_at < end,
        )
    )
    orders = list(result.scalars().all())
    lines, total, unbilled = build_lines(orders)

    return Statement(
        client_id=client_id,
        client_name=client.name,
        year=year,
        month=month,
        lines=lines,
        total_cents=total,
        delivered_order_count=len(orders),
        unbilled_order_count=unbilled,
    )
