"""
An order ingested from a client's POS/DMS. This is the row the Dynamic SLA
Engine classifies (T1/T2/T3) and the Batch-Hold Queue clusters.
"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SLATier(str, enum.Enum):
    HOT_SHOT = "HOT_SHOT"  # direct point-to-point, never commingled with another order's pickup
    T1 = "T1"  # urgent / short hold window
    T2 = "T2"  # standard
    T3 = "T3"  # flexible / long hold window


class OrderStatus(str, enum.Enum):
    received = "received"
    classified = "classified"
    held = "held"          # sitting in the batch-hold queue
    queued = "queued"       # released from hold, waiting for a route assignment
    assigned = "assigned"   # attached to a stop on a route
    delivered = "delivered"
    cancelled = "cancelled"
    # A driver flagged the stop covering this order (shop closed, access
    # blocked, a dispute, etc. - app/api/driver_routes.py's flag_stop_issue).
    # Distinct from cancelled: this order was actually attempted, not
    # cancelled pre-dispatch - ops needs to decide on redelivery/refund,
    # not just close it out.
    delivery_failed = "delivery_failed"


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "orders"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shop_profiles.id"), nullable=False)

    external_order_ref: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)  # epicor | mam | asa | flat_file

    # Raw payload as received, kept verbatim for debugging/replay.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    sla_tier: Mapped[SLATier | None] = mapped_column(Enum(SLATier, name="sla_tier"), nullable=True)
    # Explicit timezone=True is required here - without it, SQLAlchemy
    # infers a naive DateTime from the bare `datetime` annotation, which
    # doesn't match the timezone-aware column the migration actually
    # creates (see migrations/versions/0001_initial_schema.py) and every
    # tz-aware datetime this app ever produces (e.g. datetime.now(timezone.utc)
    # in app/sla/engine.py) fails to insert against a real Postgres.
    # Caught by tests/integration/test_ingestion_integration.py - fakeredis/
    # pure-function unit tests can't catch this since they never touch a
    # real database's type-checking.
    hold_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Written by DispatchOptimizerService.run_cycle the moment this order is
    # actually assigned to a driver - see app/optimizer/service.py. Lets the
    # dashboard's Order Status Summary widget (and anything else querying
    # Order.status) reflect a dispatch that already happened instead of
    # showing "held" forever once the Redis hold queue has moved on.
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    weight_units: Mapped[float] = mapped_column(Numeric(10, 2), default=1, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.received, nullable=False
    )

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Delivery (customer/drop-off) side of the order - added for the driver
    # app's active-job flow (screens 1i/1l/1m). Everything ingested before
    # this existed only ever modeled the pickup side (shop_lat/lng via
    # HeldOrder/StopCandidate) - no source-system adapter has been updated
    # to populate these yet, so they're nullable rather than backfilled.
    # A missing delivery_lat/lng means a Stop can't be generated for this
    # order when a job offer is accepted (see app/api/driver_routes.py).
    delivery_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_lat: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    delivery_lng: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    delivery_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # What LMX charges the client for this drop (Phase 8) - set once at
    # classification time (app/ingestion/service.py) from the client's
    # ClientRate for this order's tier. Null, not zero, when the client
    # has no configured rate for this tier yet - a missing price should
    # never silently look like a free delivery on the client portal or in
    # payroll math (docs/NEXT_STEPS.md item 14's driver-earnings gap).
    fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
