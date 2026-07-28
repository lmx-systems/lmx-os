"""
Package identity & scan-at-pickup verification (docs/ROADMAP.md W10)
against real Postgres/Redis. Parcels are created through the real
ingestion path; scanning is verified against the pickup stop's order(s).
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import accept_offer, list_my_offers, list_stop_parcels, scan_parcel
from app.batch_queue.store import HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.ingestion.service import ingest_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.parcel import Parcel
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.optimizer.service import DispatchOptimizerService
from app.schemas.driver_app import ScanParcelBody
from app.schemas.fleet import DriverLocation, DriverState

pytestmark = pytest.mark.integration


async def _seed_hcs(db_session, external_ref="SHOP-W10"):
    hub_id, client_id, shop_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="W10 Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(id=shop_id, client_id=client_id, name="W10 Shop", address="1 Main St",
             lat=34.06, lng=-118.24, external_ref=external_ref)
    )
    await db_session.commit()
    return hub_id, client_id, shop_id


def _payload(external_ref="SHOP-W10", ref="ORD-W10", **extra):
    base = {
        "order_ref": ref, "shop_ref": external_ref, "shop_lat": 34.06, "shop_lng": -118.24,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "delivery_lat": 34.07, "delivery_lng": -118.25,
    }
    base.update(extra)
    return base


async def _ingest(db_session, hub_id, client_id, **payload_extra):
    return await ingest_order(
        db_session, HoldQueueStore(),
        hub_id=str(hub_id), client_id=str(client_id), source_system="flat_file",
        payload=_payload(**payload_extra),
    )


async def _parcels_for(db_session, order_id):
    result = await db_session.execute(select(Parcel).where(Parcel.order_id == order_id).order_by(Parcel.barcode))
    return list(result.scalars().all())


# --- parcel creation at ingestion ---

async def test_ingestion_uses_the_distributors_barcodes_when_present(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcels=["PT-100", "PT-101"])
    parcels = await _parcels_for(db_session, order.id)
    assert [p.barcode for p in parcels] == ["PT-100", "PT-101"]


async def test_ingestion_generates_lmx_barcodes_from_a_count(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcel_count=3)
    parcels = await _parcels_for(db_session, order.id)
    assert len(parcels) == 3
    assert all(p.barcode.startswith("LMX-") for p in parcels)


async def test_ingestion_defaults_to_one_parcel(db_session, real_redis_client):
    hub_id, client_id, _shop = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id)
    assert len(await _parcels_for(db_session, order.id)) == 1


# --- scan-at-pickup verification ---

async def _arrived_pickup(db_session, hub_id, shop_id, order, parcel_count):
    driver_id = uuid.uuid4()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Scanner D.", phone="+15555550700", vehicle_capacity_units=5))
    await db_session.commit()
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active", plan_version=1)
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, shop_id=shop_id, sequence=0, stop_type="pickup",
                status="arrived", parcel_count=parcel_count)
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.commit()
    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d")
    return authed, stop


async def test_scanning_valid_barcodes_marks_them_and_bumps_the_count(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcels=["A1", "A2"])
    authed, stop = await _arrived_pickup(db_session, hub_id, shop_id, order, parcel_count=2)

    v1 = await scan_parcel(str(stop.id), ScanParcelBody(barcode="A1"), driver=authed, session=db_session)
    assert v1.scanned_count == 1
    v2 = await scan_parcel(str(stop.id), ScanParcelBody(barcode="A2"), driver=authed, session=db_session)
    assert v2.scanned_count == 2


async def test_rescanning_the_same_barcode_is_idempotent(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcels=["A1", "A2"])
    authed, stop = await _arrived_pickup(db_session, hub_id, shop_id, order, parcel_count=2)

    await scan_parcel(str(stop.id), ScanParcelBody(barcode="A1"), driver=authed, session=db_session)
    again = await scan_parcel(str(stop.id), ScanParcelBody(barcode="A1"), driver=authed, session=db_session)
    assert again.scanned_count == 1  # not 2 - same parcel


async def test_an_unknown_barcode_is_rejected_as_wrong_part(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcels=["A1"])
    authed, stop = await _arrived_pickup(db_session, hub_id, shop_id, order, parcel_count=1)

    with pytest.raises(HTTPException) as exc:
        await scan_parcel(str(stop.id), ScanParcelBody(barcode="NOPE"), driver=authed, session=db_session)
    assert exc.value.status_code == 422


async def test_a_barcode_for_another_order_is_rejected_as_wrong_part(db_session, real_redis_client):
    # The whole point of W10: a parcel that belongs to a *different* order,
    # not on this pickup, is caught in the warehouse.
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    on_stop = await _ingest(db_session, hub_id, client_id, ref="ORD-ON", parcels=["ON-1"])
    other = await _ingest(db_session, hub_id, client_id, ref="ORD-OTHER", parcels=["OTHER-1"])
    authed, stop = await _arrived_pickup(db_session, hub_id, shop_id, on_stop, parcel_count=1)

    # OTHER-1 is a real parcel in this hub, but for an order not on this stop.
    with pytest.raises(HTTPException) as exc:
        await scan_parcel(str(stop.id), ScanParcelBody(barcode="OTHER-1"), driver=authed, session=db_session)
    assert exc.value.status_code == 422
    assert other is not None  # (exists, just not scannable here)


async def test_list_stop_parcels_shows_scan_progress(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    order = await _ingest(db_session, hub_id, client_id, parcels=["A1", "A2"])
    authed, stop = await _arrived_pickup(db_session, hub_id, shop_id, order, parcel_count=2)

    await scan_parcel(str(stop.id), ScanParcelBody(barcode="A1"), driver=authed, session=db_session)
    parcels = await list_stop_parcels(str(stop.id), driver=authed, session=db_session)
    by_barcode = {p.barcode: p.scanned for p in parcels}
    assert by_barcode == {"A1": True, "A2": False}


# --- pickup parcel_count reconciliation through the real accept flow ---

async def test_accept_offer_sets_pickup_parcel_count_from_real_parcels(db_session, real_redis_client):
    hub_id, client_id, shop_id = await _seed_hcs(db_session)
    driver_id = uuid.uuid4()
    db_session.add(Driver(id=driver_id, hub_id=hub_id, name="Accept D.", phone="+15555550701", vehicle_capacity_units=5))
    await db_session.commit()
    # Ingest an order with 3 parcels (ingest_order also puts it in the hold queue).
    await _ingest(db_session, hub_id, client_id, parcel_count=3)

    fleet = FleetStateManager()
    await fleet.upsert_driver_state(
        DriverState(driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5)
    )
    # The stub optimizer needs a driver location to assign against.
    await fleet.update_driver_location(
        DriverLocation(driver_id=str(driver_id), lat=34.055, lng=-118.245,
                       recorded_at=datetime.now(timezone.utc).isoformat()),
        str(hub_id),
    )

    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="d")
    await DispatchOptimizerService().run_cycle(str(hub_id))
    offers = await list_my_offers(driver=authed, session=db_session)
    route = await accept_offer(offers[0].offer_id, driver=authed, session=db_session)

    pickup = next(s for s in route.stops if s.stop_type == "pickup")
    assert pickup.parcel_count == 3  # real parcel count, not one-per-order
