"""Schemas for internal/admin-only endpoints (app/api/admin_routes.py)."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ShopOnboardingInput(BaseModel):
    name: str
    address: str
    lat: float
    lng: float
    external_ref: str
    phone: str | None = None


class RateOnboardingInput(BaseModel):
    sla_tier: str  # T1 | T2 | T3 | HOT_SHOT - not enum-validated, see ClientRate's docstring
    rate_per_drop_cents: int


class ClientOnboardingBody(BaseModel):
    """
    Minimal client onboarding (Phase 8) - creates a Client, its shop(s),
    its per-tier billing rates, and its portal login credentials in one
    action, since there's no admin UI yet to do this as separate steps.
    """

    hub_id: str
    name: str
    pos_system: str = "flat_file"
    shops: list[ShopOnboardingInput]
    rates: list[RateOnboardingInput]
    # The client's first portal login, created as an admin client user
    # (docs/ROADMAP.md C4) who can then invite the rest of the client's
    # team themselves. portal_user_name names that person; it defaults to
    # the company name when omitted (there's often just one contact at
    # onboarding time, named later).
    portal_email: str
    portal_password: str
    portal_user_name: str | None = None


class ClientOnboardingResult(BaseModel):
    client_id: str
    shop_ids: list[str]


class ResolveFailedOrderBody(BaseModel):
    """How ops resolves a delivery_failed order (docs/ROADMAP.md R5).
    action is validated against app/delivery/resolution.RESOLUTION_ACTIONS
    at the route layer (same convention as VALID_SLA_TIERS)."""

    action: str  # redeliver | return_to_shop | cancel
    note: str | None = None


class OrderResolutionResult(BaseModel):
    order_id: str
    status: str
    delivery_attempts: int
    action: str


class HubClosureBody(BaseModel):
    """A day a hub is closed (docs/ROADMAP.md R6) - a local calendar date in
    the hub's own timezone."""

    closure_date: date
    reason: str | None = None


class HubClosureView(BaseModel):
    closure_date: date
    reason: str | None
    created_at: str


class UrgencyRuleBody(BaseModel):
    """An orchestrator-authored urgency rule (docs/ROADMAP.md W6): when an
    order's raw_payload[match_key] equals match_value (case-insensitive),
    force `tier`. tier is validated against the real tiers at the route
    layer (VALID_SLA_TIERS)."""

    match_key: str = Field(min_length=1, max_length=64)
    match_value: str = Field(min_length=1, max_length=120)
    tier: str


class UrgencyRuleUpdateBody(BaseModel):
    enabled: bool


class UrgencyRuleView(BaseModel):
    rule_id: str
    match_key: str
    match_value: str
    tier: str
    enabled: bool


class ProposedRuleView(BaseModel):
    """A Learning-Loop proposal awaiting human review (docs/ROADMAP.md I2)."""

    rule_id: str
    rule_type: str
    scope: dict
    proposed_change: dict
    confidence: float
    supporting_annotation_count: int
    status: str
    created_at: str


class ProposedRuleApprovalResult(BaseModel):
    proposed_rule_id: str
    status: str
    # Set only on approval - the active_rules row the proposal became.
    active_rule_id: str | None = None


class DriverPayrollSubmission(BaseModel):
    driver_id: str
    driver_name: str
    employment_type: str
    # w2 drivers are paid monthly, 1099/gig weekly (app/payroll/hours.py) -
    # per-submission, not per-run, since one hub can mix employment types
    # with different period lengths in the same payroll run.
    period_start: str
    period_end: str
    hours_worked: float
    overtime_hours: float
    estimated_pay_cents: int
    provider_reference: str | None = None


class PayrollRunResult(BaseModel):
    hub_id: str
    engine: str
    submissions: list[DriverPayrollSubmission]


# ---------------------------------------------------------------------------
# Driver compliance document review (docs/ROADMAP.md R4)
# ---------------------------------------------------------------------------


class PendingDriverDocumentView(BaseModel):
    """One document awaiting an ops verdict.

    Carries the driver's name and the claimed date because the review IS the
    comparison: the reviewer opens the file, reads the expiry off it, and either
    confirms or contradicts what the driver said. A queue that showed only a file
    link would make them go and look the driver up.
    """

    document_id: str
    driver_id: str
    driver_name: str
    doc_type: str
    claimed_expires_at: date
    file_url: str | None
    review_status: str
    uploaded_at: datetime


class DriverDocumentReviewBody(BaseModel):
    """An ops verdict on one document.

    `verified_expires_at` is required on approval and is what the reviewer read off
    the document - NOT a copy of the driver's claim. That is the entire point of
    the field: if approving just accepted the claimed date, this review would be a
    rubber stamp on self-attested data and the hole R4 exists to close would still
    be open one step further along.
    """

    decision: Literal["verify", "reject"]
    verified_expires_at: date | None = None
    rejection_reason: str | None = None


class DriverDocumentReviewResult(BaseModel):
    document_id: str
    doc_type: str
    review_status: str
    verified_expires_at: date | None
    # Whether this driver can now go on shift. Answered here so a reviewer working
    # a queue can see that clearing the second of two documents actually unblocked
    # someone, rather than having to go and check.
    driver_can_go_on_shift: bool
    outstanding_problems: list[str]
