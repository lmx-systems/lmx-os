"""What actually happened, attached to the decision that caused it (REC-3).

`docs/ROADMAP_1.5.md` REC-3, and its done-when is a structural requirement
rather than a feature: *"outcomes attach by key without touching the decision
row."*

**That constraint is already physically true and this table is why it stays
convenient.** REC-1's `decision_snapshots` rejects UPDATE at the database, so
there is no version of "record the outcome on the decision" that works. The
point of stating it as a done-when is that the obvious shape - a
`delivered_on_time` column on the decision, filled in later - is the one thing
the design forbids. A decision is what was known at an instant; an outcome is
known afterwards. Putting the second inside the first destroys the first.

**A ledger, so corrections are entries rather than edits.** An outcome can turn
out to be wrong - a delivery marked failed that was actually completed, a
dispute resolved in the customer's favour - and the honest record of that is a
second entry that supersedes the first, not an overwrite. `supersedes` carries
the link. This table is append-only too, enforced the same way.

**Why the decision link is nullable.** Not every outcome descends from a
decision we recorded: an order delivered before REC-1 existed, or one a human
dispatcher moved by hand. An outcome with no decision behind it is still a
fact, and refusing to record it would bias the measurement towards exactly the
deliveries the system handled - which is the population whose performance is
being claimed.
"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

SUBJECT_ORDER = "order"
SUBJECT_STOP = "stop"
SUBJECT_TYPES = (SUBJECT_ORDER, SUBJECT_STOP)

# Delivered, and whether it beat the promise it was judged against.
KIND_DELIVERED = "delivered"
# Attempted and not completed - the censored case M1b cares about, and the one
# that biases a dwell figure downward if it is counted as a fast stop.
KIND_FAILED = "failed"
# Time at the door, from whichever source was better (DRV-1's crossings, else
# the driver's taps).
KIND_DWELL = "dwell"
# The customer disagreed with something we recorded. Not a delivery outcome -
# an outcome about an outcome, which is why it is a kind rather than a column.
KIND_DISPUTED = "disputed"
# What the drop actually cost us (REC-2, app/record/cost.py). An outcome rather
# than a field on the order: the wage bill is a thing that happened after the
# decision, and a cost the dispatch engine could read back would stop being a
# record of it.
KIND_COST = "cost"
# We chose not to act on this order, because it is in the control arm
# (EXP-1/EXP-3, app/record/abstention.py). The one kind here that records an
# absence: every other entry says something happened, this one says we
# deliberately let it happen without us. Without it, an order dispatched the
# customer's old way and one we quietly held look identical afterwards, and the
# arm cannot be audited at all.
KIND_ARM_ABSTENTION = "arm_abstention"

KINDS = (
    KIND_DELIVERED,
    KIND_FAILED,
    KIND_DWELL,
    KIND_DISPUTED,
    KIND_COST,
    KIND_ARM_ABSTENTION,
)


class OutcomeEntry(Base, UUIDPrimaryKeyMixin):
    """One thing that happened, recorded once and never edited.

    No `TimestampMixin`, same reason as `DecisionSnapshot`: `updated_at` on a
    row nothing may update is a misleading field to leave lying about.
    `occurred_at` is when it happened and `recorded_at` is when we learned,
    and the gap between them is often the interesting part.
    """

    __tablename__ = "outcome_ledger"

    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # The key. Nullable because not every outcome descends from a decision we
    # recorded - see the module docstring. No foreign key, for the same reason
    # 0051 dropped its own: an outcome must stay readable if its decision is
    # ever purged, and evidence that vanishes with its subject is not evidence.
    decision_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    subject_type: Mapped[str] = mapped_column(String(8), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # When the thing happened, by the clock of whatever observed it.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When we learned. An outbox flush or a dispute can make this much later,
    # and a measurement that assumed they were the same would place a delivery
    # on the wrong day.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # The measured values. A payload rather than columns because the four kinds
    # measure different things - lateness against a promise, a dwell in
    # seconds, a disputed amount - and four sets of mostly-null columns would
    # say less than one honest one.
    values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # A correction. The superseded entry stays exactly as written; this is how
    # the record says "we later learned better" without pretending it always
    # knew.
    supersedes: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
