"""
Per-client, per-tier billing rate (Phase 8). What LMX charges a client per
delivered drop - $18.00/drop standard, a separate (typically higher) rate
for HOT_SHOT, set once at client onboarding (see app/api/admin_routes.py).

sla_tier is a plain string here, not a foreign key into the Postgres
`sla_tier` enum Order.sla_tier uses - matching how the rest of the
codebase already treats tier as freeform text outside that one strict
column (HeldOrder, BatchDecision, StopCandidate all do the same). Keeps
this table decoupled from the enum, so a future tier doesn't need an
enum migration before a rate can be configured for it.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ClientRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One *version* of one tier's rate for one client.

    **Append-only since migration 0045 (T2.5 A1).** Before it, a row was one tier's rate
    and changing a price UPDATEd it in place. Orders were safe - `fee_cents` and
    `fee_breakdown` are frozen at ingestion - but the card's own history was destroyed, so
    *"what was this client's T2 rate on 15 August"* had no answer, and neither did
    *"which rate priced this drop"*. That is the audit trail `H1` asks for, and it cannot
    be reconstructed from a row that was overwritten.

    A rate change now inserts a new row with a later `effective_from`. Pricing reads the
    latest version whose `effective_from` has passed, so the old row stays exactly as it
    was on the day it applied.
    """

    __tablename__ = "client_rates"
    __table_args__ = (
        # Was unique on (client_id, sla_tier) - which is precisely what made versioning
        # impossible, since a second version is a second row for the same pair. The
        # effective date is what distinguishes them now, and two versions of one tier
        # starting at the same instant is still a contradiction worth refusing.
        UniqueConstraint(
            "client_id", "sla_tier", "effective_from", name="uq_client_rates_client_tier_effective"
        ),
    )

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    sla_tier: Mapped[str] = mapped_column(String(16), nullable=False)  # T1 | T2 | T3 | HOT_SHOT

    # When this version starts applying. Pricing takes the newest version at or before the
    # moment it prices, so a future date schedules a rate change rather than performing one
    # - which is what lets a negotiated increase be entered when it is agreed rather than
    # remembered on the morning it starts.
    #
    # Backfilled from `created_at` for rows that predate 0045. That is approximate for any
    # rate edited before the migration, and deliberately not dressed up as more: the
    # instant an overwritten version started is not recoverable.
    # Defaulted to "now" at both layers rather than required at every call site. A rate
    # created without an explicit date is effective immediately, which is both the correct
    # meaning and the safe one: the alternative is a NOT NULL column that any forgotten
    # constructor turns into an IntegrityError at runtime, and the forgotten constructor is
    # more likely than the deliberate backdate.
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # The flat per-drop price, and still the only field most contracts use. Now the BASE
    # of an additive rate rather than the whole of it (docs/ROADMAP.md F5, migration 0039).
    rate_per_drop_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    # ------------------------------------------------------------------
    # Rate-table components (F5). A price is base + sum(per-unit x units), floored at
    # `minimum_charge_cents`.
    #
    # **Additive rather than a mutually-exclusive `basis` enum, because that is how
    # courier rates are actually written.** Nobody quotes "per mile" alone; they quote
    # "$8 plus $1.50 a mile, minimum $12". An enum would force every hybrid contract to be
    # approximated, and the approximation would show up as an argument about an invoice.
    #
    # All default to zero, so every existing row keeps pricing exactly as before.
    # ------------------------------------------------------------------
    rate_per_mile_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Per line item on the order - "pieces" in the trade.
    rate_per_piece_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_per_weight_unit_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A floor. Distinct from the base: a base is added to everything, a minimum only bites
    # on the short cheap drops, and a contract can name both.
    minimum_charge_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
