"""
What LMX OS *would* have done, recorded while somebody else dispatched for real
(docs/ROADMAP.md W9, session decision D3).

Every initial customer engagement runs live on the Elite EXTRA scaffold while LMX OS
decides in parallel on the same orders, and the two are compared until a scorecard
passes and that engagement cuts over. This is the recording half: the decision, frozen
at the moment it was made, so it can be compared against what actually happened.

**Two tables, and the second one is the point.** D3 is explicit that aggregate metrics
look fine while the two systems agree - the divergent orders are the entire point. A
cycle-level row alone would report "82% agreement" and leave nobody able to ask *which
orders, and did our answer turn out better*. `ShadowOrderDecision` is one row per order
per cycle, so that question is a join rather than an archaeology project.

**Nothing here is computed from a second implementation of the optimizer.** The rows
are written from `DispatchOptimizerService.plan_cycle`, the same call `run_cycle` makes
before it commits anything. A parallel implementation would drift, and every divergence
it reported would then be ambiguous - a real disagreement, or just the shadow path
having fallen behind. One implementation, two callers.

**Frozen, not recomputed.** A shadow decision is evidence in a cutover argument. If the
optimizer is retuned next month, last month's recorded decisions must not quietly change
with it - the same reasoning as `InvoiceCredit` storing what was promised rather than
recomputing it.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ShadowDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One shadow cycle: what the optimizer decided, having changed nothing."""

    __tablename__ = "shadow_decisions"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)

    # When the decision was made, not when the row was written. The scorecard windows
    # on this, and `created_at` would drift from it if a write were ever retried.
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # A closed hub decided nothing; it did not decide to dispatch nothing. Without this
    # the scorecard's data-completeness metric would read a quiet Sunday as the
    # optimizer having had a chance at the work and declined it.
    hub_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Which solver produced it. A run against the stub and a run against Google are not
    # comparable evidence, and until E1 verifies the live client, most of these rows
    # will say stub - which the scorecard must be able to say out loud.
    engine: Mapped[str] = mapped_column(String(48), nullable=False)

    # D3's re-plan speed metric: "<5s at real volume". Measured over the decision only,
    # deliberately excluding the commit half, because that is the number the claim is
    # about and the commit does not run in shadow.
    plan_duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    held_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    released_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    driver_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unassigned_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The plan as the solver returned it - driver, order sequence and visit ordering.
    # Kept whole alongside the per-order rows below because the per-order view cannot
    # express "these four were one route", which is what a batch-rate metric counts.
    assignment_payload: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class ShadowOrderDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What the optimizer would have done with one order, in one shadow cycle.

    The unit divergence is measured in. Comparing this against the order's real
    `assigned_at`, driver and outcome is what turns "the two systems disagreed" into
    "the two systems disagreed about these seven orders, and ours were delivered
    eleven minutes sooner".
    """

    __tablename__ = "shadow_order_decisions"

    shadow_decision_id: Mapped[UUID] = mapped_column(
        ForeignKey("shadow_decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Not a FK to orders.id on purpose: a shadow decision is a record of a moment, and
    # it must survive the order being deleted under a retention policy (R3) rather than
    # taking the evidence with it.
    order_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # `assigned` - the optimizer would have given it to `driver_id` now.
    # `unassigned` - it was released from the hold queue and the optimizer could not
    #                place it this cycle. Distinct from never having been released,
    #                which produces no row at all: "we would have left it held" and
    #                "we tried and failed to place it" are different decisions.
    decision: Mapped[str] = mapped_column(String(16), nullable=False)

    driver_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Position in that driver's planned route. Lets a divergence be graded rather than
    # just counted - the same driver in a different order is a smaller disagreement
    # than a different driver.
    sequence_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sla_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
