"""
Returns & core pickups, slice 1 (docs/ROADMAP.md W1) against real
Postgres/Redis: expected returns created at ingestion, collected on the
delivery visit, plus the not-ready path and the ops list.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.admin_routes import list_returns
from app.api.client_routes import flag_shop_returns_ready, list_my_returns
from app.api.driver_routes import collect_return, return_not_ready
from app.batch_queue.store import HoldQueueStore
from app.client_auth.dependencies import AuthedClient
from app.driver_auth.dependencies import AuthedDriver
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.return_item import ReturnItem
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.schemas.returns import CollectReturnBody, ReturnFlagBody

pytestmark = pytest.mark.integration


async def _seed_hcs(db_session, external_ref="SHOP-RET"):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Returns Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(id=shop_id, client_id=client_id, name="Returns Shop", address="1 Main St",
             lat=34.06, lng=-118.24, external_ref=external_ref)
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


async def _ingest(db_session, hub_id, client_id, ref="ORD-RET", **extra):
    payload = {
        "order_ref": ref, "shop_ref": "SHOP-RET", "shop_lat": 34.06, "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "delivery_lat": 34.07, "delivery_lng": -118.25,
    }
    payload.update(extra)
    return await ingest_order(
        db_session, HoldQueueStore(),
        hub_id=str(hub_id), client_id=str(client_id), source_system="flat_file", payload=payload,
    )


async def _returns_for(db_session, order_id):
    result = await db_session.execute(select(ReturnItem).where(ReturnItem.origin_order_id == order_id))
    return list(result.scalars().all())


async def _arrived_dropoff(db_session, hub_id, order, stop_type="dropoff"):
    driver_id = uuid.uuid4()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Ret D.", phone="+15555550800", vehicle_capacity_units=5))
    await db_session.commit()
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, shop_id=None, sequence=0, stop_type=stop_type, status="arrived", parcel_count=1)
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.commit()
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d"), stop


async def test_ingestion_creates_expected_return_from_a_flag(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, return_manifest="core: alternator")
    returns = await _returns_for(db_session, order.id)
    assert len(returns) == 1
    assert returns[0].status == "expected"
    assert returns[0].manifest == "core: alternator"


async def test_core_return_bool_gets_a_generic_manifest(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, core_return=True)
    returns = await _returns_for(db_session, order.id)
    assert [r.manifest for r in returns] == ["core exchange"]


async def test_no_flag_creates_no_return(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id)
    assert await _returns_for(db_session, order.id) == []


async def test_collect_return_marks_the_expected_one_collected(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, return_manifest="core: caliper")
    authed, stop = await _arrived_dropoff(db_session, hub_id, order)

    views = await collect_return(str(stop.id), CollectReturnBody(), driver=authed, session=db_session)
    assert len(views) == 1
    assert views[0].status == "collected"
    assert views[0].origin_order_ref == "ORD-RET"

    refreshed = (await _returns_for(db_session, order.id))[0]
    assert refreshed.status == "collected" and refreshed.collected_at is not None


async def test_collect_return_records_an_adhoc_core_when_none_expected(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id)  # no expected return
    authed, stop = await _arrived_dropoff(db_session, hub_id, order)

    views = await collect_return(
        str(stop.id), CollectReturnBody(manifest="unexpected core: starter"), driver=authed, session=db_session
    )
    assert len(views) == 1 and views[0].manifest == "unexpected core: starter"
    assert (await _returns_for(db_session, order.id))[0].status == "collected"


async def test_collect_return_409_when_nothing_expected_and_no_manifest(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id)
    authed, stop = await _arrived_dropoff(db_session, hub_id, order)
    with pytest.raises(HTTPException) as exc:
        await collect_return(str(stop.id), CollectReturnBody(), driver=authed, session=db_session)
    assert exc.value.status_code == 409


async def test_collect_return_rejects_a_pickup_stop(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, return_manifest="core")
    authed, stop = await _arrived_dropoff(db_session, hub_id, order, stop_type="pickup")
    with pytest.raises(HTTPException) as exc:
        await collect_return(str(stop.id), CollectReturnBody(), driver=authed, session=db_session)
    assert exc.value.status_code == 409


async def test_return_not_ready_marks_expected_not_ready(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, return_manifest="core")
    authed, stop = await _arrived_dropoff(db_session, hub_id, order)
    views = await return_not_ready(str(stop.id), driver=authed, session=db_session)
    assert views[0].status == "not_ready"


async def test_admin_list_returns_filters_by_status(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    o1 = await _ingest(db_session, hub_id, client_id, ref="ORD-A", return_manifest="core A")
    await _ingest(db_session, hub_id, client_id, ref="ORD-B", return_manifest="core B")
    # collect one so it moves to 'collected'
    authed, stop = await _arrived_dropoff(db_session, hub_id, o1)
    await collect_return(str(stop.id), CollectReturnBody(), driver=authed, session=db_session)

    all_returns = await list_returns(str(hub_id), session=db_session)
    assert len(all_returns) == 2
    expected_only = await list_returns(str(hub_id), status="expected", session=db_session)
    assert [r.origin_order_ref for r in expected_only] == ["ORD-B"]


# --- slice 2: shop-flagged standalone returns (client portal) ---

def _authed_client(client_id):
    return AuthedClient(client_id=str(client_id), client_user_id="u", email="u@x.example", name="U", role="admin")


async def test_flag_shop_returns_ready_creates_a_standalone_return(db_session):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    view = await flag_shop_returns_ready(
        str(shop_id), ReturnFlagBody(manifest="5 cores accumulated"),
        client=_authed_client(client_id), session=db_session,
    )
    assert view.status == "ready_for_pickup"
    assert view.origin_order_ref == ""  # standalone - no originating delivery
    assert view.shop_name == "Returns Shop"

    result = await db_session.execute(select(ReturnItem).where(ReturnItem.shop_id == shop_id))
    item = result.scalar_one()
    assert item.origin_order_id is None and item.status == "ready_for_pickup"


async def test_flag_rejects_a_shop_that_isnt_the_clients(db_session):
    _hub_a, client_a, _shop_a = await _seed_hcs(db_session, external_ref="SHOP-A")
    _hub_b, _client_b, shop_b = await _seed_hcs(db_session, external_ref="SHOP-B")
    with pytest.raises(HTTPException) as exc:
        await flag_shop_returns_ready(
            str(shop_b), ReturnFlagBody(manifest="x"), client=_authed_client(client_a), session=db_session
        )
    assert exc.value.status_code == 404


async def test_list_my_returns_is_scoped_to_the_client(db_session):
    hub_a, client_a, shop_a = await _seed_hcs(db_session, external_ref="SHOP-A")
    _hub_b, client_b, shop_b = await _seed_hcs(db_session, external_ref="SHOP-B")
    await flag_shop_returns_ready(str(shop_a), ReturnFlagBody(manifest="A cores"), client=_authed_client(client_a), session=db_session)
    await flag_shop_returns_ready(str(shop_b), ReturnFlagBody(manifest="B cores"), client=_authed_client(client_b), session=db_session)

    mine = await list_my_returns(client=_authed_client(client_a), session=db_session)
    assert [r.manifest for r in mine] == ["A cores"]
