"""
DB-facing orchestration for the Annotation and Learning Loop's nightly job
(component 6). Thin wrapper around app/learning_loop/detection.py: load
recent stop_flags for a hub, resolve each shop's current effective SLA
minutes, run detection, and write proposed_rules rows - skipping shops that
already have a pending proposal so re-running the job doesn't spam
duplicates.

Nothing here promotes a proposed_rule to active_rules automatically - per
LMX_OS_Peer_Review.md there's no auto-approval path in Phase 1, a human
reviews proposed_rules and promotes deliberately.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.learning_loop.detection import FlagRecord, RELEVANT_FLAG_TYPES, detect_patterns
from app.models.rules import ActiveRule, ProposedRule
from app.models.stop import Stop, StopFlag
from app.models.route import Route
from app.sla.engine import DEFAULT_HOLD_WINDOW_MINUTES

DEFAULT_LOOKBACK_DAYS = 14


async def _load_recent_flags(
    session: AsyncSession, hub_id: str, lookback_days: int
) -> list[FlagRecord]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    result = await session.execute(
        select(StopFlag.flag_type, StopFlag.created_at, Stop.shop_id)
        .join(Stop, StopFlag.stop_id == Stop.id)
        .join(Route, Stop.route_id == Route.id)
        .where(
            Route.hub_id == uuid.UUID(hub_id),
            StopFlag.flag_type.in_(RELEVANT_FLAG_TYPES),
            StopFlag.created_at >= cutoff,
        )
    )
    return [
        FlagRecord(shop_id=str(shop_id), flag_type=flag_type, created_at=created_at)
        for flag_type, created_at, shop_id in result.all()
    ]


async def _resolve_current_minutes(
    session: AsyncSession, hub_id: str, shop_ids: set[str]
) -> dict[str, dict[str, int]]:
    """
    Same precedence as app/ingestion/service.py's _load_sla_overrides:
    shop-level active_rules override wins, then hub-level, then the
    hardcoded defaults in app/sla/engine.py.
    """
    result = await session.execute(
        select(ActiveRule).where(
            ActiveRule.rule_type == "sla_hold_window_override",
            ActiveRule.enabled.is_(True),
            ActiveRule.hub_id == uuid.UUID(hub_id),
        )
    )
    hub_level: dict[str, int] = {}
    shop_level: dict[str, dict[str, int]] = {}
    for rule in result.scalars():
        scope_shop_id = rule.scope.get("shop_id")
        if scope_shop_id is None:
            hub_level.update(rule.value)
        else:
            shop_level.setdefault(scope_shop_id, {}).update(rule.value)

    resolved: dict[str, dict[str, int]] = {}
    for shop_id in shop_ids:
        minutes = dict(DEFAULT_HOLD_WINDOW_MINUTES)
        minutes.update(hub_level)
        minutes.update(shop_level.get(shop_id, {}))
        resolved[shop_id] = minutes
    return resolved


async def _shops_with_pending_proposal(
    session: AsyncSession, hub_id: str, shop_ids: set[str]
) -> set[str]:
    if not shop_ids:
        return set()
    result = await session.execute(
        select(ProposedRule.scope).where(
            ProposedRule.hub_id == uuid.UUID(hub_id),
            ProposedRule.rule_type == "sla_hold_window_override",
            ProposedRule.status == "pending_review",
        )
    )
    pending: set[str] = set()
    for (scope,) in result.all():
        scope_shop_id = scope.get("shop_id")
        if scope_shop_id in shop_ids:
            pending.add(scope_shop_id)
    return pending


async def run_nightly_job(
    session: AsyncSession, *, hub_id: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> list[ProposedRule]:
    """
    Runs pattern detection for one hub and persists any new proposals.
    Returns the ProposedRule rows created on this run (empty list if
    nothing new was detected, or everything detected already has a
    pending proposal).
    """
    flags = await _load_recent_flags(session, hub_id, lookback_days)
    if not flags:
        return []

    shop_ids = {f.shop_id for f in flags}
    current_minutes_by_shop = await _resolve_current_minutes(session, hub_id, shop_ids)
    patterns = detect_patterns(flags, current_minutes_by_shop)
    if not patterns:
        return []

    already_pending = await _shops_with_pending_proposal(session, hub_id, {p.shop_id for p in patterns})

    created: list[ProposedRule] = []
    for pattern in patterns:
        if pattern.shop_id in already_pending:
            continue
        proposed = ProposedRule(
            hub_id=uuid.UUID(hub_id),
            rule_type="sla_hold_window_override",
            scope={"shop_id": pattern.shop_id},
            proposed_change=pattern.proposed_tier_minutes,
            confidence=pattern.confidence,
            supporting_annotation_count=pattern.occurrence_count,
            status="pending_review",
        )
        session.add(proposed)
        created.append(proposed)

    if created:
        await session.commit()
    return created
