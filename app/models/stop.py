"""
A physical stop on a route (usually 1:1 with a shop delivery, but can carry
multiple commingled orders per Section 8's multi-client commingling design).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Stop(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "stops"

    route_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routes.id"), nullable=False)
    # Only set for stop_type="pickup" - a dropoff stop is at the customer's
    # delivery address (Order.delivery_lat/lng), not a shop.
    shop_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shop_profiles.id"), nullable=True)

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
    # The ETA as first predicted, written once and never refreshed
    # (app/delivery/eta.py, migration 0040). `eta` above moves as the route
    # progresses, which is what a driver needs and what makes it worthless as
    # ground truth - a number recomputed until the moment of arrival is accurate
    # by construction. `arrived_at - planned_eta` is the real error.
    planned_eta: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Ground-truth capture (docs/ROADMAP.md I1): when the driver actually
    # arrived. time-at-stop = completed_at - arrived_at (measurable per
    # shop), and arrived_at vs `planned_eta` is direct ETA-accuracy ground
    # truth. Set on the first arrive_at_stop transition, never overwritten.
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Every photo captured, not only the first. `pod_photo_url` above predates
    # configurable proof and holds a single URL; an order can require several with
    # named subjects (ProofRequirements, migration 0037), and a stop that was made to
    # produce four photos must not store one - we would have insisted on evidence we
    # then failed to keep. The single column stays populated so existing readers and
    # older app builds keep working.
    pod_photo_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    pod_pin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Where the driver says they left it (e.g. "front door") - screen 1m's
    # "Left at" field. Free text, not validated against anything.
    pod_left_at: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Delivery PIN (docs/ROADMAP.md A4), generated the moment this dropoff
    # stop is created (accept_offer, app/api/driver_routes.py) and shown to
    # the recipient on the tracking page (app/messaging/delivery_pin.py).
    # Null only on stops created before every dropoff got one -
    # complete_stop's method="pin" path refuses a PIN nobody could have been
    # given.
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

    # --- The golden record's Delivery layer (`docs/ROADMAP_1.5.md` DE-1) -------
    # A drop-off or visit stop is a record: one delivery or visit, from one
    # source, on one date. Which door it used, and who captured it how.
    handoff_point_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("handoff_points.id"), nullable=True, index=True
    )
    # lmx_app for a stop driven in our app; an import, a gig form or a desk
    # pre-fill says so. Every record carries its source.
    record_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="lmx_app", server_default="lmx_app"
    )
    collected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class StopOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Join row: which order(s) a stop covers. A pickup stop can commingle
    several orders from the same shop (Section 8 clustering); a dropoff
    stop is one order per customer address in v1 (commingled multi-order
    dropoffs - e.g. two orders to the same address - are a fast-follow,
    same join table handles it without a schema change when that lands).
    """
    __tablename__ = "stop_orders"

    stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)


# Who raised a StopFlag. A driver's report and the system's inference are
# both evidence that a hold window was wrong, and only one of them is a person
# saying so - the exception queue surfaces the first and must not surface the
# second (migration 0068).
FLAG_SOURCE_DRIVER = "driver"
FLAG_SOURCE_INFERRED = "inferred"


class StopFlag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Flag on a stop (e.g. 'gate code needed', 'shop closes early Fridays').
    Feeds the Annotation and Learning Loop (component 6). Raised by a driver,
    or - for `hold_window_too_short` - inferred from a pickup's dwell by
    app/learning_loop/not_ready.py; `source` says which.
    """
    __tablename__ = "stop_flags"
    __table_args__ = (
        # One inferred flag of a type per stop, ever: the nightly inference is
        # re-runnable and the query that skips already-flagged stops is not a
        # guarantee. Partial, because a driver may flag a stop twice on purpose.
        Index(
            "uq_stop_flags_inferred_once",
            "stop_id",
            "flag_type",
            unique=True,
            postgresql_where=text("source = 'inferred'"),
        ),
    )

    stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)
    flag_type: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source: Mapped[str] = mapped_column(
        String(16), default=FLAG_SOURCE_DRIVER, server_default=FLAG_SOURCE_DRIVER, nullable=False
    )
    # Null when `source` is inferred - nobody raised it.
    created_by_driver_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drivers.id"), nullable=True
    )
