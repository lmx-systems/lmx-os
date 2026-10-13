"""What ops should look at before the phone rings (`CON-4`).

*"Exceptions surface before the customer calls."*

`app/health/checks.py` already counts stuck orders, and that is a different
thing for a different reader: a number for a monitor, answering "is the system
healthy". This answers "which of my customers is about to ring, and what do I do
about it" - which needs the order, the customer, how long it has been, and the
next action, ordered so the top of the list is the one to pick up.

## Four ways a delivery goes wrong, and they are not equally quiet

**A driver flagged the stop.** The earliest warning there is: somebody was
physically there and the shop was shut, the gate was locked, nobody would sign.
The customer usually does not know yet. Most actionable, least urgent by the
clock, and the one an exception queue exists to catch.

**The delivery was attempted and failed.** They may already know - a van turned
up and left. `R5` resolution is the action, and until somebody takes it the
parts are in the back of a vehicle going nowhere.

**Past the promise and still moving.** The clock is running and nothing is
obviously broken, which is what makes it easy to miss until it is a call.

**Released from the hold queue and never placed.** The quietest and the worst:
no driver has it, no exception was raised, nothing is happening. It looks
identical to an order in transit from every other view in the system. Two ways
in: a dispatcher released it (`queued`) and no cycle placed it, or its hold ran
out and it is still `held` - which this list can see but not explain. Usually
cycles released it and no driver could take it; but if the cycles stopped, or the
hub is closed, the row looks the same, so the detail says only what the data
shows (`app/record/explain.py` refuses the same inference).

## Ordered by the clock, and that is a heuristic, not a prediction

The top of the list is whatever is furthest past its promise. That is a
heuristic dressed as nothing more: the model that would rank these properly is
`M2`, *P(a consequence | this order is late)*, and `M2` needs 500-1,000 observed
consequences that do not exist yet. Calling this an "urgency score" would make it
look like the thing it is standing in for, so it is called minutes and sorted
descending.

The one deliberate exception to the clock is a flagged stop, which is ranked by
its kind rather than its age: it is the earliest signal we get, and burying it
under older items defeats the point of having it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.order import Order, OrderStatus
from app.ingestion.service import open_duplicate_questions
from app.models.stop import FLAG_SOURCE_DRIVER, Stop, StopFlag, StopOrder
from app.orders.unplaced import unplaced_clause
from app.playbook.sweep import current_calls

# Ordered most-actionable-first for ties, not most-urgent-first: the clock does
# the urgency ranking and this only decides what to do when two things are
# equally late.
KIND_FLAGGED = "flagged_by_driver"
KIND_FAILED = "delivery_failed"
KIND_PAST_PROMISE = "past_promise"
KIND_UNPLACED = "released_but_unplaced"
KIND_POSSIBLE_DUPLICATE = "possible_duplicate"
KINDS = (KIND_FLAGGED, KIND_FAILED, KIND_PAST_PROMISE, KIND_UNPLACED, KIND_POSSIBLE_DUPLICATE)

# A likely repeat is the client's to answer, in the portal (`DEC-13`): before a
# driver has it, they cancel it themselves or say both are real. It becomes a
# person's only once a driver is on the way to collect it with no answer - the
# client can no longer cancel it then, and a wasted run is minutes off.
_DRIVER_ON_THE_WAY = (
    OrderStatus.assigned,
    OrderStatus.en_route_pickup,
)

_NEXT_ACTION = {
    KIND_FLAGGED: "read the driver's note and decide: retry, reschedule, or call the customer",
    KIND_FAILED: "resolve it (retry, return to shop, or cancel) - the parts are on a van going nowhere",
    KIND_PAST_PROMISE: "check the route is moving, then tell the customer before they ask",
    KIND_UNPLACED: "nobody has this order - assign it or put it back in the queue",
    KIND_POSSIBLE_DUPLICATE: "the client has not said whether it was sent twice, and a driver is on the way - call them; cancel the repeat if so",
}


@dataclass(frozen=True)
class PlaybookCall:
    """What the playbook's rule chose for this order (`DEC-8`), shown beside it.

    `outcome` is `open` - recommended, or waiting for `due_at` to act - or
    `escalated`, which is the rule saying a person decides this one.
    """

    rule: str
    action: str
    reason: str
    mode: str
    outcome: str
    due_at: datetime | None


@dataclass(frozen=True)
class Exception_:
    """One thing worth a person's attention, with enough to act on."""

    kind: str
    order_id: object
    client_id: object
    external_ref: str
    sla_tier: str | None
    # Minutes past the promise, or since the exception began when there is no
    # promise to be past. Named for what it is rather than scored, because the
    # thing that would score it is M2 and M2 is not trainable yet.
    minutes_waiting: float
    promised_at: datetime | None
    detail: str
    # What the order is now and when its hold runs out, so the console can offer
    # only what the server would accept (an assign takes `held` or `queued`; a
    # hold override only `queued`) and say when putting it back is pointless.
    status: str = ""
    hold_deadline: datetime | None = None
    playbook: PlaybookCall | None = None

    @property
    def next_action(self) -> str:
        return _NEXT_ACTION[self.kind]


