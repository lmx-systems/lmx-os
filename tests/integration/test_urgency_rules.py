"""
Orchestrator-editable urgency rules (docs/ROADMAP.md W6) against real
Postgres/Redis: the tier override applied end-to-end through ingestion, and
the admin CRUD for authoring the rules.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.admin_routes import (
    add_urgency_rule,
    list_urgency_rules,
    remove_urgency_rule,
    update_urgency_rule,
)
from app.batch_queue.store import HoldQueueStore
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.hub import Hub
from app.models.rules import ActiveRule
from app.models.shop import Shop
from app.schemas.admin import UrgencyRuleBody, UrgencyRuleUpdateBody

pytestmark = pytest.mark.integration


async def _seed(db_session, external_ref: str = "SHOP-1"):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Urgency Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(id=shop_id, client_id=client_id, name="Test Shop", address="1 Main St",
             lat=34.06, lng=-118.24, external_ref=external_ref)
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


def _payload(**extra):
    base = {
        "order_ref": "ORD-URG-1",
        "shop_ref": "SHOP-1",
        "shop_lat": 34.06,
        "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(extra)
    return base


async def _add_rule(db_session, hub_id, *, match_key, match_value, tier, enabled=True):
    rule = ActiveRule(
        hub_id=hub_id, rule_type="tier_override", scope={},
        value={"match_key": match_key, "match_value": match_value, "tier": tier}, enabled=enabled,
    )
    db_session.add(rule)
    await db_session.commit()
    return rule


async def test_active_rule_overrides_the_tier_at_ingestion(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed(db_session)
    await _add_rule(db_session, hub_id, match_key="part_category", match_value="body_panel", tier="T3")

    # Payload has a rush flag (heuristic would say T1) AND a body_panel part
    # category (ops rule says T3). The ops rule wins.
    order = await ingest_order(
        db_session, HoldQueueStore(),
        hub_id=str(hub_id), client_id=str(client_id), source_system="flat_file",
        payload=_payload(part_category="body_panel", rush=True),
    )
    assert order.sla_tier == "T3"


async def test_disabled_rule_does_not_override(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed(db_session)
    await _add_rule(
        db_session, hub_id, match_key="part_category", match_value="body_panel", tier="T3", enabled=False
    )

    order = await ingest_order(
        db_session, HoldQueueStore(),
        hub_id=str(hub_id), client_id=str(client_id), source_system="flat_file",
        payload=_payload(part_category="body_panel", rush=True),
    )
    assert order.sla_tier == "T1"  # disabled rule ignored -> rush heuristic applies


async def test_add_list_patch_and_delete_urgency_rule(db_session):
    hub_id, _client, _shop = await _seed(db_session)
    hid = str(hub_id)

    created = await add_urgency_rule(
        hid, UrgencyRuleBody(match_key="part_category", match_value="body_panel", tier="T3"), session=db_session
    )
    assert created.tier == "T3"
    assert created.enabled is True

    listed = await list_urgency_rules(hid, session=db_session)
    assert [r.rule_id for r in listed] == [created.rule_id]

    toggled = await update_urgency_rule(
        hid, created.rule_id, UrgencyRuleUpdateBody(enabled=False), session=db_session
    )
    assert toggled.enabled is False

    await remove_urgency_rule(hid, created.rule_id, session=db_session)
    assert await list_urgency_rules(hid, session=db_session) == []


async def test_add_urgency_rule_rejects_bad_tier_and_unknown_hub(db_session):
    hub_id, _client, _shop = await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await add_urgency_rule(
            str(hub_id), UrgencyRuleBody(match_key="k", match_value="v", tier="T99"), session=db_session
        )
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        await add_urgency_rule(
            str(uuid.uuid4()), UrgencyRuleBody(match_key="k", match_value="v", tier="T3"), session=db_session
        )
    assert exc.value.status_code == 404


async def test_cannot_touch_another_hubs_rule(db_session):
    hub_a, _c, _s = await _seed(db_session)
    hub_b, _c2, _s2 = await _seed(db_session, external_ref="SHOP-2")
    rule = await _add_rule(db_session, hub_a, match_key="k", match_value="v", tier="T3")

    with pytest.raises(HTTPException) as exc:
        await remove_urgency_rule(str(hub_b), str(rule.id), session=db_session)
    assert exc.value.status_code == 404
