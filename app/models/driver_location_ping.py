"""
A single reported driver position (docs/ROADMAP.md F1).

Why this exists alongside the Redis hash. `app/fleet_state/manager.py`
already keeps a driver's *current* position at
`fleet:{hub_id}:driver:{driver_id}:location`, and that is what the
optimizer reads on its hot path (`app/optimizer/service.py` skips any
driver whose location is None). What it cannot do is answer anything
historical: Redis holds one lat/lng per driver and overwrites it on every
ping, so distance actually travelled is unrecoverable the moment the next
ping lands.

Miles per drop is one of the nine metrics the shadow-mode cutover
scorecard is scored on (docs/ROADMAP.md W9), and it is computed from the
path a driver actually took, not from their latest position. Same argument
as `app/models/driver_shift_event.py` (durable transitions vs. Redis's
current status) and I1's ground-truth capture: a figure we never recorded
cannot be backfilled.

Append-only by design - a ping is an observation, never edited. There is
deliberately no unique constraint on (driver_id, recorded_at): two pings
sharing a timestamp is a duplicate reading, not a data error, and
rejecting it would make a retrying offline client fail for no reason.

RETENTION IS NOT HANDLED HERE, and it is a real open question rather than
an oversight. At a 30s ping interval (driver-app/src/location's
LOCATION_PING_INTERVAL_MS) an on-duty driver writes ~120 rows/hour, so 20
drivers on a 10-hour shift is ~24k rows/day - trivial for Postgres now,
but unbounded over a year. Pruning needs a decision about how long a
breadcrumb trail should be kept, which is a privacy/retention question
(docs/ROADMAP.md R3) and not one to answer unilaterally in a model
docstring.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DriverLocationPing(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_location_pings"
    __table_args__ = (
        # Every read of this table is "this driver's trail over a window"
        # (a route's mileage, a shift replay). recorded_at descending so
        # "latest known position for driver X" is a backwards index scan
        # limit 1 rather than a sort over the driver's whole history.
        Index(
            "ix_driver_location_pings_driver_recorded",
            "driver_id",
            "recorded_at",
            postgresql_using="btree",
        ),
    )

    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    # Denormalized from the driver so a hub's whole fleet trail is queryable
    # without a join, matching why app/models/parcel.py carries hub_id.
    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)

    # When the *device* observed this position, which is not when the row
    # was written: the driver app queues pings while offline
    # (app/../offline) and flushes them later, so created_at can trail
    # recorded_at by an entire dead-zone. Every distance/replay
    # computation must order by this column, never created_at.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Metres of horizontal uncertainty the device reported, when it gave
    # one. Kept because a 2km-accuracy fix and a 5m fix are not equally
    # usable for mileage, and discarding it here would make that
    # undecidable downstream. Null = the platform reported no accuracy.
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
