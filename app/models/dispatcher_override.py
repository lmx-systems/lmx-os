"""When a human overruled the queue, and why (`CON-2`, `CON-3`).

`CON-2`: *"no override completes without a reason."* `CON-3`: *"every override
lands in the decision log as a labelled example."*

Building them found that **there was no override.** Nothing let a dispatcher
release a held order or hold a released one - the mandatory reason code had
nothing to be mandatory on. So this is the override and its record together,
which is the right shape anyway: an override that could be performed without
writing one of these would make the reason optional in practice however loudly
the API insisted otherwise.

## Why it is a label and not a log line

The queue decides to hold an order and records why (`AGT-4`). A dispatcher
disagrees and records why. That pair - *the queue said hold because no cluster
mate had arrived; the dispatcher said release because the customer called* - is
a labelled example of the queue being wrong, and it is the only source of them
we will ever have. `MODEL_AND_DATA_BRIEF.md` §12 schedules override capture in
Phase 3 for exactly this reason: it is what "live authority" is built on.

Which is also why the system's side is **copied as values**. A foreign key to
the snapshot would let the label's other half change, or disappear with a purge,
long after somebody trained on it.

## The refusal that matters

`system_decision_known` is false when no cycle had recorded a decision about the
order. The override still happened and is still recorded in full - but it is not
a labelled disagreement, because there is nothing to disagree with, and
`labelled_overrides` excludes it. Treating an unknown as an implied hold would
manufacture labels out of the queue's silence, and they would be
indistinguishable from real ones by the time anyone trained on them.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

ACTION_RELEASE = "release"
ACTION_HOLD = "hold"
ACTIONS = (ACTION_RELEASE, ACTION_HOLD)

# The closed vocabulary. Free text is available as a note and is never a
# substitute: a reason nobody can count is a reason nobody will act on, and the
# whole point of CON-3 is that these aggregate into something a model can read.
#
# Deliberately short. A long list is answered by whichever option is nearest the
# cursor, which produces data that looks specific and is not.
REASON_CUSTOMER_CALLED = "customer_called"
REASON_CUSTOMER_WAITING = "customer_waiting_on_site"
REASON_DRIVER_GOING_THAT_WAY = "driver_going_that_way"
REASON_ORDER_IS_WRONG = "order_is_wrong"
REASON_HUB_CONSTRAINT = "hub_constraint"
REASON_SYSTEM_LOOKS_WRONG = "system_looks_wrong"
REASON_OTHER = "other"

REASON_CODES = (
    REASON_CUSTOMER_CALLED,
    REASON_CUSTOMER_WAITING,
    REASON_DRIVER_GOING_THAT_WAY,
    REASON_ORDER_IS_WRONG,
    REASON_HUB_CONSTRAINT,
    REASON_SYSTEM_LOOKS_WRONG,
    REASON_OTHER,
)

# What each one means to a dispatcher, and what it means to a model - they are
# not the same thing, and conflating them is how a reason vocabulary rots.
#
# `system_looks_wrong` is the only code that is a claim about the model itself.
# It is separated from `customer_called` on purpose: both produce a release, but
# one says the queue misjudged the urgency and the other says the world changed
# after it judged correctly. A single "released early" bucket would mix a label
# with a non-label, and M2 would learn from the mixture.
REASON_LABELS: dict[str, str] = {
    REASON_CUSTOMER_CALLED: "Customer called about it",
    REASON_CUSTOMER_WAITING: "Someone is waiting on site",
    REASON_DRIVER_GOING_THAT_WAY: "A driver is going that way anyway",
    REASON_ORDER_IS_WRONG: "The order details are wrong",
    REASON_HUB_CONSTRAINT: "A hub or dock constraint",
    REASON_SYSTEM_LOOKS_WRONG: "The system's call looks wrong",
    REASON_OTHER: "Something else (say what)",
}

# `other` is the escape hatch and must stay one. A note is required with it,
# because an `other` with no note is an override with no reason wearing the
# costume of one - which is the exact thing CON-2 exists to prevent, and the
# form of it a closed vocabulary alone cannot catch.
REASON_CODES_REQUIRING_NOTE = (REASON_OTHER,)


class DispatcherOverride(Base, UUIDPrimaryKeyMixin):
    """One human decision against the queue's, append-only.

    No `TimestampMixin`: nothing here may be updated, so an `updated_at` would
    be a field that can never change - the same misleading signal `REC-1`'s
    table avoids. The trigger in migration `0060` refuses UPDATE and DELETE,
    because a reason editable after the outcome is known is not a label.
    """

    __tablename__ = "dispatcher_overrides"

    hub_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    order_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    overridden_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Copied as values so "who decided this" survives the account being deleted,
    # which is when somebody is most likely to be asking.
    ops_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ops_user_email: Mapped[str] = mapped_column(String(255), nullable=False)

    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The system's side of the label, as values (see the module docstring).
    system_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    system_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system_decision_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cited_snapshot_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    order_status_before: Mapped[str] = mapped_column(String(32), nullable=False)

    @property
    def is_labelled_example(self) -> bool:
        """Whether this row is usable as training data (`CON-3`).

        An override of a decision nobody recorded is a real override and a real
        record. It is not an example of the queue being wrong, because nothing
        says what the queue thought.
        """
        return self.system_decision_known

    @property
    def contradicts_the_system(self) -> bool:
        """Whether the human and the queue actually reached different conclusions.

        Not every override is a disagreement. A dispatcher releasing an order the
        queue had already decided to release - because the cycle had not run yet,
        or they did not know - agrees with it and happened to get there first.
        Counting that as a correction would inflate the disagreement rate, which
        is the number anyone judging the queue will look at first.
        """
        return self.system_decision_known and self.system_action != self.action
