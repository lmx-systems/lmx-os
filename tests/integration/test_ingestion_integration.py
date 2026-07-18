"""
Ingestion service against a real Postgres session + real Redis hold queue,
instead of the pure-function/mocked coverage in tests/test_ingestion_adapters.py.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.batch_queue.store import HoldQueueStore
from app.ingestion.service import ShopNotFoundError, ingest_order
from app.models.client import Client
from app.models.hub import Hub
from app.models.shop import Shop

pytestmark = pytest.mark.integration


async def _seed_hub_client_shop(db_session, external_ref: str = "SHOP-1"):
    # Committed in FK dependency order (hub -> client -> shop) rather than
    # one add_all + commit: SQLAlchemy's flush-order sorting is driven by
    # relationship()-mapped dependencies, which these models deliberately
    # don't use (plain FK columns only - see app/models/*.py) - so nothing
    # guarantees hub gets inserted before client in a single flush.
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    db_session.add(Hub(id=hub_id, name="Integration Test Hub", lat=34.05, lng=-118.25))
    await db_session.commit()

    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()

    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Test Shop",
            address="123 Main St",
            lat=34.06,
            lng=-118.24,
            external_ref=external_ref,
        )
    )
    await db_session.commit()

    return hub_id, client_id, shop_id


async def test_ingest_order_persists_and_lands_in_hold_queue(db_session, real_redis_client):
    hub_id, client_id, _shop_id = await _seed_hub_client_shop(db_session)
    hold_queue = HoldQueueStore()

    payload = {
        "order_ref": "ORD-INT-1",
        "shop_ref": "SHOP-1",
        "shop_lat": 34.06,
        "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    order = await ingest_order(
        db_session,
        hold_queue,
        hub_id=str(hub_id),
        client_id=str(client_id),
        source_system="flat_file",
        payload=payload,
    )

    assert order.sla_tier == "T2"
    assert order.status.value == "held"
    assert order.hold_deadline is not None

    held = await hold_queue.get_all(str(hub_id))
    assert len(held) == 1
    assert held[0].order_id == str(order.id)
    assert held[0].sla_tier == "T2"


async def test_ingest_order_with_rush_flag_classifies_t1(db_session, real_redis_client):
    hub_id, client_id, _shop_id = await _seed_hub_client_shop(db_session)
    hold_queue = HoldQueueStore()

    payload = {
        "order_ref": "ORD-INT-2",
        "shop_ref": "SHOP-1",
        "shop_lat": 34.06,
        "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "rush": True,
    }

    order = await ingest_order(
        db_session,
        hold_queue,
        hub_id=str(hub_id),
        client_id=str(client_id),
        source_system="flat_file",
        payload=payload,
    )

    assert order.sla_tier == "T1"


async def test_ingest_order_raises_for_unknown_shop(db_session, real_redis_client):
    hub_id, client_id, _shop_id = await _seed_hub_client_shop(db_session)
    hold_queue = HoldQueueStore()

    payload = {
        "order_ref": "ORD-INT-3",
        "shop_ref": "SHOP-DOES-NOT-EXIST",
        "shop_lat": 34.06,
        "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    with pytest.raises(ShopNotFoundError):
        await ingest_order(
            db_session,
            hold_queue,
            hub_id=str(hub_id),
            client_id=str(client_id),
            source_system="flat_file",
            payload=payload,
        )
