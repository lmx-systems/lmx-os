"""Dispatch gives an order to a driver by hand.

The exceptions queue's "nobody has it" row said *"nobody has this order -
assign it or put it back in the queue"*, and nothing could assign it. The cycle
had released the order and found nobody it could place it with, or a dispatcher
had released it and the cycle still found nobody; either way the dispatcher
could see a driver who would take it and had no way to say so.

## An offer, the way the cycle makes one

An idle driver (fleet status `available`, no route) is sent a `RouteOffer`,
exactly as the cycle sends one, and may decline it. Only `accept_offer` builds a
Route, texts the PINs and tells the shop (`app/api/driver_routes.py`); forcing a
route on a driver would mean a second copy of all of that. A decline or a lapse
puts the order back in the hold queue the way every offer's does
(`app/orders/requeue.py`).

**A driver already on a route is refused, for now.** Adding the order after
their last stop - what the cycle's in-flight insertion does for a passing
driver - is the other half of the design doc's §6 answer, and it is left out on
purpose: an appender writes a route a driver is tapping through, and doing that
safely beside the cycle needs the cycle's own writes hardened first
(`docs/ROADMAP_1.5.md`, `CON-5`). Every other status (`off_shift`, `on_break`,
`offered`) is refused too: the driver is not there to take it, or already has a
question in front of them.

## Two writers, one order and one driver

The cycle offers beside this without a shared lock, so each thing both could
write is written once:

  - **The order** is read `FOR UPDATE` here and held to the commit. The cycle's
    status write moves an order only if it is still held or queued
    (`advance_orders(only_from=...)`): queued behind this one, it re-checks
    after the commit, matches nothing, and the cycle does not offer the order. Before, it wrote
    `assigned` over `assigned` and offered the order to a second driver.
  - **The driver** is claimed with `FleetStateManager.claim_for_offer`, which
    reads `available` and marks `offered` in one Redis script. The cycle claims
    the same way, so only one of the two offers them anything, and a break the
    driver takes in between is never overwritten.
  - **The hub** is locked for the assign, as every dispatch cycle and the Redis
    rebuild lock it (`app/events/bus.py`'s `hub_lock`). The rebuild reads the
    open offers once and then corrects each `offered` driver it finds with
    none: an assign committed between the two had its driver put back in the
    pool, and the sweep's cycle that follows offered them a second job. A
    cycle mid-run makes the assign wait a moment, then refuse.

## What refuses, and what only informs

Refused, because each is a fact a dispatcher cannot wish away: the order is not
waiting for a driver; it was imported as history (`intake_mode` backfill: held
on purpose, never queued, and never to be delivered); the driver is not at this hub, or is switched off; the hub
is closed today; the driver's documents would not let them go on shift (R4 holds
for manual work too - the optimizer never checks, but it only ever sees drivers
who passed the gate to go `available`, and an assign can reach one who did not);
the driver has no live state; the van has no room.

Not refused: a stale position. The dispatcher may have phoned the driver, and an
offer nobody answers lapses on its own. It goes back in the result, so the
console can say so.

## The record

Every assign writes a `DispatchAssignment` row in the same transaction: who,
which driver, and the offer. Without it a manual offer would be
indistinguishable from the optimizer's in every count that reads offers.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.store import HoldQueueStore
from app.compliance.driver_documents import evaluate_driver_documents
from app.config import settings
from app.events.bus import release_hub_lock, try_acquire_hub_lock
from app.fleet_state.manager import FleetStateManager
from app.hub_calendar import is_hub_closed_at
from app.messaging.job_offer_notifications import notify_driver_of_new_offer
from app.models.dispatch_assignment import DispatchAssignment
from app.models.driver import Driver
from app.models.order import INTAKE_LIVE, Order, OrderStatus
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.optimizer.event_trigger import dispatch_event_bus
from app.optimizer.service import _position_is_stale
from app.orders.state_machine import TERMINAL
from app.orders.status_service import advance_orders

logger = structlog.get_logger(__name__)

# Waiting for a driver. Both move to `assigned` legally; nothing else here does
# without undoing something first.
ASSIGNABLE_STATUSES = (OrderStatus.held, OrderStatus.queued)

# A driver already has it, in one of the ways a driver can.
_WITH_A_DRIVER = (
    OrderStatus.assigned,
    OrderStatus.accepted,
    OrderStatus.en_route_pickup,
    OrderStatus.picked_up,
    OrderStatus.en_route_drop,
)

# The optimizer's per-order unit (`StopCandidate.weight_units` in plan_cycle).
# Orders carry no weight the cycle reads yet, so a manual assign judges room
# the same way rather than more strictly than the automatic path.
ORDER_WEIGHT_UNITS = 1.0

# How long an assign waits for a dispatch cycle (or the rebuild) to let go of
# the hub before saying so. A cycle is budgeted at a few seconds; a dispatcher
# would rather press again than watch a spinner for longer.
HUB_LOCK_WAIT_SECONDS = 3.0
_HUB_LOCK_POLL_SECONDS = 0.1


class AssignmentRefused(Exception):
    """Why this order can't go to this driver, with the HTTP status to say it with."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AssignmentOutcome:
    order_id: str
    driver_id: str
    offer_id: str
    offer_expires_at: datetime
    driver_position_stale: bool = False


