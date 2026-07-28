"""
Rule review & promotion (docs/ROADMAP.md I2) - the missing rung of the
Annotation & Learning Loop (component 6).

The nightly job (app/learning_loop/service.py) accumulates ProposedRule
rows but nothing promotes them - until now that was a manual SQL insert.
This is the human-approval step: an ops admin approves a proposal, which
copies it into active_rules where the SLA engine / ingestion actually read
it (app/ingestion/service.py's _load_sla_overrides), or dismisses it.

The promotion is a faithful field copy - rule_type and scope carry over
unchanged and proposed_change becomes the active rule's value - so an
approved proposal lands in exactly the shape its consumer already queries
for (e.g. rule_type='sla_hold_window_override', value=tier_minutes).
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rules import ActiveRule, ProposedRule

PENDING = "pending_review"
APPROVED = "approved"
REJECTED = "rejected"


class ProposedRuleNotPendingError(Exception):
    """Only a still-pending proposal can be approved or dismissed - one
    already approved/rejected has been decided, and re-approving would
    create a duplicate active rule."""


async def promote_proposed_rule(session: AsyncSession, proposed: ProposedRule) -> ActiveRule:
    if proposed.status != PENDING:
        raise ProposedRuleNotPendingError(
            f"Proposed rule {proposed.id} is '{proposed.status}', not '{PENDING}'"
        )
    active = ActiveRule(
        hub_id=proposed.hub_id,
        rule_type=proposed.rule_type,
        scope=proposed.scope,
        value=proposed.proposed_change,
        promoted_from_proposed_rule_id=proposed.id,
        enabled=True,
    )
    session.add(active)
    proposed.status = APPROVED
    await session.commit()
    return active


async def dismiss_proposed_rule(session: AsyncSession, proposed: ProposedRule) -> ProposedRule:
    if proposed.status != PENDING:
        raise ProposedRuleNotPendingError(
            f"Proposed rule {proposed.id} is '{proposed.status}', not '{PENDING}'"
        )
    proposed.status = REJECTED
    await session.commit()
    return proposed
