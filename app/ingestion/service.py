"""
Order Ingestion Layer service (component 1).

Orchestrates: adapter.normalize() -> resolve shop -> persist Order row ->
Dynamic SLA Engine classification -> push into the Batch-Hold Queue.
This is the only place that wires those pieces together, so ingestion
behavior can be reasoned about from one file.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.ingestion.registry import get_adapter
from app.models.order import Order, OrderStatus
from app.models.rules import ActiveRule
from app.models.shop import Shop
from app.sla.engine import HoldWindowOverride, classify_order

logger = structlog.get_logger(__name__)


class ShopNotFoundError(Exception):
    pass


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
    Full ingestion pipeline for a single order. Raises IngestionAdapterError
    or ShopNotFoundError on bad input - callers (the router) translate those
    into 4xx responses.
    """
    adapter = get_adapter(source_system)
    normalized = adapter.normalize(hub_id, client_id, payload)

    shop = await _resolve_shop(session, client_id, normalized.shop_external_ref)

    order = Order(
        hub_id=uuid.UUID(hub_id),
        client_id=uuid.UUID(client_id),
        shop_id=shop.id,
        external_order_ref=normalized.external_order_ref,
        source_system=normalized.source_system,
        raw_payload=normalized.raw_payload,
        weight_units=normalized.weight_units,
        status=OrderStatus.received,
        requested_at=normalized.requested_at,
    )
    session.add(order)
    await session.flush()  # assigns order.id without committing

    overrides = await _load_sla_overrides(session, hub_id, str(shop.id))
    now = datetime.now(timezone.utc)
    classified = classify_order(normalized, now=now, overrides=overrides)

    order.sla_tier = classified.sla_tier
    order.hold_deadline = classified.hold_deadline
    order.status = OrderStatus.held
    await session.commit()

    await hold_queue.add(
        hub_id,
        HeldOrder(
            order_id=str(order.id),
            shop_lat=shop.lat,
            shop_lng=shop.lng,
            sla_tier=classified.sla_tier,
            hold_deadline=classified.hold_deadline,
            held_since=now,
        ),
    )

    logger.info(
        "order_ingested",
        order_id=str(order.id),
        hub_id=hub_id,
        source_system=source_system,
        sla_tier=classified.sla_tier,
        reason=classified.reason,
    )
    return order
