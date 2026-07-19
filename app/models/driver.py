"""A driver assigned to a hub."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Driver(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "drivers"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    vehicle_capacity_units: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="off_shift", nullable=False)
    # off_shift | available | en_route | on_break

    # Driver app onboarding (screen 1c, "Vehicle & profile setup"). Nullable
    # because existing/seeded drivers predate this screen; the app should
    # treat a null vehicle_type as "setup incomplete" and route there.
    vehicle_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # car | van | bike
    plate_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_zone: Mapped[str | None] = mapped_column(String(120), nullable=True)
