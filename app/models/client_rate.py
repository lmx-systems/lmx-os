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
    rate_per_drop_cents: Mapped[int] = mapped_column(Integer, nullable=False)
