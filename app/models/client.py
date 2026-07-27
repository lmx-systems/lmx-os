"""
A wholesale distributor client (e.g. a design-partner auto-parts distributor).
Internal naming policy: never hardcode a real client name in code/tests -
use 'Design Partner' / 'Customer Warehouse' as placeholders.
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class Client(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clients"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Which POS/DMS system this client's shops report through.
    # Phase 1 priority: epicor. Then mam, asa, flat_file.
    pos_system: Mapped[str] = mapped_column(String(32), nullable=False, default="flat_file")
    active: Mapped[bool] = mapped_column(default=True)

    # Client-facing portal logins now live in their own table
    # (app/models/client_user.py, migration 0019, docs/ROADMAP.md C4) -
    # many named users per client, not the single inline
    # portal_email/portal_password_hash this used to carry. That split is
    # exactly the "if/when multi-user-per-client happens" the previous
    # version of this docstring anticipated.