@dataclass
class ExceptionQueue:
    generated_at: datetime
    hub_id: object | None
    items: list = field(default_factory=list)

    def by_kind(self) -> dict:
        return {kind: sum(1 for i in self.items if i.kind == kind) for kind in KINDS}

    @property
    def worst_wait_minutes(self) -> float:
        return max((i.minutes_waiting for i in self.items), default=0.0)


def _minutes_since(moment: datetime | None, now: datetime) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max((now - moment).total_seconds() / 60.0, 0.0)


async def build_exception_queue(
    session: AsyncSession, *, hub_id=None, now: datetime | None = None
) -> ExceptionQueue:
    """Everything a dispatcher should be looking at, worst wait first."""
    now = now or datetime.now(timezone.utc)
    grace = timedelta(seconds=settings.stuck_order_after_seconds)
    queue = ExceptionQueue(generated_at=now, hub_id=hub_id)

    def _scope(statement):
        return statement.where(Order.hub_id == hub_id) if hub_id else statement

    # Flagged stops on orders that have not landed. A flag on a delivered order
    # is history; on an open one it is the earliest warning available.
    #
    # Driver flags only. An inferred flag (app/learning_loop/not_ready.py) is
    # the system's reading of a dwell, not somebody who was there saying so,
    # and this kind's whole claim - and its next action, "read the driver's
    # note" - is that a person was.
    flagged = (
        await session.execute(
            _scope(
                select(Order, StopFlag)
                .select_from(StopFlag)
                .join(Stop, Stop.id == StopFlag.stop_id)
                .join(StopOrder, StopOrder.stop_id == Stop.id)
                .join(Order, Order.id == StopOrder.order_id)
                .where(
                    Order.delivered_at.is_(None),
                    StopFlag.source == FLAG_SOURCE_DRIVER,
                )
            )
        )
    ).all()
    seen: set = set()
    for order, flag in flagged:
        if order.id in seen:
            continue
        seen.add(order.id)
        queue.items.append(
            _item(
                KIND_FLAGGED, order, now,
                detail=f"{flag.flag_type}: {flag.note or 'no note'}",
            )
        )

    failed = list(
        await session.scalars(
            _scope(select(Order).where(Order.status == OrderStatus.delivery_failed))
        )
    )
    for order in failed:
        if order.id in seen:
            continue
        seen.add(order.id)
        queue.items.append(
            _item(
                KIND_FAILED, order, now,
                detail=order.failure_reason or "attempted and not completed",
            )
        )


    # Past the promise and still open. The grace is the same one the health
    # check uses, so a dispatcher and a pager do not disagree about what late
    # means - an order that is an exception on one screen and fine on another is
    # how a team learns to trust neither.
    late = list(
        await session.scalars(
            _scope(
                select(Order).where(
                    Order.delivered_at.is_(None),
                    Order.promised_at.is_not(None),
                    Order.promised_at < now - grace,
                    Order.status.notin_(
                        (
                            OrderStatus.cancelled,
                            OrderStatus.returned,
                            OrderStatus.delivery_failed,
                        )
                    ),
                )
            )
        )
    )
    for order in late:
        if order.id in seen:
            continue
        seen.add(order.id)
        queue.items.append(
            _item(KIND_PAST_PROMISE, order, now, detail=f"status {order.status.value}")
        )

    # Released from the hold queue and never placed on a route. The quietest
    # failure in the system: it looks like an order in transit from every other
    # view, because nothing went wrong - nothing happened at all.
    #
    # Two ways to be here. A dispatcher released it and no cycle placed it: the
    # override is the only writer of `queued`. Or the hold ran out and the
    # cycle released it, found nobody, and left it `held` - it stays in the
    # queue and is released again every cycle, so its status never changes and
    # without the second clause it never appeared here at all. Past its
    # deadline by more than the grace, so an order the next cycle is about to
    # place is not raised as an exception.
    #
    # Live orders only. A backfill loads history as `held` with a deadline long
    # past (app/ingestion/service.py), on purpose and never queued; without
    # this every imported order showed here with an Assign button, as the
    # health check's stuck count has always excluded them.
    unplaced = list(await session.scalars(_scope(select(Order).where(unplaced_clause(now)))))
    for order in unplaced:
        if order.id in seen:
            continue
        seen.add(order.id)
        queue.items.append(
            _item(
                KIND_UNPLACED, order, now,
                detail=(
                    "released from the hold queue and not assigned to a route"
                    if order.status == OrderStatus.queued
                    else "its hold ran out and no driver has it - no cycle placed it, or none has run"
                ),
            )
        )

    # An order with no reference of its own that matches one the same client sent
    # minutes earlier (D-DUP, app/ingestion/service.py). Flagged at intake and never
    # merged: only the client knows whether it was sent twice, and they are asked
    # (`DEC-13`). Listed here only unanswered with a driver on the way.
    repeats = list(
        await session.scalars(
            _scope(
                select(Order).where(
                    Order.intake_flags.contains([{"kind": KIND_POSSIBLE_DUPLICATE}]),
                    Order.status.in_(_DRIVER_ON_THE_WAY),
                )
            )
        )
    )
    # Answered, or settled by the other copy being cancelled, is not a question.
    still_open = await open_duplicate_questions(session, repeats)
    for order in repeats:
        if order.id in seen or order.id not in still_open:
            continue
        seen.add(order.id)
        original = next(
            (f.get("of_reference") for f in order.intake_flags if f.get("kind") == KIND_POSSIBLE_DUPLICATE),
            None,
        )
        queue.items.append(
            _item(KIND_POSSIBLE_DUPLICATE, order, now, detail=f"looks like a repeat of {original}")
        )

    # The playbook's call on each order listed (`DEC-8`): what its rule
    # recommends, when it will act, or that it has handed the order to a person.
    # A failed delivery's call, or an unplaced order's (`DEC-10`), whatever kind
    # the order is listed under.
    calls = await current_calls(session, [o for o, _ in flagged] + failed + late + unplaced)
    if calls:
        queue.items = [
            replace(item, playbook=_call(calls[item.order_id])) if item.order_id in calls else item
            for item in queue.items
        ]

    # Worst wait first, flagged stops lifted on a tie. See the module docstring:
    # the clock is a heuristic standing in for M2, and a flag is the earliest
    # signal we get - burying it under older items defeats having it.
    queue.items.sort(
        key=lambda i: (-i.minutes_waiting, KINDS.index(i.kind))
    )
    return queue


def _call(decision) -> PlaybookCall:
    return PlaybookCall(
        rule=decision.rule,
        action=decision.action,
        reason=decision.reason,
        mode=decision.mode,
        outcome=decision.outcome,
        due_at=decision.due_at,
    )


def _item(kind: str, order: Order, now: datetime, *, detail: str) -> Exception_:
    reference = order.promised_at or order.requested_at
    return Exception_(
        kind=kind,
        order_id=order.id,
        client_id=order.client_id,
        external_ref=order.external_order_ref,
        sla_tier=order.sla_tier.value if order.sla_tier is not None else None,
        minutes_waiting=round(_minutes_since(reference, now), 1),
        promised_at=order.promised_at,
        detail=detail,
        status=order.status.value,
        hold_deadline=order.hold_deadline,
    )
