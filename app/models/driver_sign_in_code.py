"""A one-time code that signs a driver's phone in, issued from the ops console.

Replaces the texted sign-in code. Ops already creates every driver, so ops
handing the driver a code, in person, as a QR code to scan or ten characters to
type, is the check of who they are. No text message, so no SMS provider and no
SIM swap.

**Only a keyed hash is stored** (`code_hmac`). The code itself exists on the
console screen that issued it and nowhere else, so a database dump yields
nothing that signs anybody in.

**One code works once, and only the newest.** Redeeming stamps `redeemed_at`
and the device it signed in; issuing a new code supersedes any older one that
was never used. Rows are kept: they are the record of who let which phone in,
and when, which a texted code never left behind.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DriverSignInCode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_sign_in_codes"

    driver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False, index=True)
    code_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    issued_by_ops_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ops_users.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
