"""
A driver's support thread with the hub's dispatchers (screens 1p/1q).

The driver writes from the app (POST /driver/me/messages); a dispatcher reads
and answers in the ops console's support inbox (app/api/admin_routes.py). Both
sides are LMX's own apps, so nothing goes through a phone network. It used to be
texted to a support number through Twilio, with replies arriving on an inbound
webhook, and it shared this table with masked texts to recipients, which are
gone: a driver now calls or texts a recipient from their own phone.
"""
import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"

    hub_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    # Which stop a message is about, where it is about one. Null for the
    # ongoing support thread.
    stop_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stops.id"), nullable=True)

    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # support
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    # From the driver's side: outbound (driver -> dispatch) | inbound (dispatch -> driver)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    # The dispatcher who wrote an inbound message. Null on the driver's own.
    ops_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ops_users.id"), nullable=True)

    # created_at (TimestampMixin) doubles as "sent_at" - no separate column needed.
