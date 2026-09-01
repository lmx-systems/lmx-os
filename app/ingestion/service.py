"""
Order Ingestion Layer service (component 1).

Orchestrates: adapter.normalize() -> resolve shop -> persist Order row ->
Dynamic SLA Engine classification -> push into the Batch-Hold Queue.
This is the only place that wires those pieces together, so ingestion
behavior can be reasoned about from one file.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import metrics
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.geocoding import BaseGeocoder, get_geocoder, normalize_address, resolve_address
from app.ingestion.registry import get_adapter
from app.billing.rates import distance_between, price_drop
from app.models.client_rate import ClientRate
from app.models.order import Order, OrderStatus
from app.models.parcel import Parcel
from app.models.return_item import ReturnItem
from app.models.rules import ActiveRule
from app.models.shop import Shop
from app.schemas.lmx_order import LMXOrder
from app.schemas.order import NormalizedOrder
from app.sla.engine import HoldWindowOverride, TierOverride, classify_order

logger = structlog.get_logger(__name__)

# Marks a shop this system created from a typed address rather than one anybody
# registered. Visible on purpose: ops looking at shop_profiles should be able to
# tell at a glance which rows came from LMX Link's ad-hoc pickup.
_AUTO_SHOP_REF_PREFIX = "lmxlink:"

# Dispatch priority for an order whose deadline someone else set. §1.3 forbids
# classifying an EXTERNAL order - the external window IS the commitment - so this
# is not a computed urgency, only the tier the solver uses for skip penalties
# when the caller didn't state one.
#
# T2 rather than T1 deliberately: an externally-committed order should not get
# unearned premium priority over LMX's own urgent work simply because it arrived
# through a different door. The real per-tier weighting is unconfirmable before
# real hub data exists (same status as docs/ROADMAP.md E2/E10, which have no spec
# to check against either).
_DEFAULT_EXTERNAL_TIER = "T2"


class ShopNotFoundError(Exception):
    pass


class OriginUnresolvableError(Exception):
    """A typed pickup address that could not be turned into coordinates.

    Deliberately fatal to ingestion rather than being swallowed. Without
    coordinates the batch-hold queue cannot cluster the order, the optimizer
    cannot route it, and `app/api/driver_routes.py` renders its pickup stop from
    a 0.0/0.0 fallback - putting a driver's stop in the Gulf of Guinea. A
    refused order that the client can correct beats a silently undeliverable one.
    """


class DestinationUnresolvableError(Exception):
    """A typed delivery address that could not be turned into coordinates.

    Also fatal, for the symmetric reason: without a drop coordinate no dropoff
    Stop can be generated (see `Order.delivery_lat`'s docstring), so the order
    would be collected and then have nowhere to go.

    This is NOT a violation of §2.2 principle 7 ("never block on a missing
    field"). That principle is about optional detail - a contact name, an access
    note - which should never stop an order being taken. An address nobody can
    find is not missing detail, it is an undeliverable order, and the person who
    can fix it is standing at the form right now. Once they have walked away, it
    becomes a phone call.
    """


def _parcel_barcodes(payload: dict) -> list[str]:
    """Ownership-agnostic package barcodes for an order (docs/ROADMAP.md W10).
    If the source payload carries the distributor's own pick-ticket barcodes
    (`parcels`: a list), use those (the scan-existing path); otherwise mint
    `parcel_count` (or 1) LMX codes (the LMX-label path). Either way the
    scan-at-pickup verification is identical - this only decides where the
    value comes from, which is the reversible half of W10's open decision."""
    raw = payload.get("parcels")
    if isinstance(raw, list) and raw:
        seen: set[str] = set()
        provided: list[str] = []
        for item in raw:
            bc = str(item).strip()
            if bc and bc not in seen:
                seen.add(bc)
                provided.append(bc)
        if provided:
            return provided

    count = payload.get("parcel_count")
    try:
        n = max(1, int(count)) if count is not None else 1
    except (TypeError, ValueError):
        n = 1
    return [f"LMX-{secrets.token_hex(5)}" for _ in range(n)]


def _expected_return_manifest(payload: dict) -> str | None:
    """The manifest for a core/return expected back with this order
    (docs/ROADMAP.md W1), or None if the payload flags no return. A
    `return_manifest` string wins; a truthy `core_return` falls back to a
    generic label."""
    manifest = payload.get("return_manifest")
    if isinstance(manifest, str) and manifest.strip():
        return manifest.strip()
    if payload.get("core_return"):
        return "core exchange"
    return None


async def _resolve_shop(session: AsyncSession, client_id: str, shop_external_ref: str) -> Shop:
    result = await session.execute(
        select(Shop).where(
            Shop.client_id == uuid.UUID(client_id),
            Shop.external_ref == shop_external_ref,
        )
    )
    shop = result.scalar_one_or_none()
    if shop is None:
        raise ShopNotFoundError(
            f"No shop_profiles row for client_id={client_id} external_ref={shop_external_ref!r}"
        )
    return shop


def _auto_shop_external_ref(address: str) -> str:
    """A deterministic ref for an auto-created shop, derived from the address.

    Using the normalized address as the dedupe key means the *existing*
    (client_id, external_ref) lookup does the deduplication for free - the second
    order to the same place finds the shop the first one created, which is
    exactly §2.2 principle 3's "remember every shop. Second order to the same
    shop is two taps."

    Hashed rather than storing the address in the ref because external_ref is 120
    characters and a real address plus the prefix can exceed that.
    """
    digest = hashlib.sha1(normalize_address(address).encode()).hexdigest()[:16]
    return f"{_AUTO_SHOP_REF_PREFIX}{digest}"


async def _find_shop_by_ref(session: AsyncSession, client_id: str, ref: str) -> Shop | None:
    """Look up an auto-created shop, tolerating duplicates.

    Uses limit(1) rather than scalar_one_or_none() because there is no unique
    constraint on (client_id, external_ref) - only an index - so two orders to a
    brand-new address arriving concurrently could both create a row. That race
    is cosmetic rather than harmful: both shops carry the same coordinates, so
    clustering (which is coordinate-based, not shop-id-based) still behaves. But
    it must not crash ingestion, which scalar_one_or_none() would.
    """
    result = await session.execute(
        select(Shop)
        .where(Shop.client_id == uuid.UUID(client_id), Shop.external_ref == ref)
        .order_by(Shop.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _resolve_or_create_shop(
    session: AsyncSession, lmx: LMXOrder, *, geocoder: BaseGeocoder
) -> Shop:
    """Turn an order's origin into a Shop row, whichever form it arrived in.

    Three accepted forms, in precedence order:

      1. `shop_external_ref` - a shop somebody registered. Unchanged behaviour,
         including raising ShopNotFoundError, so every existing adapter path
         behaves exactly as before this function existed.
      2. `pickup_address` - a typed address. Geocoded (once ever, via the address
         cache), deduped on the normalized form, and remembered as a Shop.
      3. `pickup_lat`/`pickup_lng` - coordinates supplied directly, no geocoding.

    **Why create a Shop rather than teach the pipeline to live without one.**
    Pickup location is Shop-dependent through four layers: HeldOrder clusters on
    shop coordinates, the optimizer builds StopCandidates from them,
    `accept_offer` groups pickups by shop_id, and the HOT_SHOT non-commingling
    guarantee keys off that grouping. Threading coordinates onto Order instead
    would mean reworking all four - including the one guarantee we actually sell.
    Creating the Shop leaves every one of them untouched.

    It also isn't a workaround: "without pre-registration" means the *customer*
    registers nothing, not that the system refuses to learn an address.
    """
    if lmx.client_id is None:
        # Shop.client_id is NOT NULL, and an order with no client relationship
        # has no shop to belong to. Nothing on this path produces one today -
        # the aggregator adapter that would is still gated - so this is a guard
        # against a future caller, not a live branch.
        raise OriginUnresolvableError(
            "cannot resolve an origin for an order with no client_id"
        )

    if lmx.shop_external_ref is not None:
        return await _resolve_shop(session, lmx.client_id, lmx.shop_external_ref)

    lat, lng = lmx.pickup_lat, lmx.pickup_lng
    address = lmx.pickup_address

    if address is None:
        # Coordinates only. Validated by LMXOrder's origin validator, so both are
        # present if we got here.
        address = f"{lat:.6f},{lng:.6f}"

    ref = _auto_shop_external_ref(address)
    existing = await _find_shop_by_ref(session, lmx.client_id, ref)
    if existing is not None:
        return existing

    if lat is None or lng is None:
        resolved = await resolve_address(session, address, geocoder=geocoder)
        if resolved is None:
            raise OriginUnresolvableError(
                f"could not geocode pickup address {address!r} - the order cannot be "
                "dispatched without coordinates"
            )
        lat, lng = resolved.lat, resolved.lng

    shop = Shop(
        client_id=uuid.UUID(lmx.client_id),
        # What the customer typed, not the geocoder's canonical form - this is
        # the label they will see on their own orders, so it should read the way
        # they think of the place.
        name=address[:160],
        address=address[:255],
        lat=lat,
        lng=lng,
        phone=lmx.pickup_contact_phone,
        external_ref=ref,
    )
    session.add(shop)
    await session.flush()
    logger.info(
        "adhoc_shop_created",
        shop_id=str(shop.id),
        client_id=lmx.client_id,
        external_ref=ref,
    )
    return shop


async def _load_sla_overrides(
    session: AsyncSession, hub_id: str, shop_id: str
) -> list[HoldWindowOverride]:
    result = await session.execute(
        select(ActiveRule).where(
            ActiveRule.rule_type == "sla_hold_window_override",
            ActiveRule.enabled.is_(True),
            ActiveRule.hub_id == uuid.UUID(hub_id),
        )
    )
    overrides: list[HoldWindowOverride] = []
    shop_scoped: list[HoldWindowOverride] = []
    hub_scoped: list[HoldWindowOverride] = []

    for rule in result.scalars():
        override = HoldWindowOverride(
            scope_shop_id=rule.scope.get("shop_id"),
            scope_hub_id=hub_id,
            tier_minutes=rule.value,
        )
        if override.scope_shop_id == shop_id:
            shop_scoped.append(override)
        elif override.scope_shop_id is None:
            hub_scoped.append(override)

    # Most specific first: shop-level overrides checked before hub-level.
    overrides.extend(shop_scoped)
    overrides.extend(hub_scoped)
    return overrides


async def _load_tier_overrides(session: AsyncSession, hub_id: str) -> list[TierOverride]:
    """Orchestrator-authored urgency rules for this hub (docs/ROADMAP.md W6),
    ordered oldest-first so the first-created matching rule wins deterministically."""
    result = await session.execute(
        select(ActiveRule)
        .where(
            ActiveRule.rule_type == "tier_override",
            ActiveRule.enabled.is_(True),
            ActiveRule.hub_id == uuid.UUID(hub_id),
        )
        .order_by(ActiveRule.created_at)
    )
    overrides: list[TierOverride] = []
    for rule in result.scalars():
        match_key = rule.value.get("match_key")
        match_value = rule.value.get("match_value")
        tier = rule.value.get("tier")
        # Skip a malformed rule rather than crash ingestion - the authoring
        # endpoint validates shape, but a hand-edited/legacy row shouldn't be
        # able to take the pipeline down.
        if match_key and match_value and tier:
            overrides.append(TierOverride(match_key=match_key, match_value=match_value, tier=tier))
    return overrides


async def _price_order(
    session: AsyncSession,
    *,
    client_id: str,
    sla_tier: str,
    lmx: LMXOrder,
    order: Order,
    shop: Shop,
) -> tuple[int | None, dict | None, uuid.UUID | None]:
    """(fee_cents, breakdown, rate_version_id) for this order, from the client's rate.

    Returns (None, None) when the client has no ClientRate for this tier yet -
    `Order.fee_cents`' docstring is explicit that null must never look like a free
    delivery. Logged as a warning rather than raised: a missing rate is an onboarding gap
    ops needs to fix, not a reason to fail ingestion for every order from that client.

    **Priced here, once, and frozen on the order.** That was already true and matters more
    now that a rate can have a per-mile component: a rate card edited mid-month must not
    retroactively reprice work already taken, and with a distance term it would move
    numbers a client has already been quoted.
    """
    # The version in force *now* - the newest one whose effective date has passed (T2.5 A1).
    # A card with a future `effective_from` is a scheduled change and must not price today's
    # order, which is the whole point of being able to enter a negotiated increase when it
    # is agreed rather than on the morning it starts.
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ClientRate)
        .where(
            ClientRate.client_id == uuid.UUID(client_id),
            ClientRate.sla_tier == sla_tier,
            ClientRate.effective_from <= now,
        )
        .order_by(ClientRate.effective_from.desc())
        .limit(1)
    )
    rate = result.scalar_one_or_none()
    if rate is None:
        logger.warning(
            "client_rate_missing", client_id=client_id, sla_tier=sla_tier,
            detail="No ClientRate configured for this client/tier - fee_cents left null.",
        )
        return None, None, None

    priced = price_drop(
        rate,
        miles=distance_between(
            shop.lat, shop.lng, order.delivery_lat, order.delivery_lng
        ),
        # From the contract object, not the row: line items are carried on LMXOrder and
        # are not a column on Order, so reading them off the row would price every
        # piece-rate contract at zero pieces and never say so.
        pieces=len(lmx.line_items),
        weight_units=float(order.weight_units or 0),
    )
    return priced.fee_cents, priced.breakdown, rate.id


def _lmx_from_normalized(normalized: NormalizedOrder, payload: dict) -> LMXOrder:
    """Lift an adapter's output into the canonical contract.

    `NormalizedOrder` stays exactly as it was - the narrow thing a POS/DMS
    adapter emits, carrying no destination, no windows and no commitment owner.
    This is where it becomes an LMXOrder so there is only ever ONE persistence
    path, which is the entire point of having a contract (§1.1).

    An adapter order is always `sla_owner='LMX'`: a distributor's POS states what
    they want, and we decide the tier and own the clock.
    """
    return LMXOrder(
        source_system=normalized.source_system,
        source_order_ref=normalized.external_order_ref,
        hub_id=normalized.hub_id,
        client_id=normalized.client_id,
        shop_external_ref=normalized.shop_external_ref,
        pickup_lat=normalized.shop_lat,
        pickup_lng=normalized.shop_lng,
        sla_owner="LMX",
        total_weight_units=normalized.weight_units,
        received_at=normalized.requested_at,
        raw_payload=normalized.raw_payload or payload,
    )


async def ingest_lmx_order(
    session: AsyncSession,
    hold_queue: HoldQueueStore,
    lmx: LMXOrder,
    *,
    geocoder: BaseGeocoder,
    payload: dict | None = None,
) -> Order:
    """The one ingestion path (docs/LMX_LINK_PLAN.md §1.1).

    Every source - adapters today, the client portal and later the CSV drop and
    REST webhook - lands here. Nothing downstream of this function knows or
    branches on where the order came from; `source_system` is the only record of
    it.

    Raises ShopNotFoundError for an unknown registered shop, or
    OriginUnresolvableError when a typed address cannot be geocoded.
    """
    payload = payload if payload is not None else lmx.raw_payload

    shop = await _resolve_or_create_shop(session, lmx, geocoder=geocoder)

    # Resolve the destination too, when one was given without coordinates.
    # Adapter orders carry no destination at all today (Order.delivery_address's
    # docstring), so this is skipped entirely on that path and only runs for
    # sources that actually state a drop - which today means the client portal.
    drop_lat, drop_lng = lmx.drop_lat, lmx.drop_lng
    if drop_lat is None and lmx.drop_address_raw:
        resolved_drop = await resolve_address(session, lmx.drop_address_raw, geocoder=geocoder)
        if resolved_drop is None:
            raise DestinationUnresolvableError(
                f"could not geocode delivery address {lmx.drop_address_raw!r} - the order "
                "would be collected with nowhere to take it"
            )
        drop_lat, drop_lng = resolved_drop.lat, resolved_drop.lng

    order = Order(
        hub_id=uuid.UUID(lmx.hub_id),
        client_id=uuid.UUID(lmx.client_id) if lmx.client_id else None,
        shop_id=shop.id,
        external_order_ref=lmx.source_order_ref,
        source_order_ref=lmx.source_order_ref,
        source_system=lmx.source_system,
        raw_payload=lmx.raw_payload,
        weight_units=lmx.total_weight_units,
        status=OrderStatus.received,
        requested_at=lmx.received_at or datetime.now(timezone.utc),
        # Contract fields (§1.2). The pickup address stays populated even after a
        # Shop exists for it - it is the raw thing the customer actually typed.
        sla_owner=lmx.sla_owner,
        pickup_address=lmx.pickup_address,
        pickup_contact_name=lmx.pickup_contact_name,
        pickup_contact_phone=lmx.pickup_contact_phone,
        ready_at=lmx.ready_at,
        pickup_window_start=lmx.pickup_window_start,
        pickup_window_end=lmx.pickup_window_end,
        delivery_window_start=lmx.delivery_window_start,
        delivery_window_end=lmx.delivery_window_end,
        promised_at=lmx.promised_at,
        assignment_scope=lmx.assignment_scope,
        proof_requirements=lmx.proof.model_dump(),
        entry_seconds=lmx.entry_seconds,
        revenue_basis=lmx.economics.revenue_basis,
        quoted_amount_cents=lmx.economics.quoted_amount_cents,
        payer_type=lmx.economics.payer_type,
        payment_status=lmx.economics.payment_status,
        cod_amount_cents=lmx.economics.cod_amount_cents,
        modality_assigned=lmx.modality_assigned,
        # Destination, geocoded above when the source gave an address without
        # coordinates.
        delivery_address=lmx.drop_address_raw,
        delivery_lat=drop_lat,
        delivery_lng=drop_lng,
        delivery_contact_name=lmx.drop_contact_name,
        delivery_contact_phone=lmx.drop_contact_phone,
        delivery_notes=lmx.access_notes,
    )
    session.add(order)
    await session.flush()  # assigns order.id without committing

    # Package identity (docs/ROADMAP.md W10): one Parcel per physical box,
    # so scan-at-pickup can verify against the expected order later.
    for barcode in _parcel_barcodes(payload):
        session.add(Parcel(hub_id=order.hub_id, order_id=order.id, barcode=barcode))

    # Core/return expected back with this delivery (docs/ROADMAP.md W1): the
    # driver collects it on the delivery visit (piggyback). Flagged either
    # by a boolean core_return or by a return_manifest string in the payload.
    return_manifest = _expected_return_manifest(payload)
    if return_manifest is not None:
        session.add(
            ReturnItem(
                hub_id=order.hub_id, origin_order_id=order.id, shop_id=order.shop_id,
                manifest=return_manifest, status="expected",
            )
        )

    now = datetime.now(timezone.utc)

    # THE ONE BRANCH ON COMMITMENT OWNERSHIP (§1.3). Everything downstream -
    # the hold queue, the optimizer, the driver app - is identical either way,
    # because the queue holds against whatever deadline ends up on the object.
    if lmx.needs_classification:
        overrides = await _load_sla_overrides(session, lmx.hub_id, str(shop.id))
        tier_overrides = await _load_tier_overrides(session, lmx.hub_id)
        classified = classify_order(
            _lmx_to_normalized_for_classification(lmx, shop),
            now=now,
            overrides=overrides,
            tier_overrides=tier_overrides,
        )
        sla_tier = classified.sla_tier
        hold_deadline = classified.hold_deadline
        reason = classified.reason
    else:
        # EXTERNAL: somebody else promised the customer a window, so we enforce
        # it rather than reclassifying. The window IS the deadline.
        sla_tier = lmx.sla_tier or _DEFAULT_EXTERNAL_TIER
        hold_deadline = lmx.delivery_window_end
        reason = "external commitment - window accepted as given, not classified"

    order.sla_tier = sla_tier
    order.hold_deadline = hold_deadline
    # No client relationship means nothing to bill against. Skipped rather than
    # logged as a missing rate, which would be misleading - there is no rate to
    # be missing.
    if lmx.client_id:
        order.fee_cents, order.fee_breakdown, order.rate_version_id = await _price_order(
            session,
            client_id=lmx.client_id,
            sla_tier=sla_tier,
            lmx=lmx,
            order=order,
            shop=shop,
        )
    else:
        order.fee_cents, order.fee_breakdown, order.rate_version_id = None, None, None
    order.status = OrderStatus.held
    await session.commit()
    metrics.ORDERS_INGESTED.labels(hub_id=lmx.hub_id, source_system=lmx.source_system).inc()

    await hold_queue.add(
        lmx.hub_id,
        HeldOrder(
            order_id=str(order.id),
            shop_lat=shop.lat,
            shop_lng=shop.lng,
            sla_tier=sla_tier,
            hold_deadline=hold_deadline,
            held_since=now,
            shop_name=shop.name,
            # Carried so the routing solver plans the real journey rather than a
            # visit to the shop - see StopCandidate.delivery_lat. Numeric(9,6)
            # comes back as Decimal, which json.dumps can't serialize, so the
            # float() is load-bearing rather than tidiness.
            delivery_lat=float(order.delivery_lat) if order.delivery_lat is not None else None,
            delivery_lng=float(order.delivery_lng) if order.delivery_lng is not None else None,
        ),
    )

    logger.info(
        "order_ingested",
        order_id=str(order.id),
        hub_id=lmx.hub_id,
        source_system=lmx.source_system,
        sla_owner=lmx.sla_owner,
        sla_tier=sla_tier,
        reason=reason,
    )
    return order


def _lmx_to_normalized_for_classification(lmx: LMXOrder, shop: Shop) -> NormalizedOrder:
    """The shape `app/sla/engine.py::classify_order` expects.

    Kept as an explicit adaptation rather than changing the SLA engine's
    signature: the engine is spec-verified logic (E4/E5 corrected its hold
    windows against the technical design doc) and widening its input to a bigger
    object would put that verification at risk for no benefit. It only ever
    needed these fields.
    """
    return NormalizedOrder(
        external_order_ref=lmx.source_order_ref,
        source_system=lmx.source_system,
        hub_id=lmx.hub_id,
        client_id=lmx.client_id or "",
        shop_external_ref=shop.external_ref or "",
        shop_lat=shop.lat,
        shop_lng=shop.lng,
        weight_units=lmx.total_weight_units,
        requested_at=lmx.received_at or datetime.now(timezone.utc),
        raw_payload=lmx.raw_payload,
    )


async def ingest_order(
    session: AsyncSession,
    hold_queue: HoldQueueStore,
    *,
    hub_id: str,
    client_id: str,
    source_system: str,
    payload: dict,
) -> Order:
    """
    Full ingestion pipeline for a single order from a POS/DMS adapter. Raises
    IngestionAdapterError or ShopNotFoundError on bad input - callers (the
    router) translate those into 4xx responses.

    Signature unchanged. This now maps the adapter's NormalizedOrder into the
    canonical contract and delegates to `ingest_lmx_order`, so there is one
    persistence path rather than two that can drift.
    """
    adapter = get_adapter(source_system)
    normalized = adapter.normalize(hub_id, client_id, payload)
    lmx = _lmx_from_normalized(normalized, payload)
    # An adapter order always names a registered shop, so no geocoding happens
    # on this path - the geocoder is passed for interface uniformity and is
    # never called.
    return await ingest_lmx_order(
        session, hold_queue, lmx, geocoder=get_geocoder(), payload=payload
    )
