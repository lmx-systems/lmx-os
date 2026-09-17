"""The definition a statement was issued under (`STL-2`).

*"Baseline changes need sign-off from both sides and are versioned."*

**Why this became necessary the moment `--recompute` existed.** `STL-1` produces
a statement from costs in the outcome ledger, and `scripts/settle_month.py
--recompute` supersedes those costs when an input changes - a driver's rate
finally recorded, a geofence event arriving late. That is the right behaviour
for the ledger and it means a statement is not reproducible on its own: a
customer who received one figure in August and sees another in September's
recomputation has a dispute, and nothing recorded what the first one rested on.

So issuing a statement freezes its definition and fingerprints the costs it
read, the same shape `REC-1` uses for a dispatch decision. Re-running later
either reproduces the figure or says precisely what moved.

**Append-only, and versioned per client.** A basis is never edited. Changing the
definition issues the next version and marks the previous one superseded, so
"we changed how this was calculated in October" is a row rather than a memory.

**Signatures are recorded, not enforced.** Code cannot make a customer agree to
anything. What it can do is refuse to call a basis agreed until both sides are
on it, and make an unsigned change visible instead of letting it pass as
routine - which is the whole of what "sign-off from both sides" can mean inside
a database.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import UUIDPrimaryKeyMixin

SIDE_LMX = "lmx"
SIDE_CUSTOMER = "customer"
SIDES = (SIDE_LMX, SIDE_CUSTOMER)


class SettlementBasis(Base, UUIDPrimaryKeyMixin):
    """One issued statement's definition, frozen.

    No `TimestampMixin`: nothing here may be updated except the signature
    columns and `superseded_at`, so an `updated_at` would be a field that means
    something different from row to row - the same misleading signal
    `DecisionSnapshot` avoids.
    """

    __tablename__ = "settlement_bases"

    # No foreign key, the reason `experiment_assignments` and `outcome_ledger`
    # both record: this is evidence about an agreement, and it must stay
    # readable if the client row is ever purged.
    client_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Per client, from 1. The number a statement cites, so a customer can ask
    # "which basis was that under" and get an answer shorter than a date range.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # The definition as plain values - how cost was attributed, the arm's terms,
    # which docks were excluded, and the figures that came out. Values rather
    # than references, so a later change to a client row cannot move what this
    # says was agreed.
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # A digest over the (order, cost) pairs the statement read. The ledger is
    # append-only but a cost can be superseded, so this is what turns "the
    # number changed" into "these orders were recosted" - the difference between
    # an argument and a conversation.
    cost_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # The per-order costs themselves, so reproduction can say *which* orders
    # moved rather than only that the digest differs. Kept out of `inputs` on
    # purpose: `inputs_hash` covers the definition, and folding costs into it
    # would make every recomputation look like a change of method - collapsing
    # the one distinction this table exists to preserve.
    costs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    lmx_signed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lmx_signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    customer_signed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    customer_signed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Set when a later version replaces this one. The row stays: "we changed how
    # this was calculated in October" has to survive the change.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_agreed(self) -> bool:
        """Both sides, or it is not agreed. One signature is a draft."""
        return self.lmx_signed_at is not None and self.customer_signed_at is not None

    @property
    def is_live(self) -> bool:
        return self.superseded_at is None
