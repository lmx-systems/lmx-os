"""
Cash on delivery: collecting it, and refusing to negotiate over it
(docs/ROADMAP.md W2, story DO-8, training case E3).

The roadmap states the driver rule and then states the requirement about it:

    *"never negotiate, one tap escalates to the distributor, keep moving"* - and it
    **must be enforced by the UI, not by training alone.**

**This module is what "enforced, not trained" actually means here, and it is one
decision: there is no amount field.** `record_collection` takes no figure to type into;
it records that the full amount due was taken, or it is not called. A driver facing a
customer who offers eighty dollars against a hundred-dollar invoice has exactly two
paths, collect-in-full or escalate, because a third was never built.

That is not paternalism about drivers. **The money is the DISTRIBUTOR'S**, an invoice
between them and their own customer that LMX is carrying. Nobody at LMX has authority to
discount it, so a field allowing it would hand a driver an authority they were never
given, over a sum we are not a party to - and leave them arguing at a door on someone
else's behalf, which is the situation the rule exists to get them out of.

The other half is that a COD stop **cannot be completed silently**. Before this, a
driver could mark a COD delivery done with no record of any money changing hands, and
nothing anywhere would notice. `assert_cod_settled` is what makes "delivered" mean
"delivered and settled, or escalated".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cod_collection import (
    COD_METHODS,
    OUTCOME_COLLECTED,
    OUTCOME_DISPUTED,
    CodCollection,
)
from app.models.order import Order
from app.models.stop import StopOrder

logger = structlog.get_logger(__name__)

COD_PAYER_TYPE = "cash_on_delivery"


class CodNotSettled(Exception):
    """A COD stop with money still unaccounted for.

    Message written for the driver holding the phone: it says what to do next, not which
    invariant failed.
    """


class CodError(Exception):
    """The request doesn't make sense for this order - not COD, already settled."""


@dataclass(frozen=True)
class CodObligation:
    order_id: str
    amount_due_cents: int


async def cod_obligations(session: AsyncSession, stop_id) -> list[CodObligation]:
    """What money is owed at this stop, and for which orders.

    Only orders that are actually COD and actually carry an amount. A COD order with no
    amount is a data problem, and blocking a delivery over it would strand a driver at a
    door over something only ops can fix - it is logged and skipped, and the delivery
    proceeds.
    """
    result = await session.execute(
        select(Order)
        .join(StopOrder, StopOrder.order_id == Order.id)
        .where(StopOrder.stop_id == stop_id, Order.payer_type == COD_PAYER_TYPE)
    )
    obligations: list[CodObligation] = []
    for order in result.scalars().all():
        if not order.cod_amount_cents:
            logger.warning(
                "cod_order_without_an_amount",
                order_id=str(order.id),
                detail="treated as nothing to collect - ops must set cod_amount_cents",
            )
            continue
        obligations.append(
            CodObligation(order_id=str(order.id), amount_due_cents=order.cod_amount_cents)
        )
    return obligations


async def _settled_order_ids(session: AsyncSession, stop_id) -> set[str]:
    """Orders at this stop with a collection or a dispute already recorded.

    A dispute counts as settled FOR THE PURPOSE OF LEAVING. The rule is "keep moving" -
    once escalated, standing at the door is not the driver's job, and blocking them there
    until someone else resolves it would be the opposite of what the rule asks.
    """
    result = await session.execute(
        select(CodCollection.order_id).where(CodCollection.stop_id == stop_id)
    )
    return {str(row[0]) for row in result.all()}


