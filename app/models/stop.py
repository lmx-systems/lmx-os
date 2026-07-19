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
    # Only set for stop_type="pickup" - a dropoff stop is at the customer's
    # delivery address (Order.delivery_lat/lng), not a shop.
    shop_id: Mapped[UUID | None] = mapped_column(ForeignKey("shop_profiles.id"), nullable=True)

    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    # pending | en_route | arrived | completed | failed

    # Added for the driver app: the original model implicitly assumed every
    # stop was a shop pickup (see the module docstring and shop_id above).
    # The actual delivery workflow also needs customer dropoff stops -
    # see docs/NEXT_STEPS.md item 12 for why this wasn't modeled until now.
    stop_type: Mapped[str] = mapped_column(String(16), default="pickup", nullable=False)
    # pickup | dropoff

    # Parcel scan progress (screen 1k, "Scan parcels") - a running count,
    # not a per-parcel ledger. A real barcode/parcel model (individual
    # tracked parcel rows) is a fast-follow if per-parcel audit history
    # becomes a real requirement; this is enough to drive the scan screen.
    parcel_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # timezone=True required - see the comment on Order.hold_deadline in
    # app/models/order.py for why (a real bug this exact mismatch caused,
    # caught by tests/integration/).
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Proof of delivery (screen 1m). No image/signature upload pipeline
    # exists yet - these accept whatever string the client sends (a URL, or
    # a data: URI for a quick v1) and store it verbatim. pod_pin is not
    # verified against anything server-side yet - there's no PIN-issuance
    # system - it's recorded for the record but is not a real check in v1.
    pod_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # photo | signature | pin
    pod_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pod_signature_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pod_pin: Mapped[str | None] = mapped_column(String(16), nullable=True)


class StopOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Join row: which order(s) a stop covers. A pickup stop can commingle
    several orders from the same shop (Section 8 clustering); a dropoff
    stop is one order per customer address in v1 (commingled multi-order
    dropoffs - e.g. two orders to the same address - are a fast-follow,
    same join table handles it without a schema change when that lands).
    """
    __tablename__ = "stop_orders"

    stop_id: Mapped[UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)


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
