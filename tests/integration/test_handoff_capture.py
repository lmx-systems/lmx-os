"""DE-4: the dock survey becomes the golden handoff capture at the delivery door.

Pins are taken, never typed: the stop pin at the Arrived tap or the geofence
entry, the door pin at Delivered. The survey writes the dock's handoff point
from its answers and those pins, and is asked at every drop-off until the door
is captured.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from datetime import datetime, timezone

from app.api.driver_routes import (
    arrive_at_stop,
    complete_stop,
    record_dock_survey,
    record_geofence_events,
    record_handoff_photo_route,
)
from app.identity.dock_survey import MAX_SURVEYS_PER_SHIFT, dock_needs_survey
from app.identity.handoff_capture import is_captured, walk_metres
from app.models.handoff_point import HandoffPoint
from app.models.receiver_profile import ReceiverProfile
from app.models.location import Location
from app.schemas.driver_app import (
    ArriveBody,
    CompleteStopBody,
    DockSurveyBody,
    HandoffPhotoBody,
    StopGeofenceEventBody,
    StopGeofenceEventsBody,
)
from tests.integration.test_dock_survey import _authed, _dropoff, _world

# Every test takes `real_redis_client`: arriving and completing a stop reach
# Redis through the app's shared pool, and the fixture is what resets that pool
# between tests. Without it the pool outlives this test's event loop and the next
# test that uses the fixture fails at setup with "Event loop is closed".
pytestmark = pytest.mark.integration

STOP_PIN = (30.26000, -97.74000)
DOOR_PIN = (30.26010, -97.74000)  # about 11 m north


async def _pinned_dropoff(db_session):
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location, status="arrived")
    stop.arrived_lat, stop.arrived_lng = STOP_PIN
    stop.completed_lat, stop.completed_lng = DOOR_PIN
    await db_session.commit()
    return hub, driver, location, stop


async def test_the_stop_pin_is_the_first_arrival_and_is_never_moved(db_session, real_redis_client):
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location, status="en_route")

    await arrive_at_stop(str(stop.id), ArriveBody(lat=30.1, lng=-97.1, accuracy_m=6), driver=_authed(driver, hub), session=db_session)
    await arrive_at_stop(str(stop.id), ArriveBody(lat=31.0, lng=-98.0), driver=_authed(driver, hub), session=db_session)

    await db_session.refresh(stop)
    assert (stop.arrived_lat, stop.arrived_lng, stop.arrived_accuracy_m) == (30.1, -97.1, 6)


async def test_an_arrival_with_no_position_still_arrives(db_session, real_redis_client):
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location, status="en_route")

    view = await arrive_at_stop(str(stop.id), None, driver=_authed(driver, hub), session=db_session)

    assert view.status == "arrived"


async def test_a_geofence_entry_pins_the_stop_when_the_tap_did_not(db_session, real_redis_client):
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location, status="en_route")
    now = datetime.now(timezone.utc)

    await record_geofence_events(
        str(stop.id),
        StopGeofenceEventsBody(events=[StopGeofenceEventBody(kind="enter", occurred_at=now, lat=30.2, lng=-97.2, accuracy_m=9)]),
        driver=_authed(driver, hub), session=db_session,
    )

    await db_session.refresh(stop)
    assert (stop.arrived_lat, stop.arrived_lng) == (30.2, -97.2)


async def test_the_door_pin_is_taken_when_the_stop_completes(db_session, real_redis_client):
    hub, client, shop, driver, location = await _world(db_session)
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location, status="arrived")
    stop.stop_type, stop.parcel_count, stop.scanned_count = "pickup", 1, 1
    await db_session.commit()

    await complete_stop(
        str(stop.id), CompleteStopBody(method="photo", door_lat=30.3, door_lng=-97.3, door_accuracy_m=4),
        driver=_authed(driver, hub), session=db_session,
    )

    await db_session.refresh(stop)
    assert (stop.completed_lat, stop.completed_lng, stop.completed_accuracy_m) == (30.3, -97.3, 4)


async def test_the_survey_writes_the_handoff_point_from_answers_and_pins(db_session, real_redis_client):
    hub, driver, location, stop = await _pinned_dropoff(db_session)

    await record_dock_survey(
        str(stop.id),
        DockSurveyBody(
            stop_point="lot", door_path="ground_level", obstruction="none", landing_surface="grass",
            handoff_type="counter", continuous_sidewalk=True, overhead=["wires"],
        ),
        driver=_authed(driver, hub), session=db_session,
    )

    point = (await db_session.scalars(select(HandoffPoint).where(HandoffPoint.location_id == location.id))).one()
    assert (point.stop_type, point.stop_legal, point.steps_or_ramps) == ("lot", True, "none")
    assert (point.obstructions, point.drone_open_ground, point.overhead) == ([], True, ["wires"])
    assert (point.handoff_type, point.continuous_sidewalk) == ("counter", True)
    assert (point.stop_lat, point.stop_lng, point.door_lat, point.door_lng) == (*STOP_PIN, *DOOR_PIN)
    assert point.walk_distance_m == pytest.approx(walk_metres(*STOP_PIN, *DOOR_PIN), abs=0.1)
    assert 10 < point.walk_distance_m < 12
    assert (point.source, point.collected_by) == ("lmx_app", str(driver))
    await db_session.refresh(stop)
    assert stop.handoff_point_id == point.id


async def test_an_explicit_answer_wins_and_an_unmappable_one_stays_unknown(db_session, real_redis_client):
    hub, driver, location, stop = await _pinned_dropoff(db_session)

    await record_dock_survey(
        str(stop.id),
        # The survey says a lot; the door question says the van stood at the curb.
        # "freight_lift" says how the item goes in, not whether there are steps.
        DockSurveyBody(stop_point="lot", handoff_stop_type="curb", door_path="freight_lift"),
        driver=_authed(driver, hub), session=db_session,
    )

    point = (await db_session.scalars(select(HandoffPoint).where(HandoffPoint.location_id == location.id))).one()
    assert point.stop_type == "curb"
    assert point.steps_or_ramps is None


async def test_an_answer_outside_the_vocabulary_is_refused(db_session, real_redis_client):
    hub, driver, location, stop = await _pinned_dropoff(db_session)

    with pytest.raises(HTTPException) as refused:
        await record_dock_survey(
            str(stop.id), DockSurveyBody(obstructions=["moat"]), driver=_authed(driver, hub), session=db_session
        )
    assert refused.value.status_code == 422


async def test_the_four_photos_complete_the_capture(db_session, real_redis_client):
    hub, driver, location, stop = await _pinned_dropoff(db_session)
    await record_dock_survey(
        str(stop.id),
        DockSurveyBody(
            stop_point="lot", door_path="ground_level", obstruction="none", landing_surface="grass",
            handoff_type="counter", continuous_sidewalk=True, overhead=[],
        ),
        driver=_authed(driver, hub), session=db_session,
    )
    point = (await db_session.scalars(select(HandoffPoint).where(HandoffPoint.location_id == location.id))).one()
    assert not is_captured(point)

    for slot in ("approach", "stop_point", "path", "handoff"):
        await record_handoff_photo_route(
            str(stop.id), HandoffPhotoBody(slot=slot, url=f"handoff/{slot}.jpg"),
            driver=_authed(driver, hub), session=db_session,
        )

    await db_session.refresh(point)
    assert is_captured(point)


async def test_an_uncaptured_door_is_asked_about_past_the_shift_cap(db_session, real_redis_client):
    hub, client, shop, driver, location = await _world(db_session)
    for _ in range(MAX_SURVEYS_PER_SHIFT):
        other = Location(normalized_address=f"o{uuid.uuid4().hex}", address="elsewhere", lat=30.2, lng=-97.7)
        db_session.add(other)
        await db_session.commit()
        db_session.add(
            ReceiverProfile(location_id=other.id, landing_surface="paved_lot", surveyed_at=datetime.now(timezone.utc), surveyed_by_driver_id=driver)
        )
        await db_session.commit()
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)

    assert await dock_needs_survey(db_session, stop, driver) is True
