"""
A delivery job sourced from a commercial gig platform (docs/ROADMAP.md G3).

NAMING WARNING. This is unrelated to `app/payroll/gig_pricing.py`,
`app/models/gig_payout.py`, and `app/payroll/payout_provider.py`, which
concern paying a gig-*classified LMX driver* per delivery (A11). This file
is about gig-*platform demand*: work LMX drivers accept on Curri, Dispatch
or Roadie and relay into LMX OS to sequence. The two are opposite
directions - one is how a driver gets paid, the other is where the job came
from - and a grep for "gig" will surface both.

Deliberately NOT an `Order`. An Order has a client, an SLA tier this system
assigned, and a per-drop fee from a rate table we negotiated. A GigJob has
none of those: the platform sets the windows, the platform sets the pay, and
there is no client relationship to bill. Forcing it into Order would mean
nullable-everything plus an SLA tier that lies about who decided it.

The structural difference that matters most, and the reason the batch-hold
queue is not involved: an Order's deadline is enforced *upstream* by
choosing when to release it, whereas a GigJob's windows are hard, external,
and committed the moment the driver accepts. A gig job can never be held for
a cluster-mate. Sequencing what a driver already holds is the whole of the
available optimization here.

Sits upstream of every intake path (notification listener G1, share sheet
G2, manual entry) on purpose - see app/gig_platform/service.py. Building the
store before the intake keeps that decision swappable rather than
architectural.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

# Where the job came from. Plain strings rather than a Postgres enum, same
# as ReturnItem.status and Order.source_system: adding a fourth platform
# should be a deploy, not a migration.
SOURCE_PLATFORMS = ("curri", "dispatch", "roadie")

# How the job got into LMX OS. Recorded so the G1-vs-G2-vs-manual question
# is answered by data - which paths actually carry volume, and how intake
# latency differs between them - rather than by argument. Automated intake
# is deliberately deferred as a 30-driver problem; this column is what makes
# revisiting that decision empirical.
INTAKE_SOURCES = ("manual", "share_sheet", "notification")

# Assignment scope is a PER-JOB PROPERTY, NOT A SYSTEM MODE. Both onboarding
# tracks run simultaneously during any migration, so a single optimizer run
# has to handle both kinds of job at once. The offsite flagged getting this
# wrong as a rewrite.
#
#   pinned_to_driver - gig track. The accepting driver's individual platform
#                      account holds the commitment, so nobody else can
#                      legally or practically carry it. Becomes
#                      `allowedVehicleIndices` on the solver request (G5).
#   any_driver       - carrier track. LMX holds the authority, so the job is
#                      poolable across the fleet.
ASSIGNMENT_SCOPES = ("pinned_to_driver", "any_driver")

# offered  - captured but not yet accepted on the platform. This is G4's
#            input: the accept-gate evaluates a job in this state.
# accepted - the driver took it. Windows are now committed and unholdable.
# declined - evaluated and skipped. Kept rather than deleted, because
#            why we passed is training data as much as why we took it.
GIG_JOB_STATUSES = (
    "offered",
    "accepted",
    "picked_up",
    "delivered",
    "declined",
    "cancelled",
)

# Statuses at or past physical possession. A collected parcel is a hard pin
# regardless of track (offsite decision): reassigning it needs a physical
# handoff between drivers, so pooling only ever buys anything between accept
# and pickup.
_POSSESSION_STATUSES = ("picked_up", "delivered")


class GigJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "gig_jobs"
    __table_args__ = (
        # One row per real job. Intake paths are expected to overlap - a
        # notification and a manual entry can both capture the same offer -
        # so this is the backstop that makes double-intake a conflict to
        # resolve rather than a duplicate to discover later in the data.
        UniqueConstraint("source_platform", "platform_job_ref", name="uq_gig_job_platform_ref"),
        # The two hot reads: a driver's own day, and a hub's volume for the
        # density instrumentation that decides when batching is even possible.
        Index("ix_gig_jobs_driver_pickup", "driver_id", "pickup_window_open"),
        Index("ix_gig_jobs_hub_offered", "hub_id", "offered_at"),
    )

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)

    # Null while a job is still `offered` and nobody has committed to it.
    # Set on acceptance, and for a pinned job it is then immovable.
    driver_id: Mapped[UUID | None] = mapped_column(ForeignKey("drivers.id"), nullable=True)

    source_platform: Mapped[str] = mapped_column(String(24), nullable=False)
    intake_source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")

    # The platform's own identifier, stored exactly as it appeared. Not
    # parsed here: refs like "S4588150.002-HOU1" imply siblings off a shared
    # base, which is the cheapest batching signal available (G8) - but the
    # format differs per platform and guessing at it in the model would bake
    # in an assumption the detector should own.
    platform_job_ref: Mapped[str] = mapped_column(String(64), nullable=False)

    pickup_address: Mapped[str] = mapped_column(String(255), nullable=False)
    pickup_lat: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    pickup_lng: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)

    # Dropoff coordinates are nullable for a real captured reason, not
    # laziness: a collapsed offer card on these platforms hides the dropoff
    # address behind a chevron (G2), so a screenshot capture yields windows,
    # pay and pickup but no precise dropoff. That is enough to *reject* most
    # offers - which is exactly what the accept-gate's first two checks do -
    # and not enough to plan one. A job missing this cannot be sequenced.
    dropoff_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dropoff_lat: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    dropoff_lng: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)

    # Hard, external, and set by the platform. Unlike an Order's
    # hold_deadline these are not ours to move.
    pickup_window_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pickup_window_close: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dropoff_window_open: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dropoff_window_close: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Gross pay as advertised by the platform. Explicitly NOT margin: the
    # pilot's headline $1.75/mi and $70.74/hr exclude driving to the pickup
    # and repositioning afterwards, so an accept decision made on this number
    # alone will take money-losing work. The deadhead model (G7) is what
    # turns this into something decidable.
    pay_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_miles: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)

    assignment_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="pinned_to_driver")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="offered")

    # When the platform surfaced the offer, as distinct from when we recorded
    # it. The gap between this and pickup_window_open is the open question of
    # whether intake latency is the binding constraint: the one offer we have
    # a screenshot of arrived with four minutes left of a seventy-minute
    # window. Logging it from day one is what answers that with a
    # distribution instead of an anecdote.
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Whatever the intake path actually captured, kept verbatim - same
    # convention as Order.raw_payload. A vision extraction or a notification
    # payload will carry fields no column here anticipated, and discarding
    # them makes an extraction bug unreproducible after the fact.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    @property
    def is_pinned_to_driver(self) -> bool:
        """Whether this job can be reassigned to a different driver.

        Two independent reasons to pin, and either is sufficient. The
        contractual one is the gig track, where an individual's platform
        account holds the commitment. The physical one applies on *any*
        track: once a parcel has been collected it is in one specific
        vehicle, and moving the job means moving the box.
        """
        return self.assignment_scope == "pinned_to_driver" or self.status in _POSSESSION_STATUSES

    @property
    def is_sequenceable(self) -> bool:
        """Whether there is enough here to plan a route leg.

        A collapsed-card capture with no dropoff coordinates can still be
        evaluated and rejected, but it cannot be placed in a day.
        """
        return self.dropoff_lat is not None and self.dropoff_lng is not None
