"""
Money collected at the door, and money disputed there (docs/ROADMAP.md W2, story
DO-8, training case E3).

**Whose money this is, is the fact everything else follows from.** A COD amount is the
DISTRIBUTOR'S invoice to their own customer. LMX is carrying it, not charging it - it is
a different number from `Order.fee_cents` (what LMX bills the client) and from
`quoted_amount_cents` (what the client was quoted). Which means:

  - **A dispute is not ours to settle.** It is between the distributor and their
    customer, and the driver is not a party to it. That is why the rule is "never
    negotiate" and why it has to be enforced in software rather than in training: a
    driver with a field to type a lower amount into has been handed an authority nobody
    gave them, over money that isn't LMX's.
  - **Custody matters.** Cash in a van is a liability question (R1), so every collection
    is a row naming the driver who took it, not a boolean on the stop.

`COD_DISPUTE` has existed as a stop failure reason since the driver app was built, for a
payment mode the order object could not express - `PayerType` had no COD value, so a
driver could flag a COD dispute on an order that was never COD. This table and
`Order.payer_type = 'cash_on_delivery'` are the other half of that.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# What a driver can actually take at a door. No card: taking a card payment on the
# distributor's behalf makes LMX a payment processor for someone else's transaction,
# which is a compliance question (PCI, money transmission) and not one to answer by
# adding an enum value.
COD_METHODS = ("cash", "check")

OUTCOME_COLLECTED = "collected"
OUTCOME_DISPUTED = "disputed"
OUTCOMES = (OUTCOME_COLLECTED, OUTCOME_DISPUTED)


class CodCollection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cod_collections"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    # The stop it happened at, so a commingled dropoff's collections are separable.
    stop_id: Mapped[UUID] = mapped_column(ForeignKey("stops.id"), nullable=False)
    # Who had the cash. The custody trail, and the reason this isn't a stop column.
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    # Denormalised so the dispute report can group by account without joining through
    # orders on every row - and so a report stays right if an order is later reassigned.
    client_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("clients.id"), nullable=True, index=True
    )
    shop_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("shop_profiles.id"), nullable=True, index=True
    )

    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # What was owed, copied at the moment of the event. Not read live off the order: if
    # the distributor later corrects their invoice, what the driver was told to collect
    # must not change retroactively - that is the number a dispute is about.
    amount_due_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # **Only ever equal to amount_due_cents or absent.** There is no partial payment,
    # because accepting one is negotiating. Kept as its own column rather than inferred
    # so a future partial-payment policy is a schema-visible decision rather than a
    # reinterpretation of old rows.
    amount_collected_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # What the customer said, in the driver's words. Free text on purpose: the useful
    # signal is a pattern across an account ("they always say it's the wrong price"),
    # and a dropdown written now would decide in advance what patterns can be seen.
    dispute_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the distributor was told. Null means the escalation didn't go out - a real
    # state worth being able to find, since the whole promise of "one tap escalates" is
    # that somebody hears about it.
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
