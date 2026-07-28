"""
A day a hub is not operating - holiday, weather closure, or planned
shutdown (docs/ROADMAP.md R6). Before this, the optimizer and the Learning
Loop's nightly scheduler both assumed every active hub runs every day, so
the first closure would dispatch routes for a shut hub / misfire the
nightly job.

A closure is a *local calendar day* in the hub's own timezone
(Hub.timezone) - see app/hub_calendar.py, which is the only thing that
should turn a UTC instant into "is this hub closed right now". One row per
closed day per hub.
"""
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class HubClosure(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hub_closures"
    __table_args__ = (
        UniqueConstraint("hub_id", "closure_date", name="uq_hub_closure_hub_date"),
    )

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False, index=True)
    closure_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Optional free-text ("Thanksgiving", "Snow day") - operational context
    # for whoever reviews the calendar, not used by any decision logic.
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
