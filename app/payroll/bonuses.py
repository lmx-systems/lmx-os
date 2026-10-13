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

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route
from app.models.route_offer import RouteOffer
from app.models.stop import Stop, StopOrder


async def offer_bonuses_owed_cents(
    session: AsyncSession, *, driver_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    """The bonuses owed to `driver_id` for drop-offs they completed in [start, end).

    Owed for the delivery the offer was for, made by the driver who took it: the
    order's drop-off completed on the route that accepting the offer built, by
    this driver. Not merely "the order was delivered" - after a failed drop-off
    and a redelivery somebody else may deliver it, and the bonus is not theirs
    to collect twice nor this driver's. Each offer is counted once, in the period
    its drop-off was completed.
    """
    order_on_offer = RouteOffer.stop_payload.contains(
        func.jsonb_build_array(func.jsonb_build_object("order_id", cast(StopOrder.order_id, Text)))
    )
    delivered = (
        select(RouteOffer.id, RouteOffer.bonus_cents)
        .join(Route, Route.id == RouteOffer.route_id)
        .join(Stop, Stop.route_id == Route.id)
        .join(StopOrder, StopOrder.stop_id == Stop.id)
        .where(
            RouteOffer.driver_id == driver_id,
            RouteOffer.status == "accepted",
            RouteOffer.bonus_cents.is_not(None),
            Route.driver_id == driver_id,
            Stop.stop_type == "dropoff",
            Stop.status == "completed",
            Stop.completed_at >= start,
            Stop.completed_at < end,
            order_on_offer,
        )
        .distinct()
    )
    return sum(bonus or 0 for _, bonus in (await session.execute(delivered)).all())
