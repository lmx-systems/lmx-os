"""DE-6: a technician's re-drive day - visits to sites, with no delivery.

A visit is planned onto an active route in the region's inactive re-drive hub,
runs in the app as arrive, capture the door, done - the same golden capture as a
drop-off, with access recorded as unknown - and is graded on its door (D-VISIT):
the drop-off end and the qualifier is gold, the end alone silver, no end
reference.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.api.driver_routes import (
    arrive_at_stop,
    complete_stop,
    get_my_route,
    record_dock_survey,
    record_handoff_photo_route,
)
from app.identity.dock_survey import dock_needs_survey
from app.identity.handoff_capture import is_captured
from app.models.driver import Driver
from app.models.handoff_point import HandoffPoint
from app.models.hub import Hub
from app.models.location import Location
from app.models.machine_label import MACHINES, MachineLabel
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.stop import Stop
from app.record.labelling import labelling_queue
from app.record.visits import (
    SOURCE_REDRIVE,
    VisitDayRefused,
    plan_visit_day,
    redrive_hub_name,
    sites_to_visit,
)
from app.schemas.driver_app import ArriveBody, CompleteStopBody, DockSurveyBody, HandoffPhotoBody
from tests.integration.test_dock_survey import _authed

# Tests that arrive at or complete a stop take `real_redis_client`: both reach
# Redis through the app's shared pool, and the fixture resets it between tests.
pytestmark = pytest.mark.integration

STOP_PIN = (40.73000, -74.17000)
DOOR_PIN = (40.73010, -74.17000)  # about 11 m north


async def _technician(db_session):
    hub = Hub(id=uuid.uuid4(), name="Home Hub", timezone="America/New_York", lat=40.7, lng=-74.1)
    db_session.add(hub)
    await db_session.flush()
    driver = Driver(
        hub_id=hub.id, name="Tech A.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}", vehicle_capacity_units=5
    )
    db_session.add(driver)
    await db_session.commit()
    return hub, driver


async def _site(db_session, *, region="NJ", deliveries=1, setting="suburb"):
    """A site we have delivered to, as the Experiment 0 import leaves one: a
    location with an empty handoff point that `deliveries` drop-offs used."""
    tag = uuid.uuid4().hex[:8]
    location = Location(
        normalized_address=f"{tag}millrd", address=f"{tag} Mill Rd", lat=40.73, lng=-74.17,
        region=region, kind_of_site="body_shop", setting=setting, shares_address=False,
        site_source="import",
    )
    db_session.add(location)
    await db_session.flush()
    point = HandoffPoint(location_id=location.id, source="import")
    db_session.add(point)
    await db_session.flush()
    if deliveries:
        hub = Hub(id=uuid.uuid4(), name="Import", lat=0.0, lng=0.0, active=False)
        db_session.add(hub)
        await db_session.flush()
        driver = Driver(hub_id=hub.id, name="import", phone=f"import:{tag}", is_active=False)
        db_session.add(driver)
        await db_session.flush()
        route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        db_session.add(route)
        await db_session.flush()
        for n in range(deliveries):
            db_session.add(
                Stop(
                    route_id=route.id, stop_type="dropoff", sequence=n + 1, status="completed",
                    handoff_point_id=point.id, record_source="import_route_export",
                )
            )
    await db_session.commit()
    return location


async def _tier(db_session, stop_id):
    return (
        await db_session.execute(text("SELECT * FROM record_tiers WHERE stop_id = :s"), {"s": stop_id})
    ).mappings().one()


async def _a_day(db_session, *sites, driver=None):
    hub, tech = (None, driver) if driver is not None else await _technician(db_session)
    day = await plan_visit_day(
        db_session, driver_id=tech.id, region="NJ", location_ids=[s.id for s in sites]
    )
    await db_session.commit()
    return tech, day


class TestPlanningADay:
    async def test_a_day_is_an_active_route_of_visits_in_the_regions_inactive_hub(self, db_session):
        first, second = await _site(db_session), await _site(db_session)

        tech, day = await _a_day(db_session, first, second)

        route = await db_session.get(Route, day.route_id)
        hub = await db_session.get(Hub, day.hub_id)
        assert (route.status, route.driver_id) == ("active", tech.id)
        assert (hub.name, hub.active) == (redrive_hub_name("NJ"), False)
        stops = (await db_session.scalars(select(Stop).where(Stop.route_id == route.id).order_by(Stop.sequence))).all()
        assert [s.stop_type for s in stops] == ["visit", "visit"]
        assert {(s.record_source, s.collected_by, s.parcel_count) for s in stops} == {
            (SOURCE_REDRIVE, str(tech.id), 0)
        }
        # Each visit is linked to its site's existing handoff point - the door
        # the deliveries used - not a new one.
        points = [
            (await db_session.scalars(select(HandoffPoint.id).where(HandoffPoint.location_id == site.id))).one()
            for site in (first, second)
        ]
        assert [s.handoff_point_id for s in stops] == points

    async def test_the_app_shows_the_day_as_the_drivers_route(self, db_session):
        site = await _site(db_session)
        tech, day = await _a_day(db_session, site)

        route = await get_my_route(driver=_authed(tech.id, tech.hub_id), session=db_session)

        assert route is not None and route.route_id == str(day.route_id)
        (visit,) = route.stops
        assert (visit.stop_type, visit.address, visit.order_ids) == ("visit", site.address, [])
        assert (visit.lat, visit.lng) == (site.lat, site.lng)
        assert visit.dock_needs_survey is True

    async def test_a_second_day_in_the_region_uses_the_same_hub(self, db_session):
        _, first = await _a_day(db_session, await _site(db_session))
        _, second = await _a_day(db_session, await _site(db_session))

        assert first.hub_id == second.hub_id

    async def test_a_driver_holding_work_is_refused_a_day(self, db_session):
        tech, _ = await _a_day(db_session, await _site(db_session))

        with pytest.raises(VisitDayRefused, match="holds a route"):
            await plan_visit_day(db_session, driver_id=tech.id, region="NJ", location_ids=[(await _site(db_session)).id])

    async def test_a_site_outside_the_region_or_listed_twice_is_refused(self, db_session):
        _, tech = await _technician(db_session)
        texas, nj = await _site(db_session, region="TX"), await _site(db_session)

        with pytest.raises(VisitDayRefused, match="not in NJ"):
            await plan_visit_day(db_session, driver_id=tech.id, region="NJ", location_ids=[texas.id])
        with pytest.raises(VisitDayRefused, match="twice"):
            await plan_visit_day(db_session, driver_id=tech.id, region="NJ", location_ids=[nj.id, nj.id])

    async def test_a_switched_off_driver_is_refused(self, db_session):
        _, tech = await _technician(db_session)
        tech.is_active = False
        await db_session.commit()

        with pytest.raises(VisitDayRefused, match="switched off"):
            await plan_visit_day(db_session, driver_id=tech.id, region="NJ", location_ids=[(await _site(db_session)).id])

    async def test_the_next_sites_are_the_most_delivered_to_not_yet_planned(self, db_session):
        busy, quiet = await _site(db_session, deliveries=3), await _site(db_session, deliveries=1)
        never_delivered = await _site(db_session, deliveries=0)
        elsewhere = await _site(db_session, region="TX", deliveries=5)

        assert await sites_to_visit(db_session, "NJ", 10) == [busy.id, quiet.id]
        await _a_day(db_session, busy)
        assert await sites_to_visit(db_session, "NJ", 10) == [quiet.id]
        assert never_delivered.id not in await sites_to_visit(db_session, "NJ", 10)
        assert elsewhere.id not in await sites_to_visit(db_session, "NJ", 10)


class TestRunningAVisit:
    async def test_arrive_capture_the_door_and_done(self, db_session, real_redis_client):
        site = await _site(db_session)
        tech, day = await _a_day(db_session, site)
        (stop_id,) = day.stop_ids
        authed = _authed(tech.id, tech.hub_id)

        await arrive_at_stop(str(stop_id), ArriveBody(lat=STOP_PIN[0], lng=STOP_PIN[1]), driver=authed, session=db_session)
        # Nothing to hand over, so no proof: the completion is the door pin.
        view = await complete_stop(
            str(stop_id), CompleteStopBody(method="photo", door_lat=DOOR_PIN[0], door_lng=DOOR_PIN[1]),
            driver=authed, session=db_session,
        )
        assert view.status == "completed"

        stop = await db_session.get(Stop, stop_id)
        assert await dock_needs_survey(db_session, stop, tech.id) is True
        # Access is unknown at a visit: who receives and a booking are not
        # written from one, even when sent.
        await record_dock_survey(
            str(stop_id),
            DockSurveyBody(
                stop_point="lot", door_path="ground_level", obstruction="none", landing_surface="paved_lot",
                handoff_type="roll_up_bay", continuous_sidewalk=True, overhead=[],
                who_receives="counter_staff", appointment_required=True,
            ),
            driver=authed, session=db_session,
        )
        for slot in ("approach", "stop_point", "path", "handoff"):
            await record_handoff_photo_route(
                str(stop_id), HandoffPhotoBody(slot=slot, url=f"handoff/{slot}.jpg"), driver=authed, session=db_session
            )

        point = await db_session.get(HandoffPoint, stop.handoff_point_id)
        await db_session.refresh(point)
        assert is_captured(point)
        assert (point.stop_lat, point.stop_lng, point.door_lat, point.door_lng) == (*STOP_PIN, *DOOR_PIN)
        profile = (
            await db_session.scalars(select(ReceiverProfile).where(ReceiverProfile.location_id == site.id))
        ).one()
        assert (profile.who_receives, profile.appointment_required) == (None, None)
        route = await db_session.get(Route, day.route_id)
        await db_session.refresh(route)
        assert route.status == "completed"
        # No ETA: nobody is waiting for a visit.
        await db_session.refresh(stop)
        assert (stop.eta, stop.planned_eta) == (None, None)
        # Captured, with the site's place facts known: silver, waiting for a scorer.
        assert (await _tier(db_session, stop_id))["tier"] == "silver"


class TestGradingAVisit:
    """D-VISIT: graded on the drop-off end and the qualifier only."""

    async def _captured_visit(self, db_session, **site):
        location = await _site(db_session, **site)
        _, day = await _a_day(db_session, location)
        (stop_id,) = day.stop_ids
        point = (await db_session.scalars(select(HandoffPoint).where(HandoffPoint.location_id == location.id))).one()
        for field, value in dict(
            handoff_type="counter", door_lat=DOOR_PIN[0], door_lng=DOOR_PIN[1], stop_lat=STOP_PIN[0],
            stop_lng=STOP_PIN[1], stop_type="lot", stop_legal=True, walk_distance_m=11.0,
            steps_or_ramps="none", continuous_sidewalk=True, obstructions=[], drone_open_ground=True,
            overhead=[], photo_approach="a.jpg", photo_stop_point="b.jpg", photo_path="c.jpg",
            photo_handoff="d.jpg",
        ).items():
            setattr(point, field, value)
        stop = await db_session.get(Stop, stop_id)
        stop.status, stop.completed_at = "completed", datetime.now(timezone.utc) - timedelta(minutes=5)
        await db_session.commit()
        return stop_id, point

    async def test_a_visit_that_captured_nothing_is_reference(self, db_session):
        _, day = await _a_day(db_session, await _site(db_session))

        row = await _tier(db_session, day.stop_ids[0])

        assert row["tier"] == "reference"
        assert row["has_dropoff"] is False
        # Not applicable, not missing.
        assert (row["has_pickup"], row["has_item"], row["has_delivery_profile"]) == (None, None, None)
        assert row["elements_missing"] == 2

    async def test_a_captured_door_is_silver_without_the_access_facts(self, db_session):
        # No receiving hours and nobody known to receive - a delivery's drop-off
        # end would be missing; a visit's is not.
        stop_id, _ = await self._captured_visit(db_session)

        row = await _tier(db_session, stop_id)

        assert (row["tier"], row["has_dropoff"], row["elements_missing"]) == ("silver", True, 1)
        assert row["record_source"] == SOURCE_REDRIVE

    async def test_a_scored_captured_door_is_gold(self, db_session):
        stop_id, point = await self._captured_visit(db_session)
        for machine in MACHINES:
            db_session.add(
                MachineLabel(
                    stop_id=stop_id, handoff_point_id=point.id, machine=machine, verdict="yes",
                    reason_codes=[], scorer_id="scorer-1", machine_limits_version="v1",
                )
            )
        await db_session.commit()

        row = await _tier(db_session, stop_id)

        assert (row["tier"], row["elements_missing"]) == ("gold", 0)

    async def test_a_door_at_a_site_whose_setting_is_unknown_is_reference(self, db_session):
        # The site's place facts come from the desk before a visit; without them
        # the end is not captured, however good the door.
        stop_id, _ = await self._captured_visit(db_session, setting=None)

        assert (await _tier(db_session, stop_id))["tier"] == "reference"

    async def test_a_visit_reaches_the_labelling_queue(self, db_session):
        stop_id, _ = await self._captured_visit(db_session)

        queued = await labelling_queue(db_session, scorer_id="scorer-9", region="NJ", limit=50)

        assert str(stop_id) in {str(row["stop_id"]) for row in queued}
