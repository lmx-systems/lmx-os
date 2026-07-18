"""
A physical stop on a route (usually 1:1 with a shop delivery, but can carry
multiple commingled orders per Section 8's multi-client commingling design).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Stop(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stops"

    route_id: Mapped[UUID] = mapped_column(ForeignKey("routes.id"), nullable=False)
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shop_profiles.id"), nullable=False)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    # pending | en_route | arrived | completed | failed

    # timezone=True required - see the comment on Order.hold_deadline in
    # app/models/order.py for why (a real bug this exact mismatch caused,
    # caught by tests/integration/).
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pod_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class StopFlag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Driver-annotated flag on a stop (e.g. 'gate code needed', 'shop closes
    early Fridays'). Feeds the Annotation and Learning Loop (component 6).
    """
    __tablename__ = "stop_flags"

    stop_id: Mapped[UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)
    flag_type: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
