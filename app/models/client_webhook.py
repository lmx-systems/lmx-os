"""
Outbound status webhooks: where to send, and what is still owed
(docs/ROADMAP.md F4, docs/LMX_LINK_PLAN.md §1.4 / T5).

§1.4 is blunt about why this half exists: *"A carrier that takes orders and goes
quiet is not a carrier - it is a favour."* A webhook that POSTs once and drops the
event when the client's server happens to be restarting is that same silence with
extra steps, so **the delivery attempt is not the record - `WebhookDelivery` is.**

Two tables rather than one, because they answer different questions and have very
different lifetimes:

  `ClientWebhookEndpoint`  a client's standing subscription. Long-lived, edited
                           rarely, holds a signing secret.
  `WebhookDelivery`        one owed notification. Written in the SAME transaction
                           as the status change it describes, then delivered
                           asynchronously and retried.

**That transactional enqueue is the point of the design.** `emit_status_change` is
called from inside `advance_orders`, *before* the caller commits - so a sink that
POSTed inline could tell a client an order was delivered on a transaction that
then rolled back. A row written in the caller's session cannot: if the delivery
doesn't happen, neither does the notification.
"""
import secrets
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Identity,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# How many consecutive failures before an endpoint is switched off. An endpoint
# that has been dead for this many events is not coming back on its own, and
# retrying it forever spends our budget and fills the table on behalf of a client
# who has decommissioned a server and not told us.
MAX_CONSECUTIVE_FAILURES = 20

DELIVERY_PENDING = "pending"
DELIVERY_DELIVERED = "delivered"
# Retries exhausted - the endpoint kept failing in a way that could have worked.
DELIVERY_FAILED = "failed"
# The endpoint answered clearly that it does not want this: a 4xx that isn't a
# timeout or a rate limit. Kept distinct from `failed` because retrying it is
# pointless and counting it as an outage would misattribute the fault.
DELIVERY_REJECTED = "rejected"


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


class ClientWebhookEndpoint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_webhook_endpoints"

    client_id: Mapped[UUID] = mapped_column(
        ForeignKey("clients.id"), nullable=False, index=True
    )
    # Validated for scheme and destination before it is ever stored - see
    # app/webhooks/url_safety.py. A client-supplied URL that this server will POST
    # to is an SSRF primitive, not just a config field.
    url: Mapped[str] = mapped_column(String(500), nullable=False)

    # Signs every request body so the client can prove the call came from us.
    # Stored in the clear because we need it to compute the HMAC - the same
    # reasoning as the tracking token, and unlike a password there is nothing to
    # compare against. Shown to the client exactly once, at creation.
    secret: Mapped[str] = mapped_column(String(128), nullable=False)

    # Free text so a client running several integrations can tell them apart in
    # their own portal ("warehouse system", "Slack relay").
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Reset to zero by any success. Reaching MAX_CONSECUTIVE_FAILURES deactivates
    # the endpoint rather than deleting it, so the client can see in their portal
    # that it was switched off and why.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WebhookDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # One notification per endpoint per event. The uniqueness is what makes the
        # enqueue safe to repeat: a retried driver action that replays a
        # transition cannot produce two POSTs of the same event to the same
        # consumer.
        UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_delivery_event"),
    )

    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("client_webhook_endpoints.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)

    # Stable id for this transition, sent in the payload and in a header so a
    # consumer can dedupe at-least-once delivery on their side.
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # A monotonic counter across all events. **Retries mean arrival order is not
    # event order** - a `picked_up` that failed twice can land after the
    # `delivered` that followed it - so consumers are told to order by this rather
    # than by when the request showed up. Timestamps alone are not enough: two
    # transitions on one order can share a millisecond.
    sequence: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False
    )

    # The exact bytes that get signed and sent, frozen at enqueue time. Rebuilding
    # the payload at delivery time would let a retry describe a *later* state than
    # the event it claims to be, which is how a consumer ends up with a
    # "picked_up" webhook whose body says delivered.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=DELIVERY_PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # When the sweep should next pick this up. Indexed with status because the
    # sweep's only query is "pending and due".
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
