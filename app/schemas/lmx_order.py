"""
The LMX Order Object v1 (docs/LMX_LINK_PLAN.md §1.2).

The one canonical shape an order takes inside LMX OS, whatever sent it. Source
adapters normalize into this; status sinks read out of it; the SLA engine,
batch-hold queue, optimizer and driver app never learn where an order came from.

**The rule that makes this worth having** (§1.1): if an adapter ever needs a
change inside the SLA engine, hold queue or optimizer, *the contract is wrong* -
fix the contract, not the core.

Designed against all three demand paths at once, per the kickoff decision, even
though only the client-portal path is implemented today. The two fields that
carry that generality are `sla_owner` (see below) and `assignment_scope`.

RELATIONSHIP TO `NormalizedOrder`. `app/schemas/order.py`'s `NormalizedOrder`
stays exactly as it is: the narrow thing a POS/DMS adapter emits. This object is
wider - it carries destination, both time windows, commitment ownership, proof
requirements and economics, none of which an Epicor payload knows about.
`app/ingestion/service.py` maps one into the other, so existing adapters need no
change.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Who promised the customer a delivery time. The single most important field in
# the design (§1.3), because conflating the two models is the failure mode that
# would force a fork later.
#
#   LMX      - we promised. The SLA engine classifies urgency from the customer
#              profile, part type and deadline, sets the tier, and owns the
#              clock. Web portal, CSV, REST webhook, Epicor/MAM.
#   EXTERNAL - somebody else promised. The SLA engine does NOT classify; it
#              accepts the given window as a hard constraint and enforces it.
#              Aggregator relay, enterprise API/EDI where the retailer sets the
#              window.
#
# The batch-hold queue is identical in both cases - it holds against whatever
# deadline is on the object. That is precisely why an aggregator path needs no
# new optimizer logic.
SLAOwner = Literal["LMX", "EXTERNAL"]

# Whether this order can move between drivers. A per-job property, not a system
# mode, because both onboarding tracks run simultaneously during any migration
# and one optimizer run has to handle both (August offsite; getting this wrong
# "means a rewrite"). Mirrors app/models/gig_job.py's ASSIGNMENT_SCOPES.
AssignmentScope = Literal["pinned_to_driver", "any_driver"]

# How the order is priced, so per-drop and per-mile revenue can coexist in one
# margin report rather than needing separate reporting paths.
RevenueBasis = Literal["per_drop", "per_mile", "contract"]

# Who settles, and when. Carried in v1 even though collection is not built:
# retrofitting payment onto an order contract is a migration, and the whole
# point of a contract is to not need one. See the plan's gap 9.
# `cash_on_delivery` means the driver collects the DISTRIBUTOR'S invoice amount at the
# door (W2). It was absent until now, which meant `COD_DISPUTE` was a stop failure reason
# for a payment mode no order could declare.
PayerType = Literal["contract_client", "prepaid", "card_on_file", "cash_on_delivery"]
PaymentStatus = Literal["not_billable", "unbilled", "invoiced", "paid"]

# Size buckets a counter person can pick without a tape measure. Feeds vehicle
# capacity now and autonomy eligibility later.
SizeClass = Literal["envelope", "small", "medium", "large", "oversize"]


class LineItem(BaseModel):
    """One part on the order. Optional in aggregate - a counter person under
    time pressure should never be blocked on itemizing (§2.2 principle 7)."""

    description: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1)
    size_class: SizeClass = "small"
    weight_units: float | None = Field(default=None, ge=0)


class ProofRequirements(BaseModel):
    """What proof of delivery means for this order's source.

    Configurable rather than hardcoded because it genuinely differs: an
    aggregator can mandate four photos with specified subjects while a
    distributor wants one. The driver app reads this off the order
    (docs/LMX_LINK_PLAN.md §1.2, "Proof"); it must never be a constant in the
    app. Defaults match what the app does today, so an order that says nothing
    behaves exactly as before.
    """

    photo_count_required: int = Field(default=1, ge=0, le=8)
    photo_subjects: list[str] = Field(default_factory=list)
    signature_required: bool = False


class Economics(BaseModel):
    """Money on the order. `quoted_amount_cents` is what the customer was told,
    which is not necessarily what gets invoiced and definitely not margin -
    `cost_actuals_cents` is filled in after the fact."""

    revenue_basis: RevenueBasis = "per_drop"
    quoted_amount_cents: int | None = Field(default=None, ge=0)
    cost_actuals_cents: int | None = Field(default=None, ge=0)
    payer_type: PayerType = "contract_client"
    payment_status: PaymentStatus = "unbilled"
    payment_terms_days: int | None = Field(default=None, ge=0)
    # What the driver collects at the door when payer_type is cash_on_delivery (W2).
    # **Money that isn't ours** - the distributor's invoice to their own customer, which
    # is why it is not `quoted_amount_cents` and not `fee_cents`. A dispute over it is
    # between those two parties, and the driver has no authority to change it.
    cod_amount_cents: int | None = Field(default=None, ge=0)


class LMXOrder(BaseModel):
    """One order, in the only shape the core understands."""

    # --- Identity -------------------------------------------------------
    # source_system is the ONLY place an order's origin is ever recorded.
    # Anything downstream branching on it is a bug (§1.1).
    source_system: str = Field(min_length=1, max_length=32)
    source_order_ref: str = Field(min_length=1, max_length=120)
    hub_id: str
    # Null for a path with no client relationship at all (an aggregator job).
    # The portal path always has one.
    client_id: str | None = None
    received_at: datetime | None = None

    # --- Origin ---------------------------------------------------------
    # Either a registered shop OR a typed address. The whole point of ad-hoc
    # pickup is that a new client can send their first order without anyone
    # pre-registering a location (§1.2, "Origin"). Resolution happens in
    # app/ingestion/service.py, which geocodes and remembers the address as a
    # Shop so repeat orders to the same place are free (§2.2 principle 3).
    shop_external_ref: str | None = Field(default=None, max_length=120)
    pickup_address: str | None = Field(default=None, max_length=255)
    pickup_lat: float | None = Field(default=None, ge=-90, le=90)
    pickup_lng: float | None = Field(default=None, ge=-180, le=180)
    pickup_contact_name: str | None = Field(default=None, max_length=120)
    pickup_contact_phone: str | None = Field(default=None, max_length=32)
    ready_at: datetime | None = None
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None

    # --- Destination ----------------------------------------------------
    # Optional, which §1.2 does not say but the codebase requires: no POS/DMS
    # adapter populates a destination today (see Order.delivery_address's
    # docstring - "no source-system adapter has been updated to populate these
    # yet"), so requiring it here would break every existing ingestion path.
    # It is also the right call independently: §2.2 principle 7 says never block
    # intake on a missing field. An order without a destination is accepted and
    # stored, it just isn't dispatchable - which `is_dispatchable` reports.
    drop_address_raw: str | None = Field(default=None, max_length=255)
    drop_lat: float | None = Field(default=None, ge=-90, le=90)
    drop_lng: float | None = Field(default=None, ge=-180, le=180)
    drop_contact_name: str | None = Field(default=None, max_length=120)
    drop_contact_phone: str | None = Field(default=None, max_length=32)
    access_notes: str | None = Field(default=None, max_length=500)
    delivery_window_start: datetime | None = None
    delivery_window_end: datetime | None = None

    # --- Commitment -----------------------------------------------------
    sla_owner: SLAOwner = "LMX"
    # Only meaningful when sla_owner is LMX - when EXTERNAL, whoever made the
    # promise already decided, and we don't get to reclassify it.
    sla_tier: str | None = None
    # What the customer was actually told, as opposed to what we computed.
    # Worth keeping separately: if the two ever diverge, the customer's version
    # is the one that matters to them.
    promised_at: datetime | None = None

    # How long the person entering this order took, from their own browser
    # (§3.4, migration 0036). Only set by paths where a human types the order -
    # null for Epicor and for the public API, where there is nobody to time.
    #
    # A product metric, not an audit figure: it is client-reported and nothing
    # verifies it.
    entry_seconds: int | None = Field(default=None, ge=0, le=3600)

    # --- Payload --------------------------------------------------------
    line_items: list[LineItem] = Field(default_factory=list)
    total_weight_units: float = Field(default=1.0, ge=0)
    required_vehicle_class: str | None = Field(default=None, max_length=32)

    # --- Modality -------------------------------------------------------
    # Carried now, used later. Every delivery gets scored for autonomy
    # eligibility from the first van, so the first partner conversation starts
    # with a measured addressable share of real order flow rather than a
    # projection.
    modality_eligible: list[str] = Field(default_factory=list)
    modality_assigned: str | None = Field(default=None, max_length=32)

    # --- Proof, status, economics ---------------------------------------
    proof: ProofRequirements = Field(default_factory=ProofRequirements)
    assignment_scope: AssignmentScope = "any_driver"
    economics: Economics = Field(default_factory=Economics)

    raw_payload: dict = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _origin_must_be_resolvable(self) -> "LMXOrder":
        """An order has to say where to collect from, one way or the other.

        Deliberately permissive about *how*: a registered shop ref, or an
        address we can geocode, or coordinates supplied directly. What it will
        not accept is an order with no origin at all, because that is
        undispatchable and failing here is far cheaper than discovering it when
        a driver has nowhere to go.
        """
        has_shop = self.shop_external_ref is not None
        has_address = self.pickup_address is not None
        has_coords = self.pickup_lat is not None and self.pickup_lng is not None
        if not (has_shop or has_address or has_coords):
            raise ValueError(
                "origin required: one of shop_external_ref, pickup_address, "
                "or pickup_lat/pickup_lng"
            )
        return self

    @model_validator(mode="after")
    def _external_commitment_needs_a_window(self) -> "LMXOrder":
        """`EXTERNAL` means somebody else set the deadline - so there has to be
        one. Without it there is nothing for the hold queue to hold against and
        nothing to enforce, and since we never classify an EXTERNAL order there
        is no fallback to compute either (§1.3)."""
        if self.sla_owner == "EXTERNAL" and self.delivery_window_end is None:
            raise ValueError(
                "sla_owner=EXTERNAL requires delivery_window_end - the external "
                "promise is the only deadline this order will ever have"
            )
        return self

    @model_validator(mode="after")
    def _windows_must_be_ordered(self) -> "LMXOrder":
        """A window closing before it opens is a capture bug - most often a date
        rolling over midnight during extraction or a form. Rejecting it here
        keeps it out of the optimizer, where an inverted window makes an order
        look permanently infeasible instead of malformed."""
        for start, end, label in (
            (self.pickup_window_start, self.pickup_window_end, "pickup"),
            (self.delivery_window_start, self.delivery_window_end, "delivery"),
        ):
            if start is not None and end is not None and end < start:
                raise ValueError(f"{label}_window_end is before {label}_window_start")
        return self

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------

    @property
    def needs_classification(self) -> bool:
        """Whether the SLA engine should assign a tier and a hold deadline.

        The single branch every ingestion path takes. `EXTERNAL` orders skip
        classification entirely and carry their given window straight through.
        """
        return self.sla_owner == "LMX"

    @property
    def is_dispatchable(self) -> bool:
        """Whether this order can become a route leg yet.

        Both ends need coordinates. An order can be accepted and stored without
        them - never blocking intake on a missing field is §2.2 principle 7 -
        but it cannot be planned until geocoding has resolved them.
        """
        return (
            self.pickup_lat is not None
            and self.pickup_lng is not None
            and self.drop_lat is not None
            and self.drop_lng is not None
        )

    @property
    def total_weight_from_items(self) -> float:
        """Weight implied by the line items, when they carry it.

        Kept separate from `total_weight_units` rather than overwriting it: a
        counter person's stated total is a claim about the real shipment, and
        itemized weights are frequently absent or wrong. The optimizer uses the
        stated total; this is here to spot the disagreement.
        """
        return sum(
            (item.weight_units or 0) * item.quantity for item in self.line_items
        )