async def assert_cod_settled(session: AsyncSession, stop_id) -> None:
    """Raise `CodNotSettled` if this stop owes money nobody has accounted for.

    **The teeth.** Without it a driver could complete a COD delivery with no record of
    any money changing hands, and nothing would ever notice - the parts gone, the invoice
    unpaid, and no dispute raised to explain it.
    """
    obligations = await cod_obligations(session, stop_id)
    if not obligations:
        return
    settled = await _settled_order_ids(session, stop_id)
    outstanding = [o for o in obligations if o.order_id not in settled]
    if not outstanding:
        return

    total = sum(o.amount_due_cents for o in outstanding)
    raise CodNotSettled(
        f"This delivery is cash on delivery - collect ${total / 100:.2f}, or flag a "
        f"payment dispute if the customer won't pay. Don't negotiate."
    )


async def record_collection(
    session: AsyncSession,
    *,
    order: Order,
    stop_id,
    driver_id: str,
    method: str,
) -> CodCollection:
    """Record that the FULL amount due was collected.

    **There is deliberately no amount parameter.** The figure comes off the order, so
    "collected" can only ever mean "all of it" - see the module docstring on why a field
    to type a smaller number into would be handing a driver an authority nobody has over
    money that isn't LMX's.
    """
    if order.payer_type != COD_PAYER_TYPE:
        raise CodError("This order isn't cash on delivery")
    if not order.cod_amount_cents:
        raise CodError("No amount is set on this order - LMX ops needs to fix that")
    if method not in COD_METHODS:
        raise CodError(f"Payment must be one of: {', '.join(COD_METHODS)}")

    existing = await _existing(session, order.id, stop_id)
    if existing is not None:
        # Idempotent for the same outcome, so a retried tap on a bad connection doesn't
        # look like a second payment; a conflict otherwise, because "collected" and
        # "disputed" cannot both be true of the same money.
        if existing.outcome == OUTCOME_COLLECTED:
            return existing
        raise CodError("A payment dispute was already raised for this order")

    collection = CodCollection(
        order_id=order.id,
        stop_id=stop_id,
        driver_id=uuid.UUID(driver_id),
        client_id=order.client_id,
        shop_id=order.shop_id,
        outcome=OUTCOME_COLLECTED,
        amount_due_cents=order.cod_amount_cents,
        amount_collected_cents=order.cod_amount_cents,
        method=method,
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(collection)
    logger.info(
        "cod_collected",
        order_id=str(order.id),
        driver_id=driver_id,
        amount_cents=order.cod_amount_cents,
        method=method,
    )
    return collection


async def record_dispute(
    session: AsyncSession,
    *,
    order: Order,
    stop_id,
    driver_id: str,
    note: str | None,
) -> CodCollection:
    """Record that the customer wouldn't pay, and what they said.

    The escalation itself is sent by the caller after commit, so a failed SMS can't roll
    back the dispute - the dispute is the record, the message is a courtesy on top of it.
    """
    if order.payer_type != COD_PAYER_TYPE:
        raise CodError("This order isn't cash on delivery")

    existing = await _existing(session, order.id, stop_id)
    if existing is not None:
        if existing.outcome == OUTCOME_DISPUTED:
            return existing
        raise CodError("This order was already recorded as paid")

    dispute = CodCollection(
        order_id=order.id,
        stop_id=stop_id,
        driver_id=uuid.UUID(driver_id),
        client_id=order.client_id,
        shop_id=order.shop_id,
        outcome=OUTCOME_DISPUTED,
        # Recorded even though nothing was collected: the amount is what the dispute is
        # ABOUT, and a report of disputes with no sums in it can't be prioritised.
        amount_due_cents=order.cod_amount_cents or 0,
        dispute_note=(note or "").strip()[:500] or None,
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(dispute)
    logger.warning(
        "cod_disputed",
        order_id=str(order.id),
        client_id=str(order.client_id) if order.client_id else None,
        driver_id=driver_id,
        amount_cents=order.cod_amount_cents,
    )
    return dispute


async def _existing(session: AsyncSession, order_id, stop_id) -> CodCollection | None:
    result = await session.execute(
        select(CodCollection).where(
            CodCollection.order_id == order_id, CodCollection.stop_id == stop_id
        )
    )
    return result.scalar_one_or_none()
