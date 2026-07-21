"""
Real shift/timesheet event log - a durable record of every driver
online/offline/break transition, independent of Redis fleet state (which
holds only the current status, overwritten on every update - not a
history). Exists so hours worked can eventually be computed from actual
online-to-offline time instead of Route.created_at/updated_at (today's
earnings estimate, app/api/driver_routes.py, undercounts any time spent
online but waiting for an offer - see docs/NEXT_STEPS.md's W2
launch-readiness item). This table only captures raw transitions -
whether on_break time counts as paid time is a policy decision this log
deliberately doesn't resolve; nothing yet reads from this table to
compute pay.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DriverShiftEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_shift_events"

    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # available | off_shift | on_break | en_route - mirrors
    # DriverAvailabilityUpdate.status 1:1 (app/schemas/driver_app.py), the
    # same vocabulary POST /driver/me/state already receives on every
    # online/offline/break toggle, rather than a second vocabulary to keep
    # in sync with it.
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
