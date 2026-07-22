"""
An internal LMX ops user (dashboard/) - replaces the shared X-API-Key
stopgap (docs/ROADMAP.md S1) with a real per-account login, the same
password+JWT shape app/models/client.py's portal_email/
portal_password_hash already uses for the client portal. Not scoped to a
hub - ops staff need cross-hub visibility, matching how the dashboard
itself already works (paste any hub UUID, no restriction).
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# admin: everything, including mutating actions (run a cycle, onboard a
# client, revoke a driver device). viewer: read-only across the
# dashboard - can't be given by anyone but an admin, and only two roles
# rather than a full permissions matrix, since that's the actual line the
# dashboard's own UI draws today (OperationsPanel/OnboardClientForm vs.
# everything else) - a finer-grained model is a real gap to revisit if a
# reason for one ever shows up, not a currently-needed one.
ADMIN_ROLE = "admin"
VIEWER_ROLE = "viewer"
OPS_ROLES = (ADMIN_ROLE, VIEWER_ROLE)


class OpsUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ops_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Defaults to admin - every ops user created before this field existed
    # was already effectively unrestricted, so defaulting new/existing
    # rows to anything less would silently take capability away rather
    # than add a real, deliberate restriction.
    role: Mapped[str] = mapped_column(String(16), default=ADMIN_ROLE, nullable=False)
    # A revocation switch without deleting the row/losing the audit trail
    # of who this was - e.g. an ops staffer who's left. Checked at login
    # and on every request (app/ops_auth/dependencies.py) rather than
    # only at login, so revoking mid-session actually takes effect
    # immediately instead of waiting for the JWT to expire on its own.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
