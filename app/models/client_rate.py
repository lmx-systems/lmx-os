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
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ClientRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_rates"
    __table_args__ = (UniqueConstraint("client_id", "sla_tier", name="uq_client_rates_client_tier"),)

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    sla_tier: Mapped[str] = mapped_column(String(16), nullable=False)  # T1 | T2 | T3 | HOT_SHOT

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
