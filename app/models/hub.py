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
