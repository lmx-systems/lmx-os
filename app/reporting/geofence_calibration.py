"""Is the geofence radius right? Measure it against the driver's own taps.

`GEOFENCE_RADIUS_M` in `driver-app/src/location/geofenceWindow.ts` is 75, and
75 is a guess. It was chosen as a plausible compromise between two failures
that pull in opposite directions:

  too small - GPS noise means the fence never fires, and the stop has no
              machine arrival at all
  too large - the fence fires while the van is still down the street, and
              "arrival" is stamped early

This module is how that guess stops being a guess. It works because DRV-1
deliberately kept both clocks: `stops.arrived_at` is when the driver tapped,
`stop_geofence_events` is when the phone crossed the boundary. Neither is
ground truth on its own - a tap is late and forgetful, a fence is early by
however far the radius reaches - but the *distance between them* is exactly
the quantity the radius controls.

**What the two numbers mean.**

`coverage` is the share of completed stops that produced a crossing at all. A
low figure means the fence is too small for the GPS accuracy the fleet
actually gets, or that background permission is off - and those two causes look
identical here, which is why `stops_without_permission_signal` is not a column
this can offer. Check the permission state separately before reading a low
coverage as a radius problem.

`lead_seconds` is `tap - crossing`: how long before the driver pressed arrive
the fence had already fired. It is the radius expressed in time. A median lead
of a few seconds means the fence is tight around the door. A median of a minute
means it is firing a street away and every arrival time is early by that much.

**What this deliberately does not do: pick a radius.** It reports the two
numbers and the decision is a person's, because the trade-off is a judgement
about which error is worse for the promise being made - and that depends on
the SLA tier, not on the statistics.
"""
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.route import Route
from app.models.stop import Stop
from app.models.stop_geofence_event import KIND_ENTER, StopGeofenceEvent


@dataclass
class GeofenceCalibration:
    """One reading of how the sensor is behaving against the taps."""

    completed_stops: int
    stops_with_crossing: int
    #: Stops where both a crossing and a tap exist, so a lead time is computable.
    comparable_stops: int

    lead_p50_seconds: float | None = None
    lead_p90_seconds: float | None = None
    #: Crossings that landed *after* the tap. Not an error - a driver can tap
    #: arrive while still rolling up - but a large share means the fence is
    #: tighter than the place where drivers actually stop, and arrival times
    #: are being taken from the tap in practice.
    late_crossings: int = 0

    notes: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Share of completed stops the sensor saw at all."""
        if self.completed_stops == 0:
            return 0.0
        return self.stops_with_crossing / self.completed_stops

    @property
    def is_readable(self) -> bool:
        """Whether there is enough here to draw any conclusion from.

        Thirty comparable stops is not a statistical threshold; it is the point
        below which a median is one driver's habits rather than the fleet's.
        Reporting a lead time from four stops would invite exactly the
        false-precision this document is trying to replace.
        """
        return self.comparable_stops >= 30


async def measure_geofence_calibration(
    session: AsyncSession,
    *,
    hub_id=None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> GeofenceCalibration:
    """Compare crossings against taps over a window of completed stops.

    Scoped to completed stops: a failed stop has no dependable tap to compare
    against, and including it would measure the exception path rather than the
    sensor.

    Percentiles are computed here rather than in Postgres, which is the
    opposite of the choice `identity/profile.py` makes and for a reason. There,
    the row count per dock is unbounded and only the summary is wanted. Here
    every row is already being fetched to count coverage, so a second
    aggregate query would read the same rows twice and - as the first draft of
    this function proved - can silently apply a different filter than the one
    the counts used.
    """
    first_enter = (
        select(
            StopGeofenceEvent.stop_id.label("stop_id"),
            func.min(StopGeofenceEvent.occurred_at).label("entered_at"),
        )
        .where(StopGeofenceEvent.kind == KIND_ENTER)
        .group_by(StopGeofenceEvent.stop_id)
        .subquery()
    )

    query = (
        select(Stop.arrived_at, first_enter.c.entered_at)
        .outerjoin(first_enter, first_enter.c.stop_id == Stop.id)
        .where(Stop.status == "completed")
    )
    if hub_id is not None:
        # A stop reaches a hub through its route, not through its shop - a shop
        # belongs to a client, and a client's orders can be carried by more than
        # one hub.
        query = query.join(Route, Stop.route_id == Route.id).where(Route.hub_id == hub_id)
    if since is not None:
        query = query.where(Stop.completed_at >= since)
    if until is not None:
        query = query.where(Stop.completed_at <= until)

    rows = (await session.execute(query)).all()

    result = GeofenceCalibration(
        completed_stops=len(rows),
        stops_with_crossing=sum(1 for row in rows if row.entered_at is not None),
        comparable_stops=0,
    )

    leads = [
        (row.arrived_at - row.entered_at).total_seconds()
        for row in rows
        if row.entered_at is not None and row.arrived_at is not None
    ]
    result.comparable_stops = len(leads)

    if not leads:
        result.notes.append(
            "No stop has both a crossing and a tap, so the radius cannot be assessed."
        )
        return result

    leads.sort()
    result.lead_p50_seconds = _percentile(leads, 0.5)
    result.lead_p90_seconds = _percentile(leads, 0.9)
    result.late_crossings = sum(1 for lead in leads if lead < 0)

    if not result.is_readable:
        result.notes.append(
            f"Only {result.comparable_stops} comparable stops - below the 30 this "
            "needs before a median says anything about the fleet rather than one "
            "driver."
        )
    if result.completed_stops and result.coverage < 0.8:
        result.notes.append(
            f"The sensor saw {result.coverage:.0%} of completed stops. A small "
            "radius and a missing background permission look identical here - "
            "check the permission state before reading this as a radius problem."
        )
    return result


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Linear interpolation between order statistics, matching percentile_cont.

    Same definition Postgres uses, so a figure from here and a figure from a
    SQL percentile over the same data agree - which matters the first time
    somebody checks one against the other.
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
