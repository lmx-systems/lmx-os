"""
A job offer extended to a driver off a Dispatch Optimizer assignment.

Why this exists as its own table rather than just setting Route.driver_id
directly: the optimizer decides *who should* get a set of stops, but
nothing today lets the driver accept or decline that before it's real work
on their route (docs/NEXT_STEPS.md item 12 - "no accept/decline concept at
all"). A RouteOffer is the pending, driver-facing half of that decision;
a Route (+ Stops) is only ever created once an offer is accepted -
app/api/driver_routes.py's accept_offer. Decline/expiry never touch
Route/Stop at all - the affected orders just go back to the hold queue for
the next cycle to try again (see app/optimizer/service.py).

stop_payload is a snapshot, not a live reference, deliberately: it's the
exact set of stops the driver is being asked to accept, in sequence, so a
sibling order changing after the offer is made can't silently change what
the driver agreed to mid-review. Same shape as StopCandidate
(app/schemas/optimizer.py) plus whatever the driver app needs to render an
offer card (shop name, address) that StopCandidate doesn't carry.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class RouteOffer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "route_offers"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(16), default="offered", nullable=False)
    # offered | accepted | declined | expired

    stop_payload: Mapped[list] = mapped_column(JSONB, nullable=False)

    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set once accept_offer() creates the real Route - lets the driver app
    # go straight from "I accepted" to "here's my route" with one id.
    route_id: Mapped[UUID | None] = mapped_column(ForeignKey("routes.id"), nullable=True)
