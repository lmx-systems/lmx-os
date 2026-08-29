"""
Schemas for the driver-facing API (app/api/driver_routes.py) - screens
1a-1m of LMX Driver App Wireframes.dc.html. See docs/NEXT_STEPS.md item 12
for the gap analysis this closes.
"""
import enum
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class DriverProfileView(BaseModel):
    driver_id: str
    hub_id: str
    name: str
    phone: str
    status: str
    employment_type: str = "w2"
    # w2 | contractor_1099 | gig - drives which pay model/document set/
    # onboarding path applies (docs/NEXT_STEPS.md's phased rollout).
    vehicle_type: str | None = None
    plate_number: str | None = None
    delivery_zone: str | None = None
    payment_bank_last4: str | None = None
    # Real, computed from completed Route rows (app/api/driver_routes.py) -
    # not a stand-in. Star rating isn't shown anywhere in this app: there's
    # no rating-submission system (customers never rate a delivery), so
    # showing a number would be fabricated, not just an estimate.
    trip_count: int = 0

    @property
    def setup_complete(self) -> bool:
        return self.vehicle_type is not None


class DriverProfileUpdate(BaseModel):
    """Screens 1c ('Vehicle & profile setup') and 1r's 'Edit vehicle'."""

    vehicle_type: str  # car | van | bike
    plate_number: str
    delivery_zone: str


class PaymentMethodUpdate(BaseModel):
    """Screen 1r's payment method card. Last 4 digits only - see
    Driver.payment_bank_last4's docstring for why nothing more is collected."""

    bank_last4: str


class DriverDocumentView(BaseModel):
    """One compliance document as the driver sees it (docs/ROADMAP.md R4).

    Shows BOTH dates on purpose. `claimed_expires_at` is what the driver typed;
    `verified_expires_at` is what an LMX reviewer read off the document, and is the
    only one any decision acts on. Surfacing both is what makes a rejection
    legible - "you told us March, the card says January" is a fixable message,
    whereas one merged date would just look like we lost their upload.
    """

    doc_type: str  # license | insurance
    claimed_expires_at: date
    verified_expires_at: date | None = None
    review_status: str  # pending | verified | rejected
    rejection_reason: str | None = None
    file_url: str | None = None
    # Whether this document currently supports going on shift. Computed on the
    # server so the app can't arrive at a different answer than the gate does.
    is_usable: bool


class DriverDocumentUpdate(BaseModel):
    """What a driver may set on their own document.

    **`file_url` is deliberately absent.** It used to be here, and a driver could
    submit any string - `https://example.com/anything` was stored and treated as
    their license scan. It is now written by the backend from a key it minted for
    a presigned upload (POST /driver/me/documents/{doc_type}/upload-url), so the
    row can only ever point at something we actually hold.

    The expiry date the driver gives is recorded as a CLAIM. It does not open the
    gate; a reviewer reading the document does.
    """

    claimed_expires_at: date


class DriverDocumentUploadBody(BaseModel):
    """Requesting somewhere to put a photo of a license or insurance card."""

    content_type: str
    # The driver's own reading of the expiry, captured at the same moment as the
    # photo so the reviewer has something to compare the document against.
    claimed_expires_at: date


class DriverComplianceProblemView(BaseModel):
    doc_type: str
    # missing | awaiting_review | rejected | expired - a machine-readable reason so
    # the app can branch (upload something / wait / contact ops) instead of
    # parsing the sentence.
    reason: str
    detail: str


class DriverComplianceView(BaseModel):
    """Why the "go online" button is disabled, if it is."""

    can_go_on_shift: bool
    problems: list[DriverComplianceProblemView]


class DriverAvailabilityUpdate(BaseModel):
    """Screen 1d/1e's online/offline toggle."""

    status: Literal["available", "off_shift", "on_break", "en_route"]


