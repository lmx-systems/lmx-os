"""
Per-delivery instant payout log for gig-classified drivers (docs/ROADMAP.md
A11) - one row per completed dropoff stop a gig driver gets paid for. Its
own table, not folded into an existing one: nothing else in this codebase
carries a per-stop dollar amount or a payout-provider status/reference,
and unique(stop_id) is the real idempotency backstop that keeps a stop
from ever being paid twice (complete_stop's own idempotent early-return
already prevents a retried request from reaching this code path at all,
but this constraint holds even if that ever changes).
"""
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class GigPayout(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "gig_payouts"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    stop_id: Mapped[UUID] = mapped_column(ForeignKey("stops.id"), nullable=False, unique=True)

    # app/payroll/gig_pricing.py's estimate at the moment this stop was
    # completed - a placeholder formula (see that module's docstring), but
    # a real, fixed dollar amount once computed here, not recomputed later.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    # pending (row created, provider call not yet attempted) | paid (a real
    # Stripe transfer succeeded) | stub (StubPayoutProvider ran - no Stripe
    # account configured) | skipped_no_payout_account (this driver has no
    # Driver.stripe_connect_account_id yet - owed, not paid) | failed (a
    # real provider call errored).
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    stripe_transfer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
