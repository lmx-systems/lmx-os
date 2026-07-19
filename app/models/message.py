"""
Driver messaging (Phase 3, screens 1p/1q): masked contact with the
customer at a dropoff, and contact with LMX dispatch/support. Both ride
the same SMS channel (app/messaging/sms_client.py) - "masked" here means
the customer/support side always sees LMX's shared Twilio number, never
the driver's personal phone number, and the driver never sees the
customer's/support's real number either (it's stored server-side only,
never returned to the app - see DriverDocumentView-style read models in
app/schemas/driver_app.py).
"""
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    # Only set for channel="customer" - which dropoff this conversation is
    # about, so a driver with several deliveries today doesn't get threads
    # mixed together. Null for channel="support" (one ongoing thread with
    # dispatch, not tied to any single stop).
    stop_id: Mapped[UUID | None] = mapped_column(ForeignKey("stops.id"), nullable=True)

    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # customer | support
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    # outbound (driver -> customer/support) | inbound (customer/support -> driver)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # The real phone number this message was sent to / received from - the
    # "masked" part of masked SMS is that this never gets serialized back
    # to the driver app (see MessageView in app/schemas/driver_app.py,
    # which deliberately omits it), only used server-side to send via
    # Twilio and to match inbound replies back to the right conversation.
    # Nullable: an outbound support message sent before SUPPORT_PHONE_NUMBER
    # is configured (app/config.py) has nowhere real to go yet, but is still
    # recorded rather than silently dropped - see the "support" endpoint's
    # docstring in app/api/driver_routes.py.
    counterparty_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Twilio's message SID - null when sent through StubSmsClient (no
    # Twilio account configured yet, see app/messaging/sms_client.py) or
    # for inbound messages, which don't have one to record here.
    twilio_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # created_at (TimestampMixin) doubles as "sent_at"/"received_at" - no
    # separate column needed.