class DriverLocationPingBody(BaseModel):
    """One position report from the driver's own device (docs/ROADMAP.md F1).

    `recorded_at` is supplied by the client rather than stamped server-side
    on purpose: the app reports its own observation time, so a ping that
    was queued through a dead zone still lands at the moment it actually
    happened instead of collapsing an entire offline stretch onto the
    reconnect instant.

    Bounds are enforced here rather than trusted: lat/lng outside real
    coordinate ranges is a malformed client, and a negative accuracy is
    meaningless - both are cheap to reject at the edge (same reasoning as
    the Literal/length bounds the S6 security pass added elsewhere).
    """

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    recorded_at: datetime
    accuracy_m: float | None = Field(default=None, ge=0)


class OfferStopSummary(BaseModel):
    order_id: str
    lat: float
    lng: float
    sla_tier: str
    shop_name: str = ""


class JobOfferView(BaseModel):
    offer_id: str
    hub_id: str
    expires_at: datetime
    stops: list[OfferStopSummary]
    # Real per-delivery pay estimate (docs/ROADMAP.md A11,
    # app/payroll/gig_pricing.py) - only populated for a gig-classified
    # driver; null for w2/1099, who are paid hourly/monthly instead
    # (app/payroll/hours.py), not per offer.
    estimated_pay_cents: int | None = None

    @property
    def stop_count(self) -> int:
        return len(self.stops)


class CodObligationView(BaseModel):
    """Money owed at this stop, shown before the driver knocks.

    Sent with the stop so the app can display the amount on the way there rather than
    discovering it at the door - a driver who learns there is money to collect while the
    customer is already reaching for the parts has lost the moment to ask for it.
    """

    order_id: str
    amount_due_cents: int
    # Once settled, the app stops asking. Both a collection and a dispute settle it: the
    # rule is "keep moving", so a driver is not held at a door by an unresolved dispute.
    settled: bool
    outcome: str | None = None


class CollectCodBody(BaseModel):
    """Confirming the FULL amount was taken.

    **There is deliberately no amount field**, and that absence is the enforcement of
    "never negotiate" (docs/ROADMAP.md W2). The money is the distributor's invoice to
    their own customer; nobody at LMX has authority to discount it, so a field to type a
    smaller number into would hand a driver an authority they were never given. Collect
    it all, or raise a dispute.
    """

    method: Literal["cash", "check"]


class CodDisputeBody(BaseModel):
    """The customer won't pay. One tap, then move on."""

    # What they said, in the driver's words. Free text because the useful signal is a
    # pattern across an account, and a dropdown written now would decide in advance which
    # patterns can be seen.
    note: str | None = Field(default=None, max_length=500)


class StopProofRequirementView(BaseModel):
    """What this stop must produce, so the app can ASK for the right thing.

    Sent with every stop rather than discovered on rejection: a driver who finds out
    at the door that this client wanted four photos has already put the box down and
    driven off. The strictest requirement across the stop's orders
    (app/delivery/proof.py).
    """

    photo_count_required: int
    photo_subjects: list[str]
    signature_required: bool


class StopView(BaseModel):
    stop_id: str
    sequence: int
    stop_type: str  # pickup | dropoff
    status: str
    lat: float
    lng: float
    shop_name: str | None = None
    address: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    notes: str | None = None
    parcel_count: int
    scanned_count: int
    order_ids: list[str]
    eta: datetime | None = None
    completed_at: datetime | None = None
    left_at: str | None = None
    failure_reason: str | None = None
    flag_note: str | None = None
    # What proof this stop needs. Always present, defaulting to the one-photo
    # baseline, so the app never has to guess.
    proof: StopProofRequirementView | None = None
    # Money to collect here, if any (W2). Empty for the overwhelming majority of stops.
    cod: list[CodObligationView] = Field(default_factory=list)


class RouteView(BaseModel):
    route_id: str
    status: str
    plan_version: int
    stops: list[StopView]


class ScanParcelsBody(BaseModel):
    scanned_count: int


