"""Schemas for internal/admin-only endpoints (app/api/admin_routes.py)."""
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


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


class OrderCancellationResult(BaseModel):
    """Dispatch cancelled a live order (app/orders/cancellation.py).

    `how` says what had to be undone: `before_collection` (it was only held),
    `offer_withdrawn` (a driver's open offer was taken back) or `stops_removed`
    (its stops came off a route under way).
    """

    order_id: str
    status: str
    how: str


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


# ---------------------------------------------------------------------------
# COD disputes (docs/ROADMAP.md W2)
# ---------------------------------------------------------------------------


class ShopDisputeRowView(BaseModel):
    shop_id: str | None
    shop_name: str
    client_id: str | None
    client_name: str
    disputed_count: int
    # Beside the disputes on purpose: three out of four deliveries and three out of three
    # hundred are different facts, and a report that counts only failures makes them look
    # the same.
    collected_count: int
    disputed_amount_cents: int
    dispute_rate: float


class AdminClientView(BaseModel):
    """One client on a hub, enough to pick one and know what you picked.

    **Nothing listed clients.** Four endpoints take a `client_id` — rates, SLA
    terms, invoice generation — and the only way to obtain one was to create a
    client or read it out of the database, so every one of them was unreachable
    in practice rather than merely unsurfaced
    (`docs/ROADMAP_AUDIT_2026-09.md`). Same shape as the driver-device gap: the
    action existed and the thing that hands you its argument did not.

    `signup_status` and `active` are both here and are not the same question. A
    churned client is `active=false`; a never-approved applicant is
    `signup_status='pending'`. `Client.active`'s own comment says why conflating
    them would make either impossible to query for, and a picker that showed one
    without the other would invite somebody to set a rate on an applicant who
    cannot order.
    """

    client_id: str
    name: str
    pos_system: str
    active: bool
    signup_status: str
    # How many tiers this client has a rate for. Zero is the state that matters:
    # an approved client with no rate table cannot be invoiced, and nothing else
    # on this row would say so.
    rate_tiers: int
    # How many tiers this client has an agreed SLA term for. Zero is a different
    # state from `rate_tiers: 0` and is not necessarily wrong: a client we have
    # priced but promised no delivery window to is uncredited by design, and
    # `credit_exposure` reports that separately rather than as nothing owed.
    # Surfaced because the picker is where somebody decides which client to
    # look at, and "priced, no promise" is exactly the row worth opening.
    sla_term_tiers: int = 0


class AdminDriverDeviceView(BaseModel):
    """One device a driver has signed in on, as an admin needs to see it.

    Separate from `DriverDeviceView` because the two answer different questions.
    The driver's own list answers *"where am I signed in"* and carries
    `is_current`, which is meaningless to a third party. An admin is answering
    *"which of these is the phone in the taxi"*, so this carries `registered_at`
    and `revoked_at` instead — when it appeared, and whether somebody has
    already dealt with it.

    **Revoked devices are included**, which the driver-facing list excludes.
    *"No device"* and *"a device revoked on Tuesday"* are different answers to
    *"why can this driver not sign in"*, and only one of them is somebody's
    mistake.
    """

    device_id: str
    device_name: str | None
    last_seen_at: datetime
    registered_at: datetime
    revoked_at: datetime | None


class AdminDriverView(BaseModel):
    """One driver on a hub's roster, switched off or not.

    The console's driver pickers read the hub's fleet state, which holds only
    drivers who have gone on shift since Redis last started, and never anybody
    who has left. Switching a driver off, and back on, needs the roster itself.
    """

    driver_id: str
    name: str
    phone: str
    status: str
    is_active: bool
    deactivated_at: datetime | None


class SupportThreadView(BaseModel):
    """One driver's support thread, as the inbox lists it: newest first, and
    whether the last word was the driver's, which is what needs an answer."""

    driver_id: str
    driver_name: str
    last_body: str
    last_at: datetime
    awaiting_reply: bool


class SupportMessageView(BaseModel):
    message_id: str
    from_driver: bool
    body: str
    created_at: datetime
    # The dispatcher who wrote a reply. Null on the driver's own messages.
    sent_by: str | None


class SupportReplyBody(BaseModel):
    body: str = Field(min_length=1, max_length=1600)


class CodDisputeReportView(BaseModel):
    window_start: datetime
    window_end: datetime
    disputed_count: int
    collected_count: int
    disputed_amount_cents: int
    # Worst first - the point of the report is which conversation to have.
    shops: list[ShopDisputeRowView]
    # Disputes the distributor was never told about. Surfaced separately because it breaks
    # the promise the feature makes ("one tap escalates"), and folded into a total it would
    # disappear.
    unescalated_count: int
    # Why. With no mail server configured every dispute is un-escalated, and that is
    # one deployment-wide fact rather than N per-account failures.
    email_configured: bool


# ---------------------------------------------------------------------------
# Rate tables and SLA terms (docs/ROADMAP.md F5, W3)
# ---------------------------------------------------------------------------


class ClientRateBody(BaseModel):
    """One tier's price for one client.

    Components are ADDITIVE - `fee = base + miles*per_mile + pieces*per_piece +
    weight*per_weight`, floored at `minimum_charge_cents`. Written that way because courier
    rates are quoted that way ("$8 plus $1.50 a mile, minimum $12"), and a
    mutually-exclusive basis would force every hybrid contract to be approximated.
    """

    sla_tier: str
    rate_per_drop_cents: int = Field(ge=0)
    rate_per_mile_cents: int = Field(default=0, ge=0)
    rate_per_piece_cents: int = Field(default=0, ge=0)
    rate_per_weight_unit_cents: int = Field(default=0, ge=0)
    minimum_charge_cents: int | None = Field(default=None, ge=0)


