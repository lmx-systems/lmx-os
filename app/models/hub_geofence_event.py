"""A machine-recorded crossing of the warehouse geofence (`DRV-3`).

`DRV-1` measures how long a driver spends at a *dock*. This measures how long
they spend at *ours* — the turnaround between coming back off a route and
leaving on the next one. `ROADMAP_1.5.md` puts the done-when as *"turnaround
measured per return trip"*, and it is a real cost input: `M4`'s trip cost counts
the driver-hours a route consumes, and the twenty minutes spent reloading in the
yard are as real as the twenty spent driving.

## Why not `stop_geofence_events`

Its `stop_id` is a non-nullable foreign key, and a warehouse crossing belongs to
no stop. Widening that column to carry two different subjects would mean every
reader of a dwell sample having to remember to exclude the hub — and the first
one to forget gets a dwell distribution with the depot in it.

They are also keyed differently. A stop crossing is *"this stop, this
crossing"*; a hub crossing is *"this driver, at our place, at this moment"*, and
several drivers cross the same fence within a minute of each other. Hence
`(hub_id, driver_id, kind, occurred_at)`.

## The conventions it does share

**`occurred_at` is the phone's clock, `recorded_at` is ours.** `DRV-4`'s outbox
holds these through a dead zone and flushes on reconnect, so a whole afternoon
can arrive at one instant; stamping server-side would collapse the shift onto
the moment the signal returned, which is the failure the outbox exists to
prevent.

**Idempotent by construction.** The outbox retries, so the same crossing will be
delivered more than once. The unique constraint makes a replay a conflict the
database refuses rather than a second arrival that invents a turnaround.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

KIND_ENTER = "enter"
KIND_EXIT = "exit"
KINDS = (KIND_ENTER, KIND_EXIT)


class HubGeofenceEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hub_geofence_events"
    __table_args__ = (
        UniqueConstraint(
            "hub_id",
            "driver_id",
            "kind",
            "occurred_at",
            name="uq_hub_geofence_event_crossing",
        ),
    )

    hub_id: Mapped[UUID] = mapped_column(
        ForeignKey("hubs.id"), nullable=False, index=True
    )
    driver_id: Mapped[UUID] = mapped_column(
        ForeignKey("drivers.id"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(String(8), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The fix's reported accuracy, metres. A crossing recorded with a 500m
    # circle is not evidence of much, and a turnaround computed from two of them
    # is worth less than one from two tight fixes. Kept so a reader can weigh
    # it rather than discovering later that it could not.
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
