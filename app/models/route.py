"""A driver's active or completed route for a shift."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Route(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "routes"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="planned", nullable=False)
    # planned | active | completed | aborted

    # Version bumped every time the Dispatch Optimizer re-sequences this
    # route mid-shift, so the driver app can detect a stale route.
    plan_version: Mapped[int] = mapped_column(default=1, nullable=False)
