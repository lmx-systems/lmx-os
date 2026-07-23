"""
A physical LMX delivery hub. Every driver, order, and route is scoped to one.
"""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Hub(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hubs"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")
    lat: Mapped[float] = mapped_column(nullable=False)
    lng: Mapped[float] = mapped_column(nullable=False)
    active: Mapped[bool] = mapped_column(default=True)

    # Two-letter USPS state code (e.g. "CA") - the real, neutral fact
    # state-specific overtime rules need (docs/ROADMAP.md A9,
    # app/payroll/overtime_rules.py), independent of whichever actual
    # rule ends up applying. Nullable: no Hub creation/edit API or UI
    # exists yet (hubs are seed/DB-provisioned only), and every hub
    # predates this column, so it has to start out unset rather than
    # guessed at from lat/lng. Unset = app/payroll/overtime_rules.py's
    # federal-only default applies, same behavior as before this column
    # existed.
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
