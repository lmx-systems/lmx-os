"""
A driver's compliance document (license, insurance) - profile screen 1r.

No file-upload pipeline exists (same caveat as Stop.pod_photo_url in
app/models/stop.py) - file_url accepts whatever string the client sends
and stores it verbatim. expires_at is the one field with real behavior:
app/api/driver_routes.py's update_my_availability refuses to set a driver
"available" while any document is expired, matching the wireframe's
annotation ("Document-expiry warnings block going online until renewed").
"""
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class DriverDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "driver_documents"

    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # license | insurance
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
