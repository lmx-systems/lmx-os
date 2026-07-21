"""
An internal LMX ops user (roadmap item S1) - hub staff/admins who use the
orchestrator dashboard. Replaces "everyone shares one X-API-Key" with real
per-user identity and a role.

Deliberately separate from Driver (drivers authenticate with phone+OTP in
the driver app) and from Client.portal_email (a client company's portal
login) - three different audiences, three different auth surfaces, no
shared accounts across them.
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class OpsUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # bcrypt via app/client_auth/passwords.py - same hashing everywhere a
    # password exists in this codebase.
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    role: Mapped[str] = mapped_column(String(16), default="operator", nullable=False)
    # admin    - everything, including /admin/* (client onboarding, user management)
    # operator - day-to-day ops (fleet, hold queue, optimizer triggers), no /admin/*

    # Soft-disable instead of row deletion so audit history (who triggered
    # what) keeps pointing at a real record.
    active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Null = all hubs. Set to scope an operator to a single hub once
    # multi-hub staffing exists; enforcement is a later, contained change
    # (the column exists now so it doesn't need a second migration).
    hub_id: Mapped[UUID | None] = mapped_column(ForeignKey("hubs.id"), nullable=True)
