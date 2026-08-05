"""
Schemas for the gig-platform demand path (docs/ROADMAP.md G3).

`GigJobIntake` is the one normalized shape every intake path produces -
manual entry today, share-sheet vision extraction (G2) and the Android
notification listener (G1) later. Keeping that contract in one place is the
point of building the store before the intake: swapping or adding a capture
method should not reach past this boundary.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SourcePlatform = Literal["curri", "dispatch", "roadie"]
IntakeSource = Literal["manual", "share_sheet", "notification"]
AssignmentScope = Literal["pinned_to_driver", "any_driver"]
GigJobStatus = Literal["offered", "accepted", "picked_up", "delivered", "declined", "cancelled"]


class GigJobIntake(BaseModel):
    """One captured offer, however it was captured."""

    source_platform: SourcePlatform
    platform_job_ref: str = Field(min_length=1, max_length=64)
    intake_source: IntakeSource = "manual"

    pickup_address: str = Field(min_length=1, max_length=255)
    pickup_lat: float | None = Field(default=None, ge=-90, le=90)
    pickup_lng: float | None = Field(default=None, ge=-180, le=180)

    # Optional because a collapsed offer card hides it (G2). A job without
    # it is still worth recording - it can be evaluated and rejected - but
    # cannot be sequenced into a day.
    dropoff_address: str | None = Field(default=None, max_length=255)
    dropoff_lat: float | None = Field(default=None, ge=-90, le=90)
    dropoff_lng: float | None = Field(default=None, ge=-180, le=180)

    pickup_window_open: datetime
    pickup_window_close: datetime
    dropoff_window_open: datetime | None = None
    dropoff_window_close: datetime | None = None

    pay_cents: int = Field(ge=0)
    distance_miles: Decimal | None = Field(default=None, ge=0)

    # Defaults to the gig track, which is the one that starts immediately and
    # the stricter of the two - a job wrongly marked poolable could be
    # offered to a driver who has no standing to deliver it, while a job
    # wrongly pinned merely forgoes an optimization.
    assignment_scope: AssignmentScope = "pinned_to_driver"

    # When the platform surfaced the offer. Optional because a manual entry
    # after the fact genuinely may not know it; every automated path should
    # always supply it, since the gap to pickup_window_open is the whole
    # intake-latency question.
    offered_at: datetime | None = None

    raw_payload: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _windows_must_be_ordered(self) -> "GigJobIntake":
        """A window that closes before it opens is a capture bug - most
        likely a date rolled over midnight during extraction. Rejecting it
        here keeps it from reaching the accept-gate, where an inverted
        window silently makes every offer look infeasible."""
        if self.pickup_window_close < self.pickup_window_open:
            raise ValueError("pickup_window_close is before pickup_window_open")
        if (
            self.dropoff_window_open is not None
            and self.dropoff_window_close is not None
            and self.dropoff_window_close < self.dropoff_window_open
        ):
            raise ValueError("dropoff_window_close is before dropoff_window_open")
        return self


class GigJobView(BaseModel):
    gig_job_id: str
    hub_id: str
    driver_id: str | None
    source_platform: str
    intake_source: str
    platform_job_ref: str

    pickup_address: str
    pickup_lat: float | None
    pickup_lng: float | None
    dropoff_address: str | None
    dropoff_lat: float | None
    dropoff_lng: float | None

    pickup_window_open: datetime
    pickup_window_close: datetime
    dropoff_window_open: datetime | None
    dropoff_window_close: datetime | None

    pay_cents: int
    distance_miles: Decimal | None
    assignment_scope: str
    status: GigJobStatus

    offered_at: datetime | None
    accepted_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None

    # Both derived rather than stored, so they can never drift from the
    # status/scope they're computed from. See app/models/gig_job.py.
    is_pinned_to_driver: bool
    is_sequenceable: bool


class GigJobStatusUpdate(BaseModel):
    status: GigJobStatus


class MarginalEconomicsView(BaseModel):
    """What a job is really worth once the unpaid legs are counted (G7)."""

    pay_cents: int
    deadhead_miles: float
    engaged_miles: float
    reposition_miles: float
    vehicle_cost_cents: int
    time_cost_cents: int
    total_cost_cents: int
    margin_cents: int
    total_minutes: float
    # Pay per hour across the whole job including deadhead. Expect this to
    # sit well below the pilot's $70.74/hr engaged figure, which counts only
    # pickup-to-dropoff. The gap is the point.
    effective_hourly_cents: int | None


class AcceptVerdictView(BaseModel):
    """The accept-gate's answer (G4)."""

    accept: bool
    # Stable code, not prose - why we passed is training data and needs to
    # be queryable. One of: unreachable | impossible_windows |
    # breaks_commitment | over_capacity | unprofitable | not_sequenceable |
    # acceptable.
    reason: str
    detail: str
    # Absent when the offer was rejected before it was worth costing - the
    # cheap checks short-circuit precisely so this never gets computed for
    # an offer that was never feasible.
    economics: MarginalEconomicsView | None = None


class GigDensityReport(BaseModel):
    """Volume and pairing figures for a hub (docs/ROADMAP.md G12).

    Exists to answer "when does batching become possible" with a
    measurement. `sequenced_share` is the one that matters: the fraction of
    delivered jobs the driver was holding concurrently with another. If it
    stays near zero as drivers are added, that is the finding.
    """

    hub_id: str
    window_days: int

    total_offers: int
    offers_per_day: float
    accepted_count: int
    declined_count: int
    delivered_count: int
    # Null rather than 0.0 when there were no offers at all - "we accepted
    # none of nothing" is not a 0% acceptance rate.
    acceptance_rate: float | None

    active_driver_count: int
    jobs_per_driver_per_day: float | None
    # Rich's Austin pilot, so every reading renders against the control group.
    pilot_jobs_per_driver_per_day: float

    # Denominator and numerator exposed alongside the ratio: with volumes
    # this small, "1 of 3" and "33%" are very different things to read, and
    # only one of them invites over-interpretation.
    measurable_delivered_count: int
    sequenced_delivered_count: int
    sequenced_share: float | None
