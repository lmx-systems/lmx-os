"""A shop/store location that places orders on behalf of a client."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Shop(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shop_profiles"

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    lat: Mapped[float] = mapped_column(nullable=False)
    lng: Mapped[float] = mapped_column(nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # External POS/DMS identifier for this shop, used to match inbound
    # webhooks/flat files back to a shop_profiles row.
    external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
