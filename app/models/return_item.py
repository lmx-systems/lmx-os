"""
A core or return to be collected and brought back to the shop
(docs/ROADMAP.md W1) - the reverse leg the system couldn't model before,
and, in the workflow session's words, "half the economics of the parts
trade."

A **core** is the rebuildable old part (alternator, caliper) a customer
hands back when they receive the new one - it carries a deposit and has to
go back to the distributor. The aligned model (Decision log, July 2026) is
**piggyback + shop-flag**: most cores are collected on the *delivery
visit* (the exchange happens at the door), so a return links to its
originating delivery order and carries an item manifest; standalone
returns a shop has accumulated are handled by a counter-person flag
(a later slice).

Lifecycle: expected -> collected -> returned_to_shop. `not_ready` when the
core wasn't available to collect (feeds the reschedule workflow);
`cancelled` if it's called off.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ReturnItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "return_items"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)
    # The forward delivery this core came off - keeps the deposit/exchange
    # traceable back to the order that generated it (W1's "links to its
    # originating delivery"). Nullable (slice 2): a *standalone* return a
    # shop flags for pickup - cores it accumulated rather than handed back
    # at a specific delivery - has no single originating order.
    origin_order_id: Mapped[UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    # Where the core goes back to - the originating order's shop.
    shop_id: Mapped[UUID] = mapped_column(ForeignKey("shop_profiles.id"), nullable=False)
    # What's being returned - free text for v1 ("core: alternator"); a
    # structured line-item manifest can replace this without a schema change
    # if per-item audit becomes a requirement.
    manifest: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[str] = mapped_column(String(24), default="expected", nullable=False)
    # expected | ready_for_pickup | collected | returned_to_shop | not_ready | cancelled
    # `ready_for_pickup` (slice 2): a shop flagged accumulated cores as ready
    # to collect, independent of any delivery to piggyback on.

    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
