"""
Masked voice call log (docs/ROADMAP.md A7) - one row per "Call" button
tap on a dropoff stop. Deliberately its own table, not folded into
Message (app/models/message.py): a call has no body text to store, and
carries its own lifecycle (initiated -> connected -> completed/failed/
no-answer, updated by Twilio's status-callback webhook) that Message's
single created_at timestamp per row has no way to represent.
"""
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Call(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "calls"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    stop_id: Mapped[UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)

    # The real customer number this call bridges to - never serialized
    # back to the driver app (see CallView in app/schemas/driver_app.py),
    # same masking rule as Message.counterparty_phone.
    counterparty_phone: Mapped[str] = mapped_column(String(32), nullable=False)

    # initiated (Twilio call to the driver placed) | connected (driver
    # answered, bridged to the customer) | completed | failed | no-answer.
    # Stays "initiated" forever when the stub voice client is running (no
    # Twilio account configured, app/messaging/voice_client.py) - there's
    # no real call for a status callback to ever update.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="initiated")

    twilio_call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
