"""
Shared helpers for returns & core pickups (docs/ROADMAP.md W1). Kept out of
the route modules so both the driver-facing endpoints and the ops-facing
list build the same ReturnItemView the same way.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.return_item import ReturnItem
from app.models.shop import Shop
from app.schemas.returns import ReturnItemView

# Statuses still waiting on a pickup - the counter-facing "awaiting pickup"
# cut (docs/ROADMAP.md W1 slice 4). `collected` is already in the driver's
# hands and `returned_to_shop`/`cancelled` are terminal, so none of them are
# awaiting.
AWAITING_STATUSES = ("expected", "ready_for_pickup", "not_ready")


async def return_views(session: AsyncSession, items: list[ReturnItem]) -> list[ReturnItemView]:
    """Build views, resolving each return's originating order ref (blank for a
    standalone return) and shop name in one lookup each rather than per item."""
    if not items:
        return []
    order_ids = {item.origin_order_id for item in items if item.origin_order_id is not None}
    ref_by_id: dict = {}
    if order_ids:
        refs_result = await session.execute(
            select(Order.id, Order.external_order_ref).where(Order.id.in_(order_ids))
        )
        ref_by_id = {row[0]: row[1] for row in refs_result.all()}

    shop_ids = {item.shop_id for item in items}
    shops_result = await session.execute(select(Shop.id, Shop.name).where(Shop.id.in_(shop_ids)))
    shop_by_id = {row[0]: row[1] for row in shops_result.all()}

    now = datetime.now(timezone.utc)
    return [
        ReturnItemView(
            return_id=str(item.id),
            origin_order_ref=ref_by_id.get(item.origin_order_id, "") if item.origin_order_id else "",
            shop_name=shop_by_id.get(item.shop_id),
            manifest=item.manifest,
            status=item.status,
            created_at=item.created_at.isoformat(),
            age_hours=round((now - item.created_at).total_seconds() / 3600, 1),
            collected_at=item.collected_at.isoformat() if item.collected_at else None,
            returned_at=item.returned_at.isoformat() if item.returned_at else None,
        )
        for item in items
    ]
