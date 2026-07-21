"""A driver assigned to a hub."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Driver(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "drivers"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    vehicle_capacity_units: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="off_shift", nullable=False)
    # off_shift | available | en_route | on_break

    # Drives which pay model, document set, and onboarding path applies -
    # the phased W2 -> 1099 -> gig rollout (docs/NEXT_STEPS.md) branches on
    # this everywhere rather than assuming one classification. Defaults to
    # w2 since that's every driver provisioned so far.
    employment_type: Mapped[str] = mapped_column(String(24), default="w2", nullable=False)
    # w2 | contractor_1099 | gig

    # Real per-driver hourly rate. Null falls back to the global
    # PLACEHOLDER_HOURLY_RATE_CENTS estimate (app/payroll/hours.py) - this
    # column exists so a real rate can be set per driver without a second
    # migration once payroll actually runs on it.
    hourly_rate_cents: Mapped[int | None] = mapped_column(nullable=True)

    # Driver app onboarding (screen 1c, "Vehicle & profile setup"). Nullable
    # because existing/seeded drivers predate this screen; the app should
    # treat a null vehicle_type as "setup incomplete" and route there.
    vehicle_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # car | van | bike
    plate_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_zone: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Profile screen (1r), "Payment method". Last 4 digits only, display
    # metadata - not linked to any real payment processor or payout rail.
    # No account/routing number is ever collected or stored here; wiring an
    # actual payout ledger (docs/NEXT_STEPS.md item 12's earnings caveat) is
    # separate, larger work this deliberately doesn't attempt.
    payment_bank_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
