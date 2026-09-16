"""Which arm an order was put in, and on what terms (`EXP-1`).

The done-when is three clauses and each one is load-bearing: *"arm assigned at
intake, immutable, in the contract before the code."*

**Assigned at intake**, because an arm chosen any later can be chosen knowing
something about the order. The moment assignment happens after a dispatcher has
seen the delivery, the control group stops being random and the comparison
stops meaning anything - and nobody would have to intend that for it to happen.

**Immutable**, enforced the same way REC-1 enforces it: a trigger rejecting
UPDATE and DELETE. An arm that can be re-rolled is not an arm. The failure this
prevents is not fraud; it is a well-meaning engineer moving one order out of
control because the customer complained, which is exactly the order whose
presence in the control group the measurement depends on.

**In the contract before the code.** `MODEL_AND_DATA_BRIEF.md` puts it plainly -
*"an arm discovered rather than disclosed looks like negligence."* So the
parameters that produced each assignment are recorded on the row: the salt, the
fraction, and the date the clause was agreed. A regulator, an auditor or a
customer asking "was I in an experiment, and did I agree to it" gets an answer
from the data rather than from somebody's memory.

**Why the assignment is a hash rather than a random draw.** A random number
generator leaves nothing behind: you cannot later prove the split was fair, only
assert it. A deterministic hash of (salt, order key) can be recomputed by anyone
holding the row, so `EXP-3`'s integrity monitor can verify the arm rather than
trust it - and a re-roll becomes visible, because the recomputed value would no
longer match.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

ARM_CONTROL = "control"
ARM_TREATMENT = "treatment"
ARMS = (ARM_CONTROL, ARM_TREATMENT)

# One experiment today. Named rather than implied so a second one - an
# exploration policy, a pricing test - does not have to share this one's
# assignments or reinterpret its history.
EXPERIMENT_CONTROL_ARM = "exp-1-control-arm"


class ExperimentAssignment(Base, UUIDPrimaryKeyMixin):
    """One order's arm, with everything needed to verify it.

    No `TimestampMixin`: nothing here may be updated, so an `updated_at` would
    be a field that can never change - the same misleading signal REC-1's table
    avoids.
    """

    __tablename__ = "experiment_assignments"

    # No foreign keys, for the reason 0051 and 0053 already record: an
    # assignment is evidence about a moment, and it must stay readable if the
    # order or the client is ever purged.
    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    client_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    order_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )

    experiment: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    arm: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # The parameters, so the assignment can be recomputed rather than trusted.
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    control_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    # The hash's position in [0, 1). Stored so EXP-3 can check the distribution
    # is uniform without recomputing every hash, and so a skew shows up as a
    # property of the data rather than a claim about the code.
    draw: Mapped[float] = mapped_column(Float, nullable=False)

    # When this client's contract clause was agreed. Copied onto every
    # assignment rather than only living on the client, because the client row
    # can change and this is the fact the assignment was made under.
    contracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
