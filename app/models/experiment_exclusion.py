"""Docks a customer has asked to keep out of the experiment (`EXP-2`).

`ROADMAP_1.5.md` EXP-2 is done when *"no single dock absorbs more than its
share; fragile accounts excludable."* This is the second half.

**Why a customer needs this before they will sign the clause.** The control arm
deliberately gives a slice of their orders worse service. The first question
anyone asks is "not my biggest account, though" - and without an answer the
conversation stops there. `EXP-1` ships off until the clause is agreed, so the
thing that makes the clause agreeable has to exist first.

**Excluding is not free, and the cost is the measurement.** A customer who
excludes their most delivery-sensitive docks leaves a control arm measured on
their calmer traffic, and a saving estimated there does not generalise back to
the docks that were removed. That is a legitimate trade for them to make and an
illegitimate one to make quietly, so exclusions are counted and reported -
`exclusion_impact` exists so `STL-1` can state, in the savings statement, what
share of volume the measurement never covered.

**Rows are kept, never deleted.** Revoking sets `revoked_at` rather than
removing the row, because "this dock was excluded from March to June at the
customer's request" is part of what a later statement has to be able to explain.
Not a trigger-enforced append-only table like `experiment_assignments` - this is
configuration with a history, not evidence of a decision, and it has to be
changeable by the people whose accounts it protects.
"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ExperimentExclusion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One dock, kept out of one experiment, for a stated reason."""

    __tablename__ = "experiment_exclusions"

    # No foreign key, for the reason the assignments table records: this is
    # evidence about what a customer asked for, and it must stay readable if the
    # client row is ever purged under a retention policy.
    client_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # The dock, as `app/identity/` normalises it. A string rather than a
    # `Location` FK because the experiment package must not depend on identity -
    # the dispatch engine reads arm labels, and a transitive dependency from the
    # core into an edge package is what `tests/test_architecture_boundaries.py`
    # exists to prevent. The caller supplies the key.
    receiver_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    experiment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Required. An exclusion with no reason cannot be reviewed later, and the
    # whole point is that somebody can ask why the measurement skipped this dock.
    reason: Mapped[str] = mapped_column(String(500), nullable=False)

    # Who asked. `customer` and `lmx` are different facts: one is a constraint we
    # accepted, the other is a judgement we made, and a statement disclosing the
    # gap should be able to say which.
    requested_by: Mapped[str] = mapped_column(String(16), nullable=False)

    excluded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None
