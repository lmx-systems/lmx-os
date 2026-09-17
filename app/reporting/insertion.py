"""Do we reach the door faster than the dispatcher did? (`DEC-4`)

`ROADMAP_1.5.md` Phase 3 states the ceiling this is measured against, and it is
not a flattering one:

> **Know the ceiling:** **50.2% of orders already get in-flight insertion by
> hand**, and those reach customers *faster* (34-minute median vs 40). The
> differentiator is doing it consistently at volume without one irreplaceable
> dispatcher.

`DEC-4`'s done-when is "matches or beats the human dispatcher's 34-minute
median". The mechanism largely exists - `run_cycle` inserts into active routes
with a capacity check, and all three dispatch triggers have producers. What was
missing is the half that decides whether it worked.

**The baseline is verified, which is worth saying because several others were
not.** Recomputed from the design partner's export on 17 September 2026 via
`ml/real/`: 3,373 in-flight orders at a 34.0-minute median, 3,342 planned at
40.0, a 50.2% in-flight share. Three figures, three exact matches. The
recomputation is one command - `ml.real.load_timing` plus `was_inserted_in_flight`
- so this constant never has to be taken on trust.

**The comparison is orientation, not proof.** It holds our median against
another company's operation in a different season with a different order mix,
which is exactly the confound `ROADMAP_1.5.md` names when it says `EXP-0`'s
historical baseline "is not the counterfactual a savings statement rests on".
The same objection applies here and is stated on the measurement rather than
left for somebody to raise. `EXP-1`'s arm is the version that is caused rather
than correlated; this one tells you whether you are in the right neighbourhood.

**In-flight, defined for our own data.** The export marks an order as inserted
when it was created after its route had been dispatched. The live analogue is an
order requested after the route it ended up on was planned - `Order.requested_at`
later than `Route.created_at`. Same idea, same direction, different column names.

**Timed from driver taps on purpose.** `REC-2` prefers geofence crossings
because seconds matter to a dwell figure. Here the unit is minutes and the
number is compared against a minute-resolution baseline, so taps are the like-
for-like source; using a better clock on one side of a comparison would make the
two sides less comparable, not more.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.route import Route
from app.models.stop import Stop, StopOrder
from app.reporting.measurement import Measurement, Rate, no_data

# The design partner's operation, recomputed from the export rather than quoted.
# `ml/real/export.py` + `TimingStop.was_inserted_in_flight` reproduces all three.
BASELINE_IN_FLIGHT_MEDIAN_MINUTES = 34.0
BASELINE_PLANNED_MEDIAN_MINUTES = 40.0
BASELINE_IN_FLIGHT_SHARE = 0.502
BASELINE_SOURCE = "design partner export, 6,715 stops, Jan-Apr 2026"


def _seconds_to_door():
    return cast(
        func.extract("epoch", Stop.arrived_at - Order.requested_at), Float
    )


def _delivered_dropoffs(since: datetime):
    """Orders whose dropoff was reached, joined to the route that carried them."""
    return (
        select(_seconds_to_door(), Order.requested_at > Route.created_at)
        .select_from(Order)
        .join(StopOrder, StopOrder.order_id == Order.id)
        .join(Stop, Stop.id == StopOrder.stop_id)
        .join(Route, Route.id == Stop.route_id)
        .where(
            Stop.stop_type == "dropoff",
            Stop.arrived_at.is_not(None),
            Order.requested_at >= since,
            Stop.arrived_at > Order.requested_at,
        )
    )


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


async def _split(session: AsyncSession, since: datetime):
    rows = (await session.execute(_delivered_dropoffs(since))).all()
    in_flight = [float(seconds) for seconds, inserted in rows if inserted]
    planned = [float(seconds) for seconds, inserted in rows if not inserted]
    return in_flight, planned


def _measurement(
    name: str, seconds: list[float], baseline_minutes: float
) -> Measurement:
    if not seconds:
        return no_data(
            name,
            f"{baseline_minutes:.0f} min median ({BASELINE_SOURCE})",
            detail="no delivered orders of this kind in the window",
        )
    return Measurement(
        name=name,
        target=f"{baseline_minutes:.0f} min median ({BASELINE_SOURCE})",
        median=_median(seconds),
        p90=_percentile(seconds, 0.9),
        sample_size=len(seconds),
        unit="seconds",
    )


async def order_to_door_measurements(
    session: AsyncSession, since: datetime
) -> list[Measurement]:
    """Order to door, split the way the baseline is split.

    Both halves, because the interesting comparison is not the fleet average.
    The dispatcher's advantage shows up specifically on the orders they inserted
    by hand, and a single pooled median would average it away against the
    planned orders they were slower on.
    """
    in_flight, planned = await _split(session, since)
    return [
        _measurement(
            "Order to door, inserted after the route was planned",
            in_flight,
            BASELINE_IN_FLIGHT_MEDIAN_MINUTES,
        ),
        _measurement(
            "Order to door, planned from the start",
            planned,
            BASELINE_PLANNED_MEDIAN_MINUTES,
        ),
    ]


async def in_flight_share(session: AsyncSession, since: datetime) -> Rate:
    """How much of the book we insert after planning.

    Half the design partner's orders arrive too late to be planned for, and that
    is a property of their customers rather than of their dispatcher. A share far
    from 50% here means we are measuring a different mix, and the median
    comparison above is worth correspondingly less.
    """
    in_flight, planned = await _split(session, since)
    total = len(in_flight) + len(planned)
    if not total:
        return Rate(
            name="Orders inserted after the route was planned",
            target=f"{BASELINE_IN_FLIGHT_SHARE:.1%} ({BASELINE_SOURCE})",
            not_measured="no delivered orders in the window",
        )
    return Rate(
        name="Orders inserted after the route was planned",
        target=f"{BASELINE_IN_FLIGHT_SHARE:.1%} ({BASELINE_SOURCE})",
        numerator=len(in_flight),
        denominator=total,
    )