class ClientRateView(ClientRateBody):
    rate_id: str


class ClientSlaTermBody(BaseModel):
    """What we promised, and what missing it costs.

    **The target is why this exists.** Credits are owed against a delivery commitment and
    none was recorded anywhere: `app/sla/engine.py` defines HOLD windows (when we must set
    off), not delivery times. Without this a credit schedule would be a penalty with no
    trigger.

    Measured from when the order reached us, because that is the moment the client can
    point at - they know when they sent it and not when our driver happened to collect it.
    """

    sla_tier: str
    delivery_target_minutes: int = Field(gt=0)
    # Percentage of the order's fee, so the credit scales with what was charged.
    credit_percent: int = Field(default=0, ge=0, le=100)
    credit_minimum_cents: int | None = Field(default=None, ge=0)
    credit_maximum_cents: int | None = Field(default=None, ge=0)


class ClientSlaTermView(ClientSlaTermBody):
    term_id: str


class DriverOnboardingBody(BaseModel):
    """Provision a driver (`docs/ROADMAP_AUDIT_2026-09.md`).

    Nothing created a `Driver` before this — every row was a hand-written
    insert, while `app/api/driver_routes.py`'s sign-in said in its own comment
    that *"drivers are provisioned by ops, not self-registered"*. The
    provisioning it refers to did not exist.

    **`vehicle_capacity_units` is required and has no default here**, though the
    column defaults to 1. The optimizer's capacity check reads it, so a driver
    provisioned without thinking about it gets one order at a time — which is
    the safe direction and the wrong answer. Asking makes it a decision.

    `hourly_rate_cents` is optional because `scripts/set_driver_rate.py` already
    owns it and a second writer would be a second place for the number to be
    wrong. Left null, payroll falls back to `PLACEHOLDER_HOURLY_RATE_CENTS` and
    says so.
    """

    hub_id: str
    name: str = Field(min_length=1, max_length=120)
    # How dispatch reaches the driver. No longer how they sign in - that is a
    # code from the console (app/driver_auth/sign_in_codes.py) - but still
    # unique at the database since migration `0063`.
    phone: str = Field(min_length=5, max_length=32)
    vehicle_capacity_units: int = Field(ge=1, le=500)
    employment_type: str = Field(description="w2 | contractor_1099 | gig")
    vehicle_type: str | None = None
    plate_number: str | None = Field(default=None, max_length=32)
    hourly_rate_cents: int | None = Field(default=None, ge=0)


class DriverSignInCodeIssued(BaseModel):
    """A sign-in code, shown once to the ops user who issued it.

    The only time the code leaves the server: only a keyed hash is stored, so
    nobody can read it back later. Lost before the driver used it, the answer is
    to issue another, which retires this one.
    """

    code: str
    display: str
    qr_payload: str
    expires_at: datetime


class DriverSignInCodeView(BaseModel):
    """One code in a driver's history, without the code.

    The record of who let which phone in, and when. `status` says what happened
    to it: `redeemed`, `replaced` by a newer code, `expired`, or `open`.
    """

    issued_at: datetime
    issued_by: str | None
    expires_at: datetime
    status: str
    redeemed_at: datetime | None
    redeemed_device_id: str | None


class DriverOnboardingResult(BaseModel):
    driver_id: str
    name: str
    phone: str
    employment_type: str
    vehicle_capacity_units: int
    hourly_rate_is_placeholder: bool
    # The driver's first sign-in code, so onboarding ends with the driver able
    # to sign in rather than with a second trip to another screen.
    sign_in_code: DriverSignInCodeIssued


def _known_time_zone(value: str | None) -> str | None:
    """A hub's clock decides its pay periods, invoices and closed days
    (app/hub_calendar.py), so an unknown zone would fail there, far from
    whoever typed it."""
    if value is None:
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"{value!r} is not a time zone - use a name like America/New_York"
        ) from None
    return value


class HubCreateBody(BaseModel):
    """Create a hub (`docs/ROADMAP_AUDIT_2026-09.md`).

    `Hub.state_code`'s own comment said it: *"no Hub creation/edit API or UI
    exists yet (hubs are seed/DB-provisioned only)"*. So the column that picks a
    driver's overtime rule could not be set, and every hub was federal-only for
    ever regardless of where it is.

    `state_code` is optional here rather than required, and that is the one
    concession: a hub in a state with no rule registered is genuinely unaffected
    by leaving it blank, and demanding it would imply we know what to do with
    it. But it is asked for at creation, which is the moment somebody knows the
    answer without looking it up.
    """

    name: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="America/Los_Angeles", max_length=64)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)

    _timezone_is_known = field_validator("timezone")(_known_time_zone)


class HubUpdateBody(BaseModel):
    """Change a hub. Every field optional; absent means "leave it".

    Distinguishing *absent* from *null* matters for `state_code`: absent leaves
    the current value, and an explicit null clears it. A single optional field
    that treated those the same would make it impossible to correct a hub
    somebody coded wrongly.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    state_code: str | None = Field(default=None, min_length=2, max_length=2)
    active: bool | None = None

    _timezone_is_known = field_validator("timezone")(_known_time_zone)


class HubView(BaseModel):
    id: str
    name: str
    timezone: str
    lat: float
    lng: float
    state_code: str | None
    active: bool
    # Said out loud, because "no state set" and "a state with no rule yet" look
    # identical from the outside and only one of them is somebody's oversight.
    overtime_rule: str
