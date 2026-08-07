"""
A wholesale distributor client (e.g. a design-partner auto-parts distributor).
Internal naming policy: never hardcode a real client name in code/tests -
use 'Design Partner' / 'Customer Warehouse' as placeholders.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
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

    # Where this client is in the signup funnel (LMX Link, migration 0028).
    # `pending` -> awaiting LMX review after a public self-signup; cannot order.
    # `active`  -> approved, rates set, may submit orders.
    # `rejected`-> declined; stays on record rather than being deleted so a
    #              reapplication can be recognised.
    #
    # Deliberately NOT folded into `active` above, which means "deactivated" -
    # a churned client and a never-approved applicant are different things, and
    # conflating them would make either impossible to query for. Existing rows
    # backfill to `active`, since every client that exists today was onboarded
    # by LMX directly.
    #
    # This reverses docs/ROADMAP.md C5, which recorded self-serve signup as
    # deliberately absent ("a B2B onboarding relationship, not self-serve
    # SaaS"). The approval gate is what preserves that posture: signup is open,
    # but nobody dispatches a van until LMX says so.
    signup_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    # Which terms version they accepted, and when. A checkbox is not the
    # artifact - what they agreed to has to be identifiable later.
    terms_accepted_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Where the applicant says they need deliveries, in their own words
    # (migration 0030). Free text rather than a structured region because hubs
    # have no service-area model yet - so at signup a client is placed on a
    # provisional hub and ops assigns the real one at approval, using this.
    # Structured routing of signups to hubs is deferred until there is more
    # than one hub to route between.
    service_area: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The applicant's phone. Lives here rather than on ClientUser because it is
    # how ops calls the *company* back to qualify them, which is a fact about
    # the relationship rather than about one login.
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Client-facing portal logins now live in their own table
    # (app/models/client_user.py, migration 0019, docs/ROADMAP.md C4) -
    # many named users per client, not the single inline
    # portal_email/portal_password_hash this used to carry. That split is
    # exactly the "if/when multi-user-per-client happens" the previous
    # version of this docstring anticipated.
