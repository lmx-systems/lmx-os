"""A machine-recorded crossing of a stop's geofence (`docs/ROADMAP_1.5.md` DRV-1).

The sensor Phase 1 exists to build. `Stop.arrived_at` is the time a driver
*tapped* a button, stamped by the server when the request landed; this is the
time the phone crossed the boundary, stamped by the phone. They are different
measurements and the difference is the point.

**Why a separate table rather than better values in `Stop`.** Overwriting
`arrived_at` with a geofence time would destroy the one comparison that tells us
whether any of this worked. The design partner's dispatch export is
minute-resolution and **65.5% of its stops compute to zero dwell**; the
second-precision file shows those same stops really took 3-50 seconds. The claim
Phase 1 has to prove is that a machine-generated boundary crossing recovers what
a tap and a rounded clock threw away - and you cannot prove that from a column
that no longer holds the old number. So both are kept, side by side.

**`occurred_at` is the device's clock, `recorded_at` is ours.** Same convention
as `DriverLocationPingBody`, and here it matters more: DRV-4's outbox holds stop
events through a dead zone and flushes them on reconnect, so a whole afternoon
of crossings can arrive at one instant. Stamping them server-side would collapse
the shift onto the moment the signal came back - which is exactly the failure
the outbox exists to prevent. Keeping both also makes clock skew measurable
instead of invisible.

**Idempotent by construction.** The outbox retries, so the same crossing will be
delivered more than once. `(stop_id, kind, occurred_at)` is UNIQUE: a replay is
a conflict the database refuses rather than a second arrival that doubles a
dwell sample.
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


class StopGeofenceEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stop_geofence_events"
    __table_args__ = (
        UniqueConstraint(
            "stop_id", "kind", "occurred_at", name="uq_stop_geofence_event_crossing"
        ),
    )

    stop_id: Mapped[UUID] = mapped_column(
        ForeignKey("stops.id"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(String(8), nullable=False)

    # When the phone crossed the boundary, by the phone's clock.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # When we received it. `recorded_at - occurred_at` is the outbox delay for a
    # queued event and the clock skew for a live one, and there is no way to
    # tell them apart from one column.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # The fix's reported accuracy, metres. A crossing recorded with a 500m
    # accuracy circle is not evidence of much, and dropping it at the edge would
    # throw away the only signal that says so.
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
