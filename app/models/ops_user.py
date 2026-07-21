"""
An internal LMX ops user (dashboard/) - replaces the shared X-API-Key
stopgap (docs/ROADMAP.md S1) with a real per-account login, the same
password+JWT shape app/models/client.py's portal_email/
portal_password_hash already uses for the client portal. Not scoped to a
hub - ops staff need cross-hub visibility, matching how the dashboard
itself already works (paste any hub UUID, no restriction). No roles yet:
every ops user can do everything any other ops user can - a real gap,
same one the roadmap item itself names as still open.
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class OpsUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # A revocation switch without deleting the row/losing the audit trail
    # of who this was - e.g. an ops staffer who's left. Checked at login
    # and on every request (app/ops_auth/dependencies.py) rather than
    # only at login, so revoking mid-session actually takes effect
    # immediately instead of waiting for the JWT to expire on its own.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
