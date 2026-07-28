"""
Learning-Loop rule review & promotion (docs/ROADMAP.md I2) against real
Postgres. Covers the promotion service + admin endpoints, including the
end-to-end proof that an approved proposal actually reaches the consumer
(ingestion's _load_sla_overrides) - i.e. the loop actually closes.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import (
    approve_proposed_rule,
    dismiss_proposed_rule_endpoint,
    list_proposed_rules,
)
from app.ingestion.service import _load_sla_overrides
from app.models.hub import Hub
from app.models.rules import ActiveRule, ProposedRule

pytestmark = pytest.mark.integration


async def _seed_hub(db_session) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Promotion Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    return hub_id


async def _seed_proposal(db_session, hub_id, *, shop_id="shop-1", tier_minutes=None, status="pending_review"):
    proposal = ProposedRule(
        hub_id=hub_id,
        rule_type="sla_hold_window_override",
        scope={"shop_id": shop_id},
        proposed_change=tier_minutes or {"T2": 45},
        confidence=0.9,
        supporting_annotation_count=4,
        status=status,
    )
    db_session.add(proposal)
    await db_session.commit()
    return proposal


async def test_approving_a_proposal_creates_an_active_rule_that_ingestion_consumes(db_session):
    hub_id = await _seed_hub(db_session)
    shop_id = str(uuid.uuid4())
    proposal = await _seed_proposal(db_session, hub_id, shop_id=shop_id, tier_minutes={"T2": 45})

    result = await approve_proposed_rule(str(proposal.id), session=db_session)
    assert result.status == "approved"
    assert result.active_rule_id is not None

    # The proposal is marked approved and a faithful active_rule now exists.
    active = await db_session.get(ActiveRule, uuid.UUID(result.active_rule_id))
    assert active.rule_type == "sla_hold_window_override"
    assert active.scope == {"shop_id": shop_id}
    assert active.value == {"T2": 45}
    assert str(active.promoted_from_proposed_rule_id) == str(proposal.id)
    assert active.enabled is True

    # End-to-end: the promoted rule is what ingestion actually reads for
    # that shop - the loop closes, it's not a write nothing consumes.
    overrides = await _load_sla_overrides(db_session, str(hub_id), shop_id)
    assert any(o.scope_shop_id == shop_id and o.tier_minutes == {"T2": 45} for o in overrides)


async def test_dismissing_a_proposal_marks_it_rejected_and_creates_no_active_rule(db_session):
    hub_id = await _seed_hub(db_session)
    proposal = await _seed_proposal(db_session, hub_id)

    result = await dismiss_proposed_rule_endpoint(str(proposal.id), session=db_session)
    assert result.status == "rejected"
    assert result.active_rule_id is None

    active_count = await db_session.execute(
        select(ActiveRule).where(ActiveRule.hub_id == hub_id)
    )
    assert active_count.scalars().first() is None


async def test_cannot_approve_or_dismiss_an_already_decided_proposal(db_session):
    hub_id = await _seed_hub(db_session)
    approved = await _seed_proposal(db_session, hub_id, status="approved")
    with pytest.raises(HTTPException) as exc:
        await approve_proposed_rule(str(approved.id), session=db_session)
    assert exc.value.status_code == 409

    rejected = await _seed_proposal(db_session, hub_id, status="rejected")
    with pytest.raises(HTTPException) as exc:
        await dismiss_proposed_rule_endpoint(str(rejected.id), session=db_session)
    assert exc.value.status_code == 409


async def test_approve_and_dismiss_404_for_unknown_proposal(db_session):
    with pytest.raises(HTTPException) as exc:
        await approve_proposed_rule(str(uuid.uuid4()), session=db_session)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await dismiss_proposed_rule_endpoint(str(uuid.uuid4()), session=db_session)
    assert exc.value.status_code == 404


async def test_list_returns_only_pending_proposals(db_session):
    hub_id = await _seed_hub(db_session)
    pending = await _seed_proposal(db_session, hub_id, shop_id="pending-shop")
    await _seed_proposal(db_session, hub_id, shop_id="approved-shop", status="approved")

    listed = await list_proposed_rules(str(hub_id), session=db_session)
    assert [r.rule_id for r in listed] == [str(pending.id)]
