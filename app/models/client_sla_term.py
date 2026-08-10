"""
The delivery commitment a credit is owed against (docs/ROADMAP.md W3, story DO-3).

**W3 asks for credits "computed from order-level SLA outcomes", and the outcome did not
exist.** `app/sla/engine.py` defines HOLD windows - how long an order may wait before it
is released to a driver - and nothing anywhere defines a delivery commitment. `hold_deadline`
is when we must set off, not when the customer gets their part. `promised_at` exists but is
only populated when a source hands us one, which for an LMX-owned order is never. So
"delivered late" was not a computable fact, and a credit schedule alone would have been a
penalty with no trigger.

This table supplies the missing half, and supplies it as **contract data rather than a
constant we picked**. That distinction is the same one E2/E5/E10 are still open on: a
number nobody has agreed to is not a business rule, and hardcoding one here would invent
LMX's service level in a Python file. Each row is per client and per tier, because that is
how it is actually negotiated - a distributor paying for T1 has bought a different promise
from one on T3, and two distributors on T1 may still have signed different papers.

**A client with no term for a tier is not credited, and that is reported rather than
silently treated as zero** (app/billing/credits.py). "We owe nothing" and "nobody wrote
down what we promised" are different answers, and only one of them is safe to put on a
statement.
"""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ClientSlaTerm(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_sla_terms"
    __table_args__ = (
        UniqueConstraint("client_id", "sla_tier", name="uq_client_sla_terms_client_tier"),
    )

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    # Plain string, matching ClientRate's reasoning: a new tier shouldn't need an enum
    # migration before terms can be agreed for it.
    sla_tier: Mapped[str] = mapped_column(String(16), nullable=False)

    # The promise, measured from when the order reached us to when it was delivered.
    # From receipt rather than from pickup because receipt is the moment the client can
    # point at - they know when they sent it, and they do not know when our driver
    # happened to collect it. It is also the only timestamp guaranteed present on every
    # order regardless of path.
    delivery_target_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # What a breach costs, as a percentage of that order's fee. Percent rather than a flat
    # sum so the credit scales with what was charged - a $12 drop and a $90 hot shot are
    # not equally bad to miss, and one flat figure would over-credit the first and
    # under-credit the second.
    credit_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A floor in cents, for contracts written as "the greater of 20% or $5". Optional;
    # most contracts will use one or the other.
    credit_minimum_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A ceiling, because a contract that credits an unbounded amount on a tier we price
    # low is a liability nobody agreed to.
    credit_maximum_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
