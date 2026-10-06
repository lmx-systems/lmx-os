"""Orchestrator rules that change how long an order is held."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rules import ActiveRule
from app.sla.engine import HoldWindowOverride


async def load_hold_window_overrides(
    session: AsyncSession, hub_id: str, shop_id: str
) -> list[HoldWindowOverride]:
    """The hold-window overrides that apply to one shop's orders, most specific
    first: shop-scoped rules, then hub-wide ones (`active_rules`,
    rule_type='sla_hold_window_override', written by an approved learning-loop
    proposal).

    Here rather than in intake so that everything which sets a hold deadline
    reads the same rules. Redelivery (`app/delivery/resolution.py`) is core and
    cannot import intake; before this it used the default windows alone.
    """
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
