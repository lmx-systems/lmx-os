"""
Schemas for the driver-facing API (app/api/driver_routes.py) - screens
1a-1m of LMX Driver App Wireframes.dc.html. See docs/NEXT_STEPS.md item 12
for the gap analysis this closes.
"""
import enum
from datetime import date, datetime

from pydantic import BaseModel


class DriverProfileView(BaseModel):
    driver_id: str
    hub_id: str
    name: str
    phone: str
    status: str
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
    doc_type: str  # license | insurance
    expires_at: date
    file_url: str | None = None

    @property
    def is_expired(self) -> bool:
        return self.expires_at < date.today()


class DriverDocumentUpdate(BaseModel):
    expires_at: date
    file_url: str | None = None


class DriverAvailabilityUpdate(BaseModel):
    """Screen 1d/1e's online/offline toggle."""

    status: str  # available | off_shift | on_break | en_route


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

    @property
    def stop_count(self) -> int:
        return len(self.stops)


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


class RouteView(BaseModel):
    route_id: str
    status: str
    plan_version: int
    stops: list[StopView]


class ScanParcelsBody(BaseModel):
    scanned_count: int


class CompleteStopBody(BaseModel):
    """Proof of delivery, screen 1m."""

    method: str  # photo | signature | pin
    photo_url: str | None = None
    signature_url: str | None = None
    pin: str | None = None
    left_at: str | None = None


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


# ---------------------------------------------------------------------------
# Messaging (screens 1p/1q) and earnings (screens 1n/1o) - Phase 3.
# ---------------------------------------------------------------------------


class SendMessageBody(BaseModel):
    body: str


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


class EarningsView(BaseModel):
    """Screen 1n. is_placeholder is always True right now - see this
    module's own note below and docs/NEXT_STEPS.md item 14. There is no
    real fare/price field anywhere in Order/Route/Stop, so this is an
    estimate built from a placeholder hourly rate and each route's
    creation-to-completion span, not a verified payroll figure."""

    period_start: date
    period_end: date
    hours_worked: float
    hourly_rate_cents: int
    estimated_pay_cents: int
    is_placeholder: bool = True
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
