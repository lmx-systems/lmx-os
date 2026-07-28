"""
A single physical package belonging to an order (docs/ROADMAP.md W10).

Before this, a package had no identity at all - Stop.parcel_count /
scanned_count were just integers, and POST /driver/stops/{id}/scan took a
*number*, never a scanned value. That made A2's barcode scanner "a reader
with nothing to read" and left WRONG_PART - the session's "most expensive
recoverable error" - catchable only at the customer's door.

Ownership-agnostic by design: `barcode` holds either an LMX-generated code
or the distributor's own pick-ticket barcode (app/ingestion/service.py
populates it from the payload when present, else generates one). The
scan-at-pickup verification path (app/api/driver_routes.py) is identical
either way, so the printer-vs-scan-existing hardware decision stays
deferred and reversible - it only changes where the value comes from, not
this model.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Parcel(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "parcels"
    __table_args__ = (
        # A barcode identifies exactly one parcel within a hub. Scoped to
        # the hub (not global) so two distributors' pick-ticket barcodes
        # can't collide across hubs; scan lookup always has the driver's
        # hub in hand.
        UniqueConstraint("hub_id", "barcode", name="uq_parcel_hub_barcode"),
    )

    # Denormalized from the order for a simple per-hub unique constraint and
    # a single-column scan lookup (barcode -> parcel within the hub).
    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    barcode: Mapped[str] = mapped_column(String(128), nullable=False)
    # When this parcel was scanned at pickup. Null = not yet collected;
    # "3 of 5 collected" is a count of non-null scanned_at across an order's
    # parcels, auditable rather than self-reported.
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
