"""Offer bonuses owed to a driver (`DEC-10`, decision D-BONUS).

An offer may carry a bonus: the playbook's step-up for an order nobody has, once
it has been declined or left to lapse (`RouteOffer.bonus_cents`, a placeholder
$5). It is owed when the driver accepted that offer and the order was delivered.

**Recorded, not paid.** Drivers are paid by the hour (`app/payroll/hours.py`) and
nothing pays per job yet, so this is a figure on the driver's earnings screen,
kept apart from the hourly estimate - the same footing as the off-platform
record bonus (`gig_jobs.record_bonus_cents`). Whoever builds payouts reads it
from here.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus
from app.models.route_offer import RouteOffer


async def offer_bonuses_owed_cents(
    session: AsyncSession, *, driver_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    """The bonuses owed to `driver_id` for orders delivered in [start, end)."""
    offers = list(
        await session.scalars(
            select(RouteOffer).where(
                RouteOffer.driver_id == driver_id,
                RouteOffer.status == "accepted",
                RouteOffer.bonus_cents.is_not(None),
            )
        )
    )
    owed = 0
    for offer in offers:
        order_ids = [uuid.UUID(stop["order_id"]) for stop in offer.stop_payload if stop.get("order_id")]
        if not order_ids:
            continue
        delivered = await session.scalar(
            select(Order.id).where(
                Order.id.in_(order_ids),
                Order.status == OrderStatus.delivered,
                Order.delivered_at >= start,
                Order.delivered_at < end,
            ).limit(1)
        )
        if delivered is not None:
            owed += offer.bonus_cents or 0
    return owed