def why_not_assignable(order: Order) -> str:
    if order.status in _WITH_A_DRIVER:
        return "A driver already has this order."
    if order.status == OrderStatus.delivery_failed:
        return "Resolve the failed delivery first - redeliver puts it back in the queue."
    if order.status in TERMINAL:
        return f"This order is {order.status.value}."
    return "Not in the hold queue yet."


@contextlib.asynccontextmanager
async def _hub_locked(hub_id: str) -> AsyncIterator[None]:
    """The hub's dispatch lock for the assign, or a refusal once it has waited
    `HUB_LOCK_WAIT_SECONDS`. Released before the assign's own notices go out,
    so the cycle its event wakes can take it."""
    deadline = time.monotonic() + HUB_LOCK_WAIT_SECONDS
    while (token := await try_acquire_hub_lock(hub_id)) is None:
        if time.monotonic() >= deadline:
            raise AssignmentRefused(409, "Dispatch is running for this hub right now - try again in a moment.")
        await asyncio.sleep(_HUB_LOCK_POLL_SECONDS)
    try:
        yield
    finally:
        await release_hub_lock(hub_id, token)


async def assign_order_to_driver(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    driver_id: uuid.UUID,
    ops_user_id: str,
    ops_user_email: str,
    hold_queue: HoldQueueStore | None = None,
    fleet: FleetStateManager | None = None,
) -> AssignmentOutcome:
    """Offer `order_id` to `driver_id`, or raise `AssignmentRefused`.

    Commits. The hold-queue removal and the driver's notice follow the commit,
    in the order cancel uses (app/orders/cancellation.py): an order taken out of
    Redis with its row still `held` would never be dispatched and never be
    seen, while a row that is `assigned` and still in Redis is planned by the
    next cycle, skipped by its conditional write, and removed.
    """
    hold_queue = hold_queue or HoldQueueStore()
    fleet = fleet or FleetStateManager()

    # The hub first, unlocked: it is the lock's name, and an order never
    # changes hub. Everything that decides the assign is read under the lock.
    hub_uuid = await session.scalar(select(Order.hub_id).where(Order.id == order_id))
    if hub_uuid is None:
        raise AssignmentRefused(404, "No such order")
    async with _hub_locked(str(hub_uuid)):
        offered = await _check_and_offer(
            session, fleet, order_id=order_id, driver_id=driver_id,
            ops_user_id=ops_user_id, ops_user_email=ops_user_email,
        )
    return await _after_commit(hold_queue, fleet, offered)


@dataclass(frozen=True)
class _Offered:
    hub_id: str
    order_id: str
    driver_id: str
    offer_id: str
    expires_at: datetime
    at: datetime


