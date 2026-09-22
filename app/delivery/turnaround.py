"""How long a driver spends at the warehouse between routes (`DRV-3`).

*"Turnaround measured per return trip."*

A real cost input rather than a curiosity. `M4`'s trip cost counts the
driver-hours a route consumes, and the twenty minutes spent reloading in the
yard are as real as the twenty spent driving — but only the driving has ever
been visible, so every trip cost to date has understated itself by whatever the
turnaround was.

## What counts as one turnaround

An `enter` followed by that same driver's next `exit` at the same hub. Nothing
cleverer: a driver arrives, loads, leaves.

Three things are deliberately *not* turnarounds, and each is a way the naive
version inflates the number:

**An `enter` with no following `exit`** — they are still there, or the shift
ended in the yard. Open, not zero, and not a very long one either.

**An `exit` with no preceding `enter`** — the first departure of the day, or a
crossing lost while permission was off (`DRV-5`). There is no arrival to measure
from, and pairing it with the previous day's would report an overnight.

**Anything longer than `MAX_PLAUSIBLE_TURNAROUND`** — the driver went home. A
fence records a crossing, not an intention, and an `enter` at 6pm paired with an
`exit` at 7am next morning is a car park, not a turnaround. Reported separately
rather than dropped, because a hub whose crossings are mostly implausible has a
fence problem and silently discarding them would hide it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hub_geofence_event import KIND_ENTER, KIND_EXIT, HubGeofenceEvent

# Beyond this, an enter/exit pair is somebody's evening rather than a
# turnaround. Stated rather than derived: nothing in the brief sizes it, and
# four hours is wide enough for a genuinely bad reload and narrow enough to
# exclude an overnight. It is reported as `implausible` rather than dropped, so
# the choice can be argued with from the data.
MAX_PLAUSIBLE_TURNAROUND = timedelta(hours=4)


@dataclass(frozen=True)
class Turnaround:
    """One return trip: back in the yard, then out again."""

    driver_id: object
    arrived_at: datetime
    departed_at: datetime

    @property
    def seconds(self) -> float:
        return (self.departed_at - self.arrived_at).total_seconds()


@dataclass
class TurnaroundReport:
    """What the fence saw, including what it could not pair.

    `open` and `unpaired` are reported rather than swallowed. A hub where half
    the crossings never pair has a sensor problem — a fence too tight, or
    permission switched off mid-shift — and a median computed from the half that
    did pair would look perfectly healthy.
    """

    turnarounds: list[Turnaround]
    open_visits: int = 0
    unpaired_exits: int = 0
    implausible: int = 0

    @property
    def median_seconds(self) -> float | None:
        if not self.turnarounds:
            return None
        values = sorted(t.seconds for t in self.turnarounds)
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    @property
    def pairing_rate(self) -> float | None:
        """Share of arrivals that produced a usable turnaround.

        The number that says whether to trust the median. Not a percentage on
        its own: `None` when there were no arrivals at all, because 0% and "no
        data" are different and only one of them is a fence problem.
        """
        arrivals = len(self.turnarounds) + self.open_visits + self.implausible
        if arrivals == 0:
            return None
        return round(len(self.turnarounds) / arrivals, 3)


async def turnarounds_for_hub(
    session: AsyncSession,
    *,
    hub_id,
    since: datetime | None = None,
    until: datetime | None = None,
) -> TurnaroundReport:
    """Pair this hub's crossings into return trips.

    Ordered by the phone's clock, not ours. `recorded_at` is when a queued batch
    reached us, so ordering by it would interleave a driver's morning with their
    afternoon the moment the outbox flushed a dead zone.
    """
    query = select(HubGeofenceEvent).where(HubGeofenceEvent.hub_id == hub_id)
    if since is not None:
        query = query.where(HubGeofenceEvent.occurred_at >= since)
    if until is not None:
        query = query.where(HubGeofenceEvent.occurred_at < until)

    events = list(
        await session.scalars(
            query.order_by(HubGeofenceEvent.driver_id, HubGeofenceEvent.occurred_at)
        )
    )

    report = TurnaroundReport(turnarounds=[])
    open_enter: dict = {}

    for event in events:
        if event.kind == KIND_ENTER:
            if event.driver_id in open_enter:
                # Two arrivals with no departure between them. The first is a
                # visit whose exit was lost; the second is the live one. Pairing
                # across it would report the gap between two arrivals as time
                # spent in the yard.
                report.open_visits += 1
            open_enter[event.driver_id] = event.occurred_at
            continue

        if event.kind != KIND_EXIT:
            continue

        arrived = open_enter.pop(event.driver_id, None)
        if arrived is None:
            report.unpaired_exits += 1
            continue

        if event.occurred_at - arrived > MAX_PLAUSIBLE_TURNAROUND:
            report.implausible += 1
            continue

        report.turnarounds.append(
            Turnaround(
                driver_id=event.driver_id,
                arrived_at=arrived,
                departed_at=event.occurred_at,
            )
        )

    # Still in the yard when the window closed.
    report.open_visits += len(open_enter)
    return report
