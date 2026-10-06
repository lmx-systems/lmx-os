"""
An internal LMX ops user (dashboard/) - replaces the shared X-API-Key
stopgap (docs/ROADMAP.md S1) with a real per-account login, the same
password+JWT shape app/models/client_user.py uses for the client portal.
Not scoped to a hub - ops staff need cross-hub visibility, matching how
the dashboard itself already works (paste any hub UUID, no restriction).
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Three roles, one line between each (decided 5 October 2026):
#
#   admin       configures the hub - clients, rates, terms, rules, closures,
#               onboarding people, reviewing documents and dock logs, payroll,
#               approving the learning loop's proposals - and everything below.
#   dispatcher  does the day's order work: releases and holds, cancels, resolves
#               a failed delivery, records what happened after a late one,
#               labels a dock, answers a linkage flag, forces a dispatch cycle,
#               closes out a return, takes an order in by hand.
#   viewer      reads. Every screen, no buttons.
#
# Before the middle tier existed "viewer" was the dispatcher's account by
# intent - the override and consequence routes were open to any session so a
# dispatcher could run a day - while the console badged it "view only".
# Nothing is deployed yet, so adding the tier costs nothing; a finer matrix is
# still a gap to revisit if a reason for one shows up.
ADMIN_ROLE = "admin"
DISPATCHER_ROLE = "dispatcher"
VIEWER_ROLE = "viewer"
OPS_ROLES = (ADMIN_ROLE, DISPATCHER_ROLE, VIEWER_ROLE)
# Who may do the day's writes: a dispatcher, and an admin, who can do everything.
DISPATCH_ROLES = frozenset({ADMIN_ROLE, DISPATCHER_ROLE})


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
