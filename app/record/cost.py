"""What a drop actually cost (`REC-2`'s last column).

`ROADMAP_1.5.md` REC-2 asks for an execution trace: *"geofence, warehouse,
exceptions, actual cost."* Most of it now exists - `DRV-1` records a crossing
per stop, `driver_shift_event` records the clock, `app/payroll/hours.py` turns
the second into hours. The column nobody had built is the last one.
`Order.cost_actuals_cents` has been on the model since the beginning, documented
as *"filled in after the fact"*, and filled in by nothing.

It is the hole at the centre of Phase 2, whose goal is *"a measured cost-per-drop
delta"*. There is no cost per drop. `ml/prd/trip_cost.py` computes one for the
historical export; nothing computes one for an order we delivered ourselves.

## Attribution is the whole problem

A route is one driver for a span of time and several drops. The driver's cost is
a property of the span. Nothing in the data says what share of it belongs to any
one drop, so the split is a choice, and two different questions want two
different choices.

**Its own time** - the dwell at its door plus the leg that got there - answers
*"was this drop worth making"*. It is the number that makes a $12 part on a
forty-minute round trip visible.

**Loaded** - its own time plus a pro-rata share of everything else the driver
was paid for - answers *"what did our day cost per drop"*. This is the one that
sums back to what we actually paid, which is the only version an aggregate claim
can use. A cost-per-drop built from own-time alone is smaller than the wage bill
and would quietly flatter every comparison built on it.

Both are returned. Neither is a marginal cost: *"what would the route have cost
without this order"* needs the with-and-without solve `MODEL_AND_DATA_BRIEF.md`
§M3 describes, and inventing it from a time share would be a guess wearing a
number's clothes.

**A stop's cost divides across the orders on it.** Two orders riding to one dock
share the dwell and the leg, which is where batching shows up as money rather
than as a stops-per-route statistic.

## The unit is a driver-day, not a route

The first version of this measured a route, from its first stop's arrival to its
last stop's departure, and the overhead term came out at exactly zero every
time. That window *is* the dwells plus the legs between them, by construction -
so "loaded" and "own" collapsed into the same number and the arithmetic
flattered itself. Worse, it was wrong in the direction that matters: getting to
the first dock, coming back from the last, and every gap between routes is real
paid time that no drop was carrying.

A wage is paid by the day. So the unit is the driver's on-duty time across a
window, from `driver_shift_event`, and every drop they made in it. Overhead is
then a real quantity - the paid time no stop accounts for - and the loaded costs
sum to what the driver was actually paid rather than to the part of the day that
happened to be inside a route.

Warehouse turnaround still is not broken out. `DRV-3` is the geofence that would
measure it; until then it is inside the overhead rather than missing from it,
which is the right place for time we know was paid for and cannot yet name.

## The wage is recorded, not assumed

`Driver.hourly_rate_cents` is nullable and `app/payroll/hours.py` falls back to
a placeholder it flags as *"not tuned against any real wage decision"*. A cost
computed from a placeholder wage is a fiction, so it is not refused - that would
make this unusable on the data we have today - but every result carries
`rate_source`, and an aggregate that mixes real and placeholder rates says so.
Nobody should be able to quote one of these without meeting the word
`placeholder`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver
from app.models.outcome_entry import KIND_COST, SUBJECT_ORDER
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.models.stop_geofence_event import KIND_ENTER, KIND_EXIT, StopGeofenceEvent
from app.payroll.hours import PLACEHOLDER_HOURLY_RATE_CENTS, hours_worked_from_shift_events
from app.record.outcomes import record_outcome

SOURCE_GEOFENCE = "geofence"
SOURCE_TAPS = "taps"

RATE_FROM_DRIVER = "driver"
RATE_PLACEHOLDER = "placeholder"


@dataclass(frozen=True)
class StopTiming:
    """When a stop began and ended, and how we know.

    Geofence beats taps, the same precedence `IDN-4`'s profile uses and for the
    same reason: a crossing is machine-generated and a tap is a person
    remembering. The source travels with the number because a route timed from
    taps and one timed from crossings are not the same evidence.
    """

    stop_id: object
    sequence: int
    arrived_at: datetime | None
    departed_at: datetime | None
    source: str | None

    @property
    def dwell_seconds(self) -> float:
        if self.arrived_at is None or self.departed_at is None:
            return 0.0
        return max((self.departed_at - self.arrived_at).total_seconds(), 0.0)


@dataclass(frozen=True)
class OrderCost:
    order_id: object
    stop_seconds: float
    travel_seconds: float
    overhead_seconds: float
    rate_cents_per_hour: int
    rate_source: str
    shared_with: int

    @property
    def own_seconds(self) -> float:
        """Dwell plus the leg in. The 'was this drop worth making' number."""
        return self.stop_seconds + self.travel_seconds

    @property
    def loaded_seconds(self) -> float:
        return self.own_seconds + self.overhead_seconds

    @property
    def own_cents(self) -> int:
        return round(self.own_seconds / 3600 * self.rate_cents_per_hour)

    @property
    def loaded_cents(self) -> int:
        """The one that sums to the wage bill. Use this for cost per drop."""
        return round(self.loaded_seconds / 3600 * self.rate_cents_per_hour)


@dataclass
class DriverDayCost:
    """What one driver's paid window cost, and each drop's share of it."""

    driver_id: object
    window_start: datetime | None
    window_end: datetime | None
    paid_seconds: float
    attributed_seconds: float
    rate_cents_per_hour: int
    rate_source: str
    timing_source: str | None
    orders: list[OrderCost] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def overhead_seconds(self) -> float:
        return max(self.paid_seconds - self.attributed_seconds, 0.0)

    @property
    def total_cents(self) -> int:
        return round(self.paid_seconds / 3600 * self.rate_cents_per_hour)

    @property
    def cost_per_drop_cents(self) -> int | None:
        if not self.orders:
            return None
        return round(self.total_cents / len(self.orders))

    def summary(self) -> dict:
        return {
            "driver_id": str(self.driver_id),
            "drops": len(self.orders),
            "paid_seconds": round(self.paid_seconds, 1),
            "attributed_seconds": round(self.attributed_seconds, 1),
            "overhead_seconds": round(self.overhead_seconds, 1),
            "total_cents": self.total_cents,
            "cost_per_drop_cents": self.cost_per_drop_cents,
            "rate_cents_per_hour": self.rate_cents_per_hour,
            "rate_source": self.rate_source,
            "timing_source": self.timing_source,
            "notes": list(self.notes),
        }


async def stop_timings(session: AsyncSession, stops: list[Stop]) -> list[StopTiming]:
    """Arrive and depart per stop, crossings preferred over taps."""
    if not stops:
        return []
    crossings = list(
        await session.scalars(
            select(StopGeofenceEvent)
            .where(StopGeofenceEvent.stop_id.in_([s.id for s in stops]))
            .order_by(StopGeofenceEvent.occurred_at)
        )
    )
    entered: dict = {}
    exited: dict = {}
    for event in crossings:
        if event.kind == KIND_ENTER:
            entered.setdefault(event.stop_id, event.occurred_at)
        elif event.kind == KIND_EXIT:
            # The last exit, not the first: a phone that wobbles across the
            # boundary mid-stop would otherwise end the stop at the wobble.
            exited[event.stop_id] = event.occurred_at

    timings = []
    for stop in stops:
        arrive, depart, source = entered.get(stop.id), exited.get(stop.id), SOURCE_GEOFENCE
        if arrive is None or depart is None:
            arrive, depart, source = stop.arrived_at, stop.completed_at, SOURCE_TAPS
        if arrive is None or depart is None:
            source = None
        timings.append(
            StopTiming(
                stop_id=stop.id,
                sequence=stop.sequence,
                arrived_at=arrive,
                departed_at=depart,
                source=source,
            )
        )
    # Ordered by when they actually happened, not by sequence within a route -
    # a driver-day can hold several routes, and the leg into a stop is the gap
    # since whatever they last left, whichever route it belonged to.
    return sorted(timings, key=lambda t: (t.arrived_at is None, t.arrived_at))


async def driver_day_cost(
    session: AsyncSession, *, driver_id, since: datetime, until: datetime
) -> DriverDayCost:
    """What this driver's paid window cost, and each drop's share of it."""
    driver = await session.get(Driver, driver_id)
    rate = getattr(driver, "hourly_rate_cents", None)
    rate_source = RATE_FROM_DRIVER if rate else RATE_PLACEHOLDER
    rate = rate or PLACEHOLDER_HOURLY_RATE_CENTS

    cost = DriverDayCost(
        driver_id=driver_id,
        window_start=since,
        window_end=until,
        paid_seconds=0.0,
        attributed_seconds=0.0,
        rate_cents_per_hour=rate,
        rate_source=rate_source,
        timing_source=None,
    )
    if rate_source == RATE_PLACEHOLDER:
        cost.notes.append(
            f"rate is the {PLACEHOLDER_HOURLY_RATE_CENTS / 100:.2f}/hr placeholder - "
            "this driver has no hourly_rate_cents, so every figure here is "
            "arithmetic on an invented wage"
        )

    paid_hours = await hours_worked_from_shift_events(
        session, str(driver_id), since, until
    )
    cost.paid_seconds = paid_hours * 3600
    if cost.paid_seconds <= 0:
        cost.notes.append(
            "no on-duty time in this window from the shift-event log, so there is "
            "no wage to attribute - a day's drops with no shift behind them is a "
            "gap in the trace rather than a free day"
        )
        return cost

    stops = list(
        await session.scalars(
            select(Stop)
            .join(Route, Route.id == Stop.route_id)
            .where(Route.driver_id == driver_id)
            .order_by(Stop.sequence)
        )
    )
    timings = [
        t
        for t in await stop_timings(session, stops)
        if t.source is not None and since <= t.arrived_at < until
    ]
    if not timings:
        cost.notes.append(
            "the driver was on duty and no stop in this window has both an "
            "arrival and a departure, so the whole wage is unattributed"
        )
        return cost

    sources = {t.source for t in timings}
    cost.timing_source = SOURCE_GEOFENCE if sources == {SOURCE_GEOFENCE} else "mixed"
    if cost.timing_source == "mixed":
        cost.notes.append(
            "some stops are timed from driver taps rather than geofence crossings; "
            "minute-level tap precision is what DRV-1 exists to replace"
        )

    links = list(
        await session.execute(
            select(StopOrder.stop_id, StopOrder.order_id).where(
                StopOrder.stop_id.in_([t.stop_id for t in timings])
            )
        )
    )
    orders_at_stop: dict = {}
    for stop_id, order_id in links:
        orders_at_stop.setdefault(stop_id, []).append(order_id)

    # Per-stop own time: dwell plus the leg in from wherever the driver last
    # was. Only stops carrying an order are attributed - a stop with no order on
    # it is time somebody was paid for that no drop accounts for, which is the
    # definition of overhead. Counting it as attributed would break the one
    # property that makes these numbers usable in aggregate: that the loaded
    # costs sum back to the wage bill.
    own: dict = {}
    previous_departure = None
    for timing in timings:
        travel = 0.0
        if previous_departure is not None:
            travel = max((timing.arrived_at - previous_departure).total_seconds(), 0.0)
        if orders_at_stop.get(timing.stop_id):
            own[timing.stop_id] = (timing.dwell_seconds, travel)
        previous_departure = timing.departed_at

    cost.attributed_seconds = min(
        sum(d + t for d, t in own.values()), cost.paid_seconds
    )
    if len(own) < len(timings):
        cost.notes.append(
            f"{len(timings) - len(own)} timed stop(s) carry no order; their time is "
            "counted as overhead and spread across the drops that do"
        )
    cost.notes.append(
        "overhead is the paid time no stop accounts for - reaching the first dock, "
        "returning from the last, and every gap between. It is spread across the "
        "drops in proportion to their own time, so the loaded costs sum to the wage"
    )

    per_order: dict = {}
    for stop_id, (dwell, travel) in own.items():
        sharing = orders_at_stop[stop_id]
        share = len(sharing)
        for order_id in sharing:
            stop_seconds, travel_seconds, shared = per_order.get(order_id, (0.0, 0.0, 1))
            per_order[order_id] = (
                stop_seconds + dwell / share,
                travel_seconds + travel / share,
                max(shared, share),
            )

    # Pro-rata rather than per head so a two-minute counter drop does not carry
    # the same share of a long day as a forty-minute one.
    total_own = sum(s + t for s, t, _ in per_order.values())
    for order_id, (stop_seconds, travel_seconds, shared) in per_order.items():
        weight = (stop_seconds + travel_seconds) / total_own if total_own else 0.0
        cost.orders.append(
            OrderCost(
                order_id=order_id,
                stop_seconds=stop_seconds,
                travel_seconds=travel_seconds,
                overhead_seconds=cost.overhead_seconds * weight,
                rate_cents_per_hour=rate,
                rate_source=rate_source,
                shared_with=shared,
            )
        )
    return cost


async def record_driver_day_cost(
    session: AsyncSession,
    *,
    hub_id,
    driver_id,
    since: datetime,
    until: datetime,
    occurred_at: datetime | None = None,
) -> list:
    """Write each order's cost into the outcome ledger (`REC-3`).

    Into the ledger rather than onto `Order.cost_actuals_cents`, deliberately.
    The boundary note in `tests/test_architecture_boundaries.py` puts it plainly:
    *"a record the deciding code could read back and act on would stop being a
    record."* A cost on the order is a field dispatch can read; a cost in the
    ledger is evidence. A caller that needs it on the order - billing, say - can
    copy it, and that is their decision to own rather than this layer's to make.

    The values written are the whole basis, not just the number: the rate, where
    the rate came from, which timing source, how many orders shared the stop,
    and the notes. An entry saying `1_247` and nothing else would be unarguable
    with, and `REC-3` exists so outcomes can be argued with.
    """
    cost = await driver_day_cost(session, driver_id=driver_id, since=since, until=until)
    if not cost.orders:
        return []
    entries = []
    for order in cost.orders:
        entries.append(
            await record_outcome(
                session,
                hub_id=hub_id,
                subject_type=SUBJECT_ORDER,
                subject_id=order.order_id,
                kind=KIND_COST,
                occurred_at=occurred_at or until,
                values={
                    "loaded_cents": order.loaded_cents,
                    "own_cents": order.own_cents,
                    "stop_seconds": round(order.stop_seconds, 1),
                    "travel_seconds": round(order.travel_seconds, 1),
                    "overhead_seconds": round(order.overhead_seconds, 1),
                    "rate_cents_per_hour": order.rate_cents_per_hour,
                    "rate_source": order.rate_source,
                    "shared_with": order.shared_with,
                    "timing_source": cost.timing_source,
                    "driver_id": str(cost.driver_id),
                    "window": [cost.window_start.isoformat(), cost.window_end.isoformat()],
                    "notes": cost.notes,
                },
            )
        )
    return entries
