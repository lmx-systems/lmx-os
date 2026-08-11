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
from dataclasses import dataclass

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


# ---------------------------------------------------------------------------
# PLACEHOLDER terms, for a client whose contract hasn't been negotiated yet
# ---------------------------------------------------------------------------
#
# **These are placeholders in the same sense as PLACEHOLDER_AVERAGE_SPEED_MPH in
# app/gig_platform/economics.py, and they carry the same warning: they are a reasoned
# starting point, not something anybody has agreed to.** They exist because the
# alternative - an empty table - means no breach is ever assessable and the contract is
# unenforced while looking fine, which is a worse kind of wrong than a number that is
# openly provisional. Tracked as an open item (docs/ROADMAP.md E11).
#
# **The targets are derived; the credits are not.**
#
# Targets start from the only tier-timing data that exists, `DEFAULT_HOLD_WINDOW_MINUTES`
# in app/sla/engine.py (spec-confirmed for T1/T2/T3; HOT_SHOT is that module's own local
# guess), and add the work that cannot be skipped:
#
#     floor = hold window + 2 x 8 min on the ground + travel
#     travel for a ~5 mile metro run at 18 mph is about 17 min
#
#              hold      floor     target      headroom
#   HOT_SHOT    2 min    ~35 min    60 min       1.7x
#   T1          8 min    ~41 min    90 min       2.2x
#   T2         90 min    ~123 min  180 min       1.5x
#   T3      1,080 min  ~1,113 min 1,440 min      1.3x
#
# Two of those three inputs are themselves placeholders, so the floors inherit that -
# which is exactly why the targets carry headroom rather than sitting on the computed
# minimum. A target we breach routinely is a credit schedule that bleeds money for a
# service level nobody sold.
#
# **The credit percentages have no derivation at all.** They encode one commercial
# judgement - the more a client paid for speed, the more of it back when we miss - and
# T3 deliberately has a target with no teeth, which is an ordinary contract shape and is
# recorded as a real term rather than an absence. Replace them with what customer #1
# actually signs (B2).


@dataclass(frozen=True)
class PlaceholderTerm:
    sla_tier: str
    delivery_target_minutes: int
    credit_percent: int


PLACEHOLDER_SLA_TERMS: tuple[PlaceholderTerm, ...] = (
    PlaceholderTerm("HOT_SHOT", 60, 100),
    PlaceholderTerm("T1", 90, 50),
    PlaceholderTerm("T2", 180, 25),
    PlaceholderTerm("T3", 1440, 0),
)