class ScanParcelBody(BaseModel):
    # A real scanned barcode (docs/ROADMAP.md W10), verified against the
    # order at the pickup stop - unlike ScanParcelsBody's bare count, which
    # stays as the manual "can't scan? confirm manually" fallback.
    barcode: str = Field(min_length=1, max_length=128)


class ParcelView(BaseModel):
    barcode: str
    scanned: bool


class UploadUrlRequestBody(BaseModel):
    kind: Literal["photo", "signature", "scan"]
    # An explicit, closed allowlist - not an arbitrary string - since a
    # presigned PUT's ContentType is otherwise a driver-controlled value
    # written straight into an S3 request (docs/ROADMAP.md A2/A3,
    # app/storage/photo_upload_client.py).
    content_type: Literal["image/jpeg", "image/png", "image/webp"]


class UploadUrlResult(BaseModel):
    upload_url: str
    final_url: str
    requires_upload: bool


class CompleteStopBody(BaseModel):
    """Proof of delivery, screen 1m.

    `photo_urls` exists because an order can require more than one photo, with named
    subjects (`ProofRequirements`, docs/LMX_LINK_PLAN.md §1.2) - a single `photo_url`
    cannot express "the shelf, the box, the paperwork". `photo_url` is kept and folded
    in as the first photo, so an older app build keeps working through the change.
    """

    method: Literal["photo", "signature", "pin"]
    photo_url: str | None = None
    photo_urls: list[str] = Field(default_factory=list, max_length=8)
    signature_url: str | None = None
    pin: str | None = None
    left_at: str | None = None

    @property
    def all_photo_urls(self) -> list[str]:
        """Every photo supplied, however it was sent, de-duplicated in order.

        An app that sends the same URL in both fields must not have it counted twice
        toward a photo requirement - that would let one photo satisfy "two photos".
        """
        combined: list[str] = []
        for url in ([self.photo_url] if self.photo_url else []) + list(self.photo_urls):
            if url and url not in combined:
                combined.append(url)
        return combined


class StopFailureReason(str, enum.Enum):
    """"Flag an issue" reason codes - see the wireframe screen of the same
    name. Plain str column on Stop (app/models/stop.py), not a Postgres
    enum type - matches stop_type/pod_method's existing convention."""

    SHOP_CLOSED = "SHOP_CLOSED"
    ACCESS_ISSUE = "ACCESS_ISSUE"
    COD_DISPUTE = "COD_DISPUTE"
    PARTS_MISSING = "PARTS_MISSING"
    REFUSED = "REFUSED"


class FlagStopBody(BaseModel):
    reason: StopFailureReason
    note: str | None = None


# Why a driver turned an offer down (docs/ROADMAP.md I1, I4). A defined vocabulary
# rather than free text, because the question this answers - "is there a pattern" - needs
# values that group, and 200 drivers typing "too far away" five different ways answers
# nothing.
#
# **The column stays a free string** (`RouteOffer.decline_reason`, 64 chars) and the API
# still accepts anything: the decline endpoint predates this list, and rejecting an
# unrecognised value would break a caller to gain nothing - an unexpected reason is still
# a reason, and the report groups whatever arrives.
#
# Kept deliberately short. A driver has about two minutes to respond to an offer and a
# long list is friction on the one interaction that must not be slow. `pay_too_low` is
# included even though it only means something on the gig track, because a reason nobody
# offers is a reason nobody reports - and the whole point is to find out.
DECLINE_TOO_FAR = "too_far"
DECLINE_PAY_TOO_LOW = "pay_too_low"
DECLINE_VEHICLE_UNSUITABLE = "vehicle_unsuitable"
DECLINE_ENDING_SHIFT = "ending_shift"
DECLINE_OTHER = "other"

DECLINE_REASONS = (
    DECLINE_TOO_FAR,
    DECLINE_PAY_TOO_LOW,
    DECLINE_VEHICLE_UNSUITABLE,
    DECLINE_ENDING_SHIFT,
    DECLINE_OTHER,
)


