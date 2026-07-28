"""
Shared helpers for returns & core pickups (docs/ROADMAP.md W1). Kept out of
the route modules so both the driver-facing endpoints and the ops-facing
list build the same ReturnItemView the same way.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.return_item import ReturnItem
from app.schemas.returns import ReturnItemView


async def return_views(session: AsyncSession, items: list[ReturnItem]) -> list[ReturnItemView]:
    """Build views, resolving each return's originating order reference in a
    single lookup rather than one query per item."""
    if not items:
        return []
    order_ids = {item.origin_order_id for item in items}
    refs_result = await session.execute(
        select(Order.id, Order.external_order_ref).where(Order.id.in_(order_ids))
    )
    ref_by_id = {row[0]: row[1] for row in refs_result.all()}
    return [
        ReturnItemView(
            return_id=str(item.id),
            origin_order_ref=ref_by_id.get(item.origin_order_id, ""),
            manifest=item.manifest,
            status=item.status,
            collected_at=item.collected_at.isoformat() if item.collected_at else None,
            returned_at=item.returned_at.isoformat() if item.returned_at else None,
        )
        for item in items
    ]
