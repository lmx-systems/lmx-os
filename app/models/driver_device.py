"""
Device-bound driver sessions. A driver's JWT (app/driver_auth/tokens.py)
carries a device_id claim so a specific device's session can be revoked
(e.g. "my phone was stolen") without invalidating every device a driver
has ever signed in on, and without needing a server-side session table
looked up on every request - revocation is a Redis denylist check
(app/driver_auth/dependencies.py), this table is just the driver-facing
"which devices am I signed in on" record plus the un-revoke-on-re-OTP path.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DriverDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_devices"
    __table_args__ = (UniqueConstraint("driver_id", "device_id", name="uq_driver_devices_driver_device"),)

    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    # Client-generated stable per-install id (a UUID generated once and
    # persisted in the app's SecureStore) - not an OS advertising id.
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