class DeclineOfferBody(BaseModel):
    # Optional ground-truth capture (docs/ROADMAP.md I1) - why the driver
    # declined, for the eventual offer-acceptance model. Optional so the
    # existing "decline with no body" call keeps working.
    reason: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Messaging (screens 1p/1q) and earnings (screens 1n/1o) - Phase 3.
# ---------------------------------------------------------------------------


class SendMessageBody(BaseModel):
    # ~1 SMS segment set's worth - this goes straight to
    # app.messaging.sms_client.SmsClient.send() (billed per segment) and
    # into a Text column with no cap otherwise.
    body: str = Field(max_length=1600)


class MessageView(BaseModel):
    """Deliberately has no phone number field anywhere - the whole point of
    'masked' is that the customer's/support's real number never reaches the
    driver app. See Message.counterparty_phone's docstring."""

    message_id: str
    channel: str  # customer | support
    direction: str  # outbound | inbound
    body: str
    created_at: datetime
    stop_id: str | None = None


class CallView(BaseModel):
    """Deliberately has no phone number field anywhere - same masking rule
    as MessageView. The driver's own phone rings via a real carrier call
    (app/messaging/voice_client.py); this view is just the resulting log
    entry, not anything used to actually place the call client-side."""

    call_id: str
    status: str  # initiated | connected | completed | failed | no-answer
    created_at: datetime
    duration_seconds: int | None = None


class EarningsView(BaseModel):
    """Screen 1n. Hours now come from the real online/offline/break log
    (app/models/driver_shift_event.py) rather than route-span, and the
    rate is the driver's real hourly_rate_cents when one is set
    (is_placeholder=False) - see docs/NEXT_STEPS.md. Still not a verified
    payroll figure: overtime_hours applies only the federal 40hr/week 1.5x
    rule (no state-specific daily-OT rules), and a workweek split across
    two pay periods only sees the hours visible in THIS period (see
    app/payroll/hours.py's hours_and_overtime docstring)."""

    period_start: date
    period_end: date
    hours_worked: float
    overtime_hours: float = 0.0
    hourly_rate_cents: int
    estimated_pay_cents: int
    is_placeholder: bool = True
    # Lets the driver app render gig's real per-delivery pay differently
    # from w2/1099's hourly rate (docs/ROADMAP.md A11) without a second
    # round trip - previously missing entirely (see EarningsScreen.tsx's
    # own comment about inferring the pay period width instead).
    employment_type: str = "w2"
    note: str = (
        "Estimate only - pay formula and payroll integration are not finalized. "
        "Contact dispatch with pay questions."
    )


class TripSummaryView(BaseModel):
    """Screen 1o, trip history. hours is the same route-span estimate
    EarningsView aggregates - see that class's docstring."""

    route_id: str
    completed_at: datetime
    stop_count: int
    hours: float


class ScorecardMetricView(BaseModel):
    """One figure on the driver's scorecard, with the team's beside it.

    `fleet_median` is None when the comparison is withheld - see the scorecard's
    `comparison_withheld`. The driver's own number is never withheld; only the
    comparison can be.
    """

    name: str
    unit: str
    own_median: float | None
    own_p90: float | None
    own_sample_size: int
    fleet_median: float | None
    not_measured: str | None


class DriverScorecardView(BaseModel):
    """What a driver sees about their own work (docs/ROADMAP.md W4).

    Two metrics, each beside the same figure for their hub: how many deliveries an hour
    they complete, and how close their arrivals land to the predicted time. Computed by
    the same functions that produce the fleet-wide report, narrowed to this driver -
    which is what makes it a shared standard rather than a separate, reduced view.
    """

    window_days: int
    generated_at: datetime
    metrics: list[ScorecardMetricView]
    # Set when there are too few colleagues for a team median to be non-identifying.
    comparison_withheld: str | None = None
