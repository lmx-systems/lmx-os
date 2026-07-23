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

    # Proof of delivery (screen 1m). photo_url/signature_url are real,
    # uploaded S3 URLs (app/storage/photo_upload_client.py, docs/ROADMAP.md
    # A2/A3) once a real bucket is configured - the stub client still
    # issues a local-capture:// marker until then, so this column accepts
    # either shape either way. pod_pin is the driver-submitted value,
    # checked at complete_stop time against delivery_pin below (the real,
    # issued PIN) - not just recorded anymore (docs/ROADMAP.md A4).
    pod_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # photo | signature | pin
    pod_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pod_signature_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    pod_pin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Where the driver says they left it (e.g. "front door") - screen 1m's
    # "Left at" field. Free text, not validated against anything.
    pod_left_at: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Real delivery-PIN issuance (docs/ROADMAP.md A4) - generated and
    # texted to Order.delivery_contact_phone the moment this dropoff stop
    # is created (accept_offer, app/api/driver_routes.py), via
    # app/messaging/delivery_pin.py. Null when no contact phone was on
    # file to send one to - complete_stop's method="pin" path refuses a
    # PIN nobody could have been given, same as everywhere else in this
    # app that treats "no destination configured" as "can't do this," not
    # "silently succeed anyway."
    delivery_pin: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Caps brute-force guessing of a 4-digit PIN over the API - same
    # "attempts column, no Redis needed" shape as this table's own
    # scanned_count, since this is a per-stop, already-authenticated-driver
    # counter, not a cross-request abuse-rate concern.
    pin_verification_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # "Flag an issue" (driver-facing incident report, not to be confused
    # with StopFlag below, which is an ops route-planning annotation for a
    # different consumer - the Learning Loop). Sets status="failed".
    failure_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # SHOP_CLOSED | ACCESS_ISSUE | COD_DISPUTE | PARTS_MISSING | REFUSED
    flag_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Kept distinct from completed_at - this stop was never actually
    # completed, and anything reading completed_at as "successfully
    # delivered at" must not be corrupted by a failed stop's timestamp.
    flagged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