async def _check_and_offer(
    session: AsyncSession,
    fleet: FleetStateManager,
    *,
    order_id: uuid.UUID,
    driver_id: uuid.UUID,
    ops_user_id: str,
    ops_user_email: str,
) -> _Offered:
    """Every check, then the offer, committed. Run under the hub lock."""
    # Locked, and read fresh: the cycle and a driver's decline write the status
    # from other sessions, and two dispatchers can press Assign at once.
    order = (
        await session.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        raise AssignmentRefused(404, "No such order")
    if order.status not in ASSIGNABLE_STATUSES:
        raise AssignmentRefused(409, why_not_assignable(order))
    if order.intake_mode != INTAKE_LIVE:
        # History, loaded by a backfill: held on purpose and never queued
        # (app/ingestion/service.py). An offer would send a driver to collect a
        # delivery that already happened, and tell the client it was assigned.
        raise AssignmentRefused(
            422, "This order was imported as history, so there is nothing to deliver."
        )

    # Share-locked to the commit, as the cycle's offers take it: a switch-off
    # (which takes the row for update) either lands first and is refused here,
    # or waits and then sees this offer.
    driver = (
        await session.execute(
            select(Driver).where(Driver.id == driver_id).with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if driver is None or driver.hub_id != order.hub_id:
        raise AssignmentRefused(404, "No such driver at this hub")
    if not driver.is_active:
        raise AssignmentRefused(409, f"{driver.name} is switched off.")

    hub_id = str(order.hub_id)
    now = datetime.now(timezone.utc)
    if await is_hub_closed_at(session, hub_id, now):
        raise AssignmentRefused(409, "The hub is closed today, so nothing is being dispatched.")

    compliance = await evaluate_driver_documents(session, str(driver_id))
    if not compliance.can_go_on_shift:
        # The same sentence the driver's own app gets when the gate refuses
        # them (app/api/driver_routes.py), every problem at once.
        raise AssignmentRefused(409, "; ".join(problem.detail for problem in compliance.problems))

    state = await fleet.get_driver_state(hub_id, str(driver_id))
    if state is None:
        raise AssignmentRefused(409, "This driver has no live state - they are not on shift.")

    if state.status == "en_route":
        raise AssignmentRefused(
            409,
            f"{driver.name} is on a route. Adding to a route by hand isn't built yet - "
            "pick an idle driver, or leave it for the next cycle's in-flight insertion.",
        )
    if state.status != "available" or state.current_route_id:
        status = state.status.replace("_", " ")
        if state.status == "available":
            # Available with a route still named: the route is closing or
            # stale, and an offer accepted now would give them a second one.
            status = "finishing a route"
        raise AssignmentRefused(409, f"{driver.name} is {status} and can't take work right now.")

    if state.capacity_units - state.load_units < ORDER_WEIGHT_UNITS:
        raise AssignmentRefused(409, f"No room on {driver.name}'s van.")

    shop = await session.get(Shop, order.shop_id) if order.shop_id is not None else None
    if shop is None:
        # 422, as resolve's missing shop is: a broken row, not a state the
        # dispatcher can change by waiting.
        raise AssignmentRefused(422, "This order has no shop on file, so there is nowhere to collect it.")

    return await _offer(
        session, fleet, order=order, driver=driver, shop=shop,
        ops_user_id=ops_user_id, ops_user_email=ops_user_email, now=now,
    )


async def _offer(
    session: AsyncSession,
    fleet: FleetStateManager,
    *,
    order: Order,
    driver: Driver,
    shop: Shop,
    ops_user_id: str,
    ops_user_email: str,
    now: datetime,
) -> _Offered:
    """An offer to an idle driver, shaped as the cycle shapes one.

    `visit_payload` is left empty: with one order there is no sequence to plan,
    and accept builds pickup-then-drop from the order alone
    (app/models/route_offer.py).
    """
    hub_id, order_id, driver_id = str(order.hub_id), str(order.id), str(driver.id)
    tier = str(getattr(order.sla_tier, "value", order.sla_tier) or "")
    moved = await advance_orders(session, [order.id], OrderStatus.assigned, occurred_at=now)
    if not moved:
        raise AssignmentRefused(409, why_not_assignable(order))
    order.assigned_at = now

    expires_at = now + timedelta(seconds=settings.job_offer_ttl_seconds)
    offer = RouteOffer(
        hub_id=order.hub_id,
        driver_id=driver.id,
        status="offered",
        stop_payload=[
            {
                "order_id": order_id,
                "lat": float(shop.lat),
                "lng": float(shop.lng),
                "sla_tier": tier,
                "shop_name": shop.name or "",
            }
        ],
        visit_payload=None,
        offered_at=now,
        expires_at=expires_at,
    )
    session.add(offer)
    await session.flush()
    offer_id = str(offer.id)
    session.add(
        DispatchAssignment(
            hub_id=order.hub_id, order_id=order.id, driver_id=driver.id, assigned_at=now,
            offer_id=offer.id,
            ops_user_id=ops_user_id, ops_user_email=ops_user_email,
        )
    )

    # Claimed, not re-read: the checks above read the driver a moment ago, and
    # in that moment they may have gone on break. The claim reads and marks
    # `offered` in one step, as the cycle's offers do
    # (app/fleet_state/manager.py), so the two can't both offer one driver.
    claim = await fleet.claim_for_offer(hub_id, driver_id)
    if claim is None:
        await session.rollback()
        raise AssignmentRefused(409, "The driver's status changed - look again.")
    before, _ = claim
    if before.current_route_id:
        # Available with a route still named, which the check above refused;
        # it changed in between. Not an offer to make.
        await fleet.give_back_claim(before)
        await session.rollback()
        raise AssignmentRefused(409, "The driver's status changed - look again.")
    try:
        await session.commit()
    except Exception:
        # Nothing was offered, so the driver goes back to where they were -
        # unless they have since chosen somewhere else (`give_back_claim`).
        await fleet.give_back_claim(before)
        raise
    return _Offered(
        hub_id=hub_id, order_id=order_id, driver_id=driver_id, offer_id=offer_id,
        expires_at=expires_at, at=now,
    )


async def _after_commit(
    hold_queue: HoldQueueStore, fleet: FleetStateManager, offered: _Offered
) -> AssignmentOutcome:
    """The notices, once the offer is committed and the hub lock released."""
    hub_id, order_id, driver_id = offered.hub_id, offered.order_id, offered.driver_id
    await hold_queue.remove(hub_id, order_id)
    # The driver left the assignable pool, as accept and decline announce theirs.
    await dispatch_event_bus.publish(hub_id, "driver_status_changed")
    await notify_driver_of_new_offer(driver_id, 1, settings.job_offer_ttl_seconds)

    location = await fleet.get_driver_location(hub_id, driver_id)
    stale = location is None or _position_is_stale(location.recorded_at, offered.at)
    logger.info(
        "order_offered_by_dispatch",
        order_id=order_id, driver_id=driver_id, offer_id=offered.offer_id, hub_id=hub_id,
        driver_position_stale=stale,
    )
    return AssignmentOutcome(
        order_id=order_id,
        driver_id=driver_id,
        offer_id=offered.offer_id,
        offer_expires_at=offered.expires_at,
        driver_position_stale=stale,
    )
