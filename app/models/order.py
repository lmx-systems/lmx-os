"""
An order ingested from a client's POS/DMS. This is the row the Dynamic SLA
Engine classifies (T1/T2/T3) and the Batch-Hold Queue clusters.
"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SLATier(str, enum.Enum):
    HOT_SHOT = "HOT_SHOT"  # direct point-to-point, never commingled with another order's pickup
    T1 = "T1"  # urgent / short hold window
    T2 = "T2"  # standard
    T3 = "T3"  # flexible / long hold window


class OrderStatus(str, enum.Enum):
    received = "received"
    classified = "classified"
    held = "held"          # sitting in the batch-hold queue
    queued = "queued"       # released from hold, waiting for a route assignment
    assigned = "assigned"   # attached to a stop on a route
    # Stop-level progress promoted onto the order (LMX_LINK_PLAN.md §1.4, the
    # one status machine every sink shares - see app/orders/state_machine.py).
    # Before these existed an order sat at `assigned` from dispatch until
    # delivery, with progress visible only on its Stop rows - so a client
    # watching their order saw "assigned" for an hour with no way to tell
    # whether the driver had actually collected it yet.
    accepted = "accepted"                  # a driver took the offer covering it
    en_route_pickup = "en_route_pickup"
    picked_up = "picked_up"
    en_route_drop = "en_route_drop"
    delivered = "delivered"
    cancelled = "cancelled"
    # A driver flagged the stop covering this order (shop closed, access
    # blocked, a dispute, etc. - app/api/driver_routes.py's flag_stop_issue).
    # Distinct from cancelled: this order was actually attempted, not
    # cancelled pre-dispatch - ops needs to decide on redelivery/refund,
    # not just close it out (app/delivery/resolution.py, docs/ROADMAP.md R5).
    delivery_failed = "delivery_failed"
    # A failed order resolved by sending the parts back to the originating
    # shop rather than reattempting or cancelling (R5). Terminal, and
    # distinct from cancelled (which never physically moved) - the shop is
    # notified to expect the return.
    returned = "returned"


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "orders"

    hub_id: Mapped[UUID] = mapped_column(ForeignKey("hubs.id"), nullable=False)
    # Both nullable as of the LMX Link contract (migration 0028). A path with no
    # client relationship at all has no client_id, and an order captured before
    # its pickup address has been resolved to a Shop has no shop_id yet.
    #
    # WARNING for anything that reads these: a null client_id means "not a
    # client's order", NOT "any client". Every client-scoped query must filter
    # explicitly - see app/api/client_routes.py and app/billing/service.py,
    # which both already compare against a specific client_id and are therefore
    # safe. A query that omits the filter would leak across the boundary.
    client_id: Mapped[UUID | None] = mapped_column(ForeignKey("clients.id"), nullable=True)
    shop_id: Mapped[UUID | None] = mapped_column(ForeignKey("shop_profiles.id"), nullable=True)

    external_order_ref: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)  # epicor | mam | asa | flat_file

    # Raw payload as received, kept verbatim for debugging/replay.
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    sla_tier: Mapped[SLATier | None] = mapped_column(Enum(SLATier, name="sla_tier"), nullable=True)
    # Explicit timezone=True is required here - without it, SQLAlchemy
    # infers a naive DateTime from the bare `datetime` annotation, which
    # doesn't match the timezone-aware column the migration actually
    # creates (see migrations/versions/0001_initial_schema.py) and every
    # tz-aware datetime this app ever produces (e.g. datetime.now(timezone.utc)
    # in app/sla/engine.py) fails to insert against a real Postgres.
    # Caught by tests/integration/test_ingestion_integration.py - fakeredis/
    # pure-function unit tests can't catch this since they never touch a
    # real database's type-checking.
    hold_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Written by DispatchOptimizerService.run_cycle the moment this order is
    # actually assigned to a driver - see app/optimizer/service.py. Lets the
    # dashboard's Order Status Summary widget (and anything else querying
    # Order.status) reflect a dispatch that already happened instead of
    # showing "held" forever once the Redis hold queue has moved on.
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    weight_units: Mapped[float] = mapped_column(Numeric(10, 2), default=1, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.received, nullable=False
    )

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Real delivery timestamp (docs/ROADMAP.md I1 - ground-truth capture),
    # set once when the dropoff completes (app/api/driver_routes.py's
    # complete_stop). Replaces the `updated_at`-as-delivered-at proxy that
    # billing and the client portal used to rely on - `updated_at` gets
    # bumped by *any* later mutation (e.g. attaching an invoice_id), which
    # is exactly the corruption that proxy needed careful guarding against.
    # This column is stable: written once at delivery, never on update.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # How many delivery attempts this order has had (R5). 1 = the original
    # dispatch; incremented each time a failed order is redelivered
    # (app/delivery/resolution.py). Lets ops and the client see "2nd
    # attempt" rather than a silent re-queue, and gives a natural cap point
    # if a redelivery-attempt limit is ever wanted.
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Why the current/most-recent delivery attempt failed, denormalized off
    # the flagged Stop.failure_reason (R5) so client/ops order views can
    # show a reason without joining through StopOrder to a specific stop -
    # ambiguous once an order has several stops across redelivery attempts.
    # Set when the covering stop is flagged (driver_routes.flag_stop_issue),
    # cleared when the order is redelivered (resolution._redeliver).
    failure_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Delivery (customer/drop-off) side of the order - added for the driver
    # app's active-job flow (screens 1i/1l/1m). Everything ingested before
    # this existed only ever modeled the pickup side (shop_lat/lng via
    # HeldOrder/StopCandidate) - no source-system adapter has been updated
    # to populate these yet, so they're nullable rather than backfilled.
    # A missing delivery_lat/lng means a Stop can't be generated for this
    # order when a job offer is accepted (see app/api/driver_routes.py).
    delivery_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_lat: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    delivery_lng: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    delivery_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    delivery_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The capability that lets the delivery RECIPIENT - not the shop, not the
    # client - watch this delivery on a public page (docs/ROADMAP.md F3,
    # app/tracking/service.py, migration 0031).
    #
    # Nullable and minted lazily rather than on insert: every order predating
    # this feature has none, and orders are created from several paths
    # (both ingestion entry points, returns, failed-delivery resolution). One
    # lazy helper covers new and legacy rows identically, and an order nobody
    # ever tracks never gets a credential it doesn't need.
    #
    # Unique because the endpoint looks orders up BY this value, and indexed
    # because that lookup is on the hot path of a page that polls. Stored in the
    # clear: unlike a password-reset token this has to be resolvable from a URL
    # on every poll, and the row it sits beside already holds the delivery
    # address and the recipient's phone number - both more sensitive than a
    # position feed that stops working a day after delivery.
    tracking_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )

    # ------------------------------------------------------------------
    # LMX Link contract fields (docs/LMX_LINK_PLAN.md §1.2, migration 0028).
    # See app/schemas/lmx_order.py for the field-by-field reasoning; this is
    # the persisted half of that object.
    # ------------------------------------------------------------------

    # Who promised the customer a delivery time - the single most important
    # field in the design (§1.3). `LMX` means the SLA engine classifies and
    # owns the clock; `EXTERNAL` means somebody else promised and we enforce
    # their window without reclassifying it. Every existing row is LMX.
    sla_owner: Mapped[str] = mapped_column(String(16), nullable=False, server_default="LMX")

    # The source's own identifier for this order. Distinct from
    # external_order_ref only in intent - kept as its own column so the
    # (source_system, source_order_ref) uniqueness that gives every adapter
    # idempotent intake can be added without overloading a column that
    # predates the contract.
    source_order_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Ad-hoc pickup: a typed address for a place we have no Shop row for yet.
    # app/ingestion/service.py geocodes it, dedupes on the normalized form, and
    # creates the Shop - so the second order to the same address reuses it
    # (§2.2 principle 3, "remember every shop"). These stay populated after
    # that as the raw thing the customer actually typed.
    pickup_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pickup_contact_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pickup_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Hard windows, set by whoever owns the commitment. Distinct from
    # hold_deadline, which is ours and internal: these are what was promised.
    pickup_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pickup_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What the customer was actually told, as opposed to what we computed. Kept
    # separately on purpose: if the two diverge, theirs is the one that matters.
    promised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Whether this order can move between drivers. A per-job property rather
    # than a system mode, because both onboarding tracks run simultaneously
    # during any migration and one optimizer run handles both. Mirrors
    # app/models/gig_job.py's assignment_scope, which this will absorb.
    assignment_scope: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="any_driver"
    )

    # Proof-of-delivery requirements for this order's source (§1.2, "Proof").
    # JSONB rather than columns because it is a small config blob the driver app
    # reads whole, and its shape will grow with new sources. Empty dict means
    # "app defaults", so every pre-contract order behaves exactly as before.
    proof_requirements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Economics. `fee_cents` below predates the contract and remains what LMX
    # charges per drop; these carry the wider picture so per-drop and per-mile
    # revenue can coexist in one margin report.
    revenue_basis: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="per_drop"
    )
    quoted_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_actuals_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Carried in v1 although collection is not built: retrofitting payment onto
    # an order contract is a migration, and the point of a contract is to not
    # need one.
    payer_type: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="contract_client"
    )
    payment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="unbilled"
    )

    # Autonomy eligibility, captured from the first van so the first partner
    # conversation starts with a measured addressable share of real order flow
    # rather than a projection. Carried now, used later.
    modality_eligible: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    modality_assigned: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # What LMX charges the client for this drop (Phase 8) - set once at
    # classification time (app/ingestion/service.py) from the client's
    # ClientRate for this order's tier. Null, not zero, when the client
    # has no configured rate for this tier yet - a missing price should
    # never silently look like a free delivery on the client portal or in
    # payroll math (docs/NEXT_STEPS.md item 14's driver-earnings gap).
    fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Set once by app/billing/service.py's generate_invoice() when this
    # (delivered, fee_cents-priced) order is swept into a client's
    # statement for a period - null means "not yet billed," which is what
    # keeps a later invoice run from double-billing an order that was
    # already included in an earlier one (docs/ROADMAP.md C3).
    invoice_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("invoices.id"), nullable=True, index=True
    )
