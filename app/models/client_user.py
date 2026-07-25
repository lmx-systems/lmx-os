"""
A single named login at a client company (docs/ROADMAP.md C4). Replaces
the one-login-per-client stopgap that used to live inline on
app/models/client.py (portal_email/portal_password_hash) - see that
model's docstring, which promised to "split out if/when
multi-user-per-client happens." This is that split.

Same password+JWT shape as app/models/ops_user.py, deliberately mirrored,
but scoped to a single client_id (an ops user is fleet-wide; a client
user only ever sees their own company's orders/invoices). Multiple rows
per client are the whole point - e.g. an accounts-payable contact and an
operations contact at the same warehouse, the example C4 calls out.
"""
from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# admin: everything a member can do, plus managing the other users at
# their own client (invite a colleague, deactivate someone who's left,
# reset a password, change a role). member: read-only access to their
# company's orders/invoices, no user management. Only two roles rather
# than a full matrix, for the same reason app/models/ops_user.py stops at
# admin/viewer - user-management-vs-not is the actual line the portal's UI
# draws today; a finer-grained model (e.g. an AP contact who sees only
# invoices, an ops contact who sees only orders) is a real later decision,
# not a currently-needed one.
CLIENT_ADMIN_ROLE = "admin"
CLIENT_MEMBER_ROLE = "member"
CLIENT_USER_ROLES = (CLIENT_ADMIN_ROLE, CLIENT_MEMBER_ROLE)


class ClientUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_users"

    client_id: Mapped[UUID] = mapped_column(ForeignKey("clients.id"), nullable=False)
    # Globally unique, not just per-client - a portal login is an email +
    # password with no company field, so two different clients can't share
    # an address without making login ambiguous. Same constraint the old
    # inline Client.portal_email had.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=CLIENT_ADMIN_ROLE, nullable=False)
    # A revocation switch that keeps the row (and its audit trail) - e.g. a
    # contact who's left the client. Checked on every request, not just at
    # login (app/client_auth/dependencies.py), so deactivating someone
    # takes effect immediately instead of waiting for their JWT to expire,
    # the same tradeoff app/ops_auth/dependencies.py already makes.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
