"""DE-7: a gig job logged as a golden record.

A driver logs a delivery made for a gig platform: the job, the item at the
counter, the door at the drop-off. It becomes one record shaped like an LMX
delivery - a stand-in order, a pickup stop and a drop-off stop, `off_platform` -
fenced in an inactive hub so nothing that runs the business reads it, and graded
by `record_tiers` like any other drop-off.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.api.driver_routes import (
    arrive_at_stop,
    complete_stop,
    get_my_route,
    list_my_trips,
    record_dock_survey,
    record_handoff_photo_route,
)
from app.api.off_platform_routes import log_off_platform_delivery
from app.identity.node_class import is_a_served_dock
from app.models.driver import Driver
from app.models.gig_job import GigJob
from app.models.gig_payout import GigPayout
from app.models.handoff_point import HandoffPoint
from app.models.hub import Hub
from app.models.location import Location
from app.models.machine_label import MACHINES, MachineLabel
from app.models.order import INTAKE_BACKFILL, Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.stop import Stop
from app.record.labelling import labelling_queue
from app.record.off_platform import (
    PLACEHOLDER_RECORD_BONUS_CENTS,
    SOURCE_OFF_PLATFORM,
    off_platform_hub_name,
)
from app.reporting.operations import _deliveries_by_driver_day, _sla_hit_rates
from app.schemas.driver_app import ArriveBody, CompleteStopBody, DockSurveyBody, HandoffPhotoBody
from app.schemas.off_platform import OffPlatformRecordBody
from tests.integration.test_dock_survey import _authed

pytestmark = pytest.mark.integration

COUNTER_STOP_PIN = (30.43900, -97.62000)
COUNTER_PIN = (30.43905, -97.62000)
DOOR_STOP_PIN = (30.45000, -97.60000)
DOOR_PIN = (30.45010, -97.60000)  # about 11 m north


async def _driver(db_session):
    hub = Hub(id=uuid.uuid4(), name="Home Hub", timezone="America/Chicago", lat=30.4, lng=-97.6, state_code="TX")
    db_session.add(hub)
    await db_session.flush()
    driver = Driver(
        hub_id=hub.id, name="Driver G.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}", vehicle_capacity_units=5
    )
    db_session.add(driver)
    await db_session.commit()
    return hub, driver


def _job(**overrides):
    now = datetime.now(timezone.utc)
    tag = uuid.uuid4().hex[:8]
    job = dict(
        source_platform="curri",
        platform_job_ref=f"C-{tag}",
        pickup_address=f"{tag} Supply Way, Pflugerville, TX 78660",
        dropoff_address=f"{tag} Shop Ln, Pflugerville, TX 78660",
        pickup_window_open=(now - timedelta(hours=2)).isoformat(),
        pickup_window_close=(now - timedelta(hours=1)).isoformat(),
        dropoff_window_close=(now + timedelta(hours=1)).isoformat(),
        pay_cents=4200,
    )
    job.update(overrides)
    return job


ITEM = dict(item_description="brake rotors, boxed", item_weight_kg=11.5, item_size_class="medium", item_hazmat_or_liquid=False)


def _at_the_counter(**extra):
    return dict(
        stop_pin=dict(lat=COUNTER_STOP_PIN[0], lng=COUNTER_STOP_PIN[1]),
        door_pin=dict(lat=COUNTER_PIN[0], lng=COUNTER_PIN[1], accuracy_m=8),
        at=(datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(),
        **extra,
    )


def _at_the_door():
    return dict(
        stop_pin=dict(lat=DOOR_STOP_PIN[0], lng=DOOR_STOP_PIN[1]),
        door_pin=dict(lat=DOOR_PIN[0], lng=DOOR_PIN[1], accuracy_m=6),
        at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    )


async def _log(db_session, driver, record_id, **body):
    return await log_off_platform_delivery(
        record_id, OffPlatformRecordBody.model_validate(body), driver=_authed(driver.id, driver.hub_id), session=db_session
    )


async def _logged(db_session, driver=None, job=None):
    """A job logged the way the app sends it: the counter half, then the door."""
    if driver is None:
        _, driver = await _driver(db_session)
    record_id, pickup_id = uuid.uuid4(), uuid.uuid4()
    job = job or _job()
    await _log(db_session, driver, record_id, pickup_stop_id=pickup_id, job=job, item=ITEM, pickup=_at_the_counter())
    view = await _log(db_session, driver, record_id, pickup_stop_id=pickup_id, job=job, dropoff=_at_the_door())
    return driver, record_id, pickup_id, view


async def _capture(db_session, driver, stop_id, **survey):
    authed = _authed(driver.id, driver.hub_id)
    await record_dock_survey(
        str(stop_id),
        DockSurveyBody(
            stop_point="lot", door_path="ground_level", obstruction="none", landing_surface="paved_lot",
            handoff_type="counter", continuous_sidewalk=True, overhead=[], **survey,
        ),
        driver=authed, session=db_session,
    )
    for slot in ("approach", "stop_point", "path", "handoff"):
        await record_handoff_photo_route(
            str(stop_id), HandoffPhotoBody(slot=slot, url=f"handoff/{slot}.jpg"), driver=authed, session=db_session
        )


async def _site_known(db_session, stop_id, *, access=True):
    """The site facts no driver is asked for: kind and setting come from the
    desk (`DE-15`) or a person, receiving hours from the receiver."""
    stop = await db_session.get(Stop, stop_id)
    point = await db_session.get(HandoffPoint, stop.handoff_point_id)
    location = await db_session.get(Location, point.location_id)
    location.kind_of_site, location.setting, location.shares_address = "body_shop", "suburb", False
    if access:
        profile = (
            await db_session.scalars(select(ReceiverProfile).where(ReceiverProfile.location_id == location.id))
        ).one()
        profile.receiving_hours = {"mon": [["08:00", "17:00"]]}
        profile.who_receives = profile.who_receives or "counter_staff"
    await db_session.commit()


async def _scored(db_session, stop_id):
    stop = await db_session.get(Stop, stop_id)
    for machine in MACHINES:
        db_session.add(
            MachineLabel(
                stop_id=stop_id, handoff_point_id=stop.handoff_point_id, machine=machine, verdict="no",
                reason_codes=[], scorer_id="scorer-1", machine_limits_version="v1",
            )
        )
    await db_session.commit()


async def _tier(db_session, stop_id):
    return (
        await db_session.execute(text("SELECT * FROM record_tiers WHERE stop_id = :s"), {"s": stop_id})
    ).mappings().one()


class TestLoggingAJob:
    async def test_a_logged_job_is_one_record_shaped_like_a_delivery_and_fenced(self, db_session):
        driver, record_id, pickup_id, view = await _logged(db_session)

        assert (view.record_id, view.pickup_stop_id) == (str(record_id), str(pickup_id))
        assert view.record_bonus_cents == PLACEHOLDER_RECORD_BONUS_CENTS
        job = await db_session.get(GigJob, uuid.UUID(view.gig_job_id))
        assert (job.driver_id, job.status, job.record_bonus_cents) == (driver.id, "delivered", PLACEHOLDER_RECORD_BONUS_CENTS)
        assert job.delivered_at is not None

        order = await db_session.get(Order, job.record_order_id)
        assert (order.client_id, order.shop_id, order.source_system, order.intake_mode) == (
            None, None, SOURCE_OFF_PLATFORM, INTAKE_BACKFILL
        )
        assert (order.status, order.sla_tier) == (OrderStatus.delivered, None)
        assert (order.item_description, float(order.item_weight_kg), order.item_size_class) == (
            "brake rotors, boxed", 11.5, "medium"
        )
        assert order.delivery_window_end == job.dropoff_window_close
        assert order.delivery_location_id is not None

        hub = await db_session.get(Hub, order.hub_id)
        assert (hub.name, hub.active) == (off_platform_hub_name("TX"), False)
        pickup, dropoff = await db_session.get(Stop, pickup_id), await db_session.get(Stop, record_id)
        route = await db_session.get(Route, dropoff.route_id)
        assert (route.status, route.driver_id, route.hub_id) == ("record", driver.id, hub.id)
        for stop, kind in ((pickup, "pickup"), (dropoff, "dropoff")):
            assert (stop.stop_type, stop.status, stop.record_source, stop.collected_by) == (
                kind, "recorded", SOURCE_OFF_PLATFORM, str(driver.id)
            )
            assert stop.handoff_point_id is not None
        assert (float(dropoff.completed_lat), float(dropoff.completed_lng)) == DOOR_PIN
        assert (float(pickup.arrived_lat), float(pickup.arrived_lng)) == COUNTER_STOP_PIN
        # The sites are in the driver's region, for the test-site pools.
        door_site = await db_session.get(Location, order.delivery_location_id)
        assert door_site.region == "TX"

    async def test_sending_the_same_half_again_changes_nothing(self, db_session):
        driver, record_id, pickup_id, first = await _logged(db_session)
        job = await db_session.get(GigJob, uuid.UUID(first.gig_job_id))

        again = await _log(
            db_session, driver, record_id, pickup_stop_id=pickup_id, gig_job_id=str(job.id), dropoff=_at_the_door()
        )

        assert again.order_id == first.order_id
        assert await db_session.scalar(select(func.count()).select_from(Stop).where(Stop.route_id == (await db_session.get(Stop, record_id)).route_id)) == 2

    async def test_a_job_already_in_the_store_is_logged_by_its_reference(self, db_session):
        _, driver = await _driver(db_session)
        job = _job()
        record_id, pickup_id = uuid.uuid4(), uuid.uuid4()
        await _log(db_session, driver, record_id, pickup_stop_id=pickup_id, job=job)
        stored = (await db_session.scalars(select(GigJob).where(GigJob.platform_job_ref == job["platform_job_ref"]))).one()

        view = await _log(db_session, driver, record_id, pickup_stop_id=pickup_id, job=job, item=ITEM)

        assert view.gig_job_id == str(stored.id)
        assert await db_session.scalar(select(func.count()).select_from(GigJob).where(GigJob.platform_job_ref == job["platform_job_ref"])) == 1

    async def test_someone_elses_job_is_not_found(self, db_session):
        owner, _, _, view = await _logged(db_session)
        _, other = await _driver(db_session)

        with pytest.raises(HTTPException) as refused:
            await _log(db_session, other, uuid.uuid4(), pickup_stop_id=uuid.uuid4(), gig_job_id=view.gig_job_id)

        assert refused.value.status_code == 404

    async def test_a_job_logged_once_is_not_logged_as_a_second_record(self, db_session):
        driver, _, _, view = await _logged(db_session)

        with pytest.raises(HTTPException) as refused:
            await _log(db_session, driver, uuid.uuid4(), pickup_stop_id=uuid.uuid4(), gig_job_id=view.gig_job_id)

        assert refused.value.status_code == 409

    async def test_a_declined_job_was_not_delivered(self, db_session):
        hub, driver = await _driver(db_session)
        job = GigJob(
            hub_id=hub.id, driver_id=driver.id, source_platform="dispatch", platform_job_ref=f"D-{uuid.uuid4().hex[:8]}",
            pickup_address="1 Supply Way", dropoff_address="2 Shop Ln", pay_cents=1000, status="declined",
            pickup_window_open=datetime.now(timezone.utc), pickup_window_close=datetime.now(timezone.utc),
        )
        db_session.add(job)
        await db_session.commit()

        with pytest.raises(HTTPException) as refused:
            await _log(db_session, driver, uuid.uuid4(), pickup_stop_id=uuid.uuid4(), gig_job_id=str(job.id))

        assert refused.value.status_code == 409

    @pytest.mark.parametrize(
        "job, end",
        [
            (dict(dropoff_address=None), None),
            (dict(dropoff_address="N/A"), None),
            ({}, dict(at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())),
        ],
        ids=["no drop-off address", "a placeholder address", "a time ahead of our clock"],
    )
    async def test_what_cannot_be_a_record_is_refused(self, db_session, job, end):
        _, driver = await _driver(db_session)

        with pytest.raises(HTTPException) as refused:
            await _log(db_session, driver, uuid.uuid4(), pickup_stop_id=uuid.uuid4(), job=_job(**job), dropoff=end)

        assert refused.value.status_code == 422


class TestTheDoorAndTheCounter:
    async def test_the_door_is_captured_through_the_drop_offs_own_endpoints(self, db_session):
        driver, record_id, _, _ = await _logged(db_session)

        await _capture(db_session, driver, record_id, who_receives="counter_staff")

        stop = await db_session.get(Stop, record_id)
        point = await db_session.get(HandoffPoint, stop.handoff_point_id)
        await db_session.refresh(point)
        assert (float(point.stop_lat), float(point.stop_lng), float(point.door_lat), float(point.door_lng)) == (
            *DOOR_STOP_PIN, *DOOR_PIN
        )
        assert point.walk_distance_m == pytest.approx(11.1, abs=0.5)
        assert point.photo_handoff == "handoff/handoff.jpg"

    async def test_the_counter_is_filed_under_the_pickup_and_learns_no_access(self, db_session):
        driver, record_id, pickup_id, _ = await _logged(db_session)

        await _capture(db_session, driver, pickup_id, who_receives="counter_staff", appointment_required=True)

        pickup, dropoff = await db_session.get(Stop, pickup_id), await db_session.get(Stop, record_id)
        counter = await db_session.get(HandoffPoint, pickup.handoff_point_id)
        door = await db_session.get(HandoffPoint, dropoff.handoff_point_id)
        assert counter.location_id != door.location_id
        await db_session.refresh(counter)
        assert (float(counter.door_lat), float(counter.door_lng)) == COUNTER_PIN
        profile = (
            await db_session.scalars(select(ReceiverProfile).where(ReceiverProfile.location_id == counter.location_id))
        ).one()
        assert (profile.who_receives, profile.appointment_required) == (None, None)


class TestGrading:
    async def test_a_realistic_log_is_reference_and_scoring_makes_it_bronze(self, db_session):
        # The item, the deadline, both ends pinned and the door fully captured -
        # and still no site facts at a door nobody has desk-checked, so neither
        # end counts.
        driver, record_id, _, view = await _logged(db_session)
        await _capture(db_session, driver, record_id, who_receives="counter_staff")

        row = await _tier(db_session, record_id)
        assert (row["has_item"], row["has_delivery_profile"]) == (True, True)
        assert (row["has_pickup"], row["has_dropoff"], row["has_autonomy_qualifier"]) == (False, False, False)
        assert (row["tier"], row["record_source"]) == ("reference", SOURCE_OFF_PLATFORM)

        await _scored(db_session, record_id)
        assert (await _tier(db_session, record_id))["tier"] == "bronze"

    async def test_with_the_doors_site_known_a_scored_record_is_silver(self, db_session):
        driver, record_id, _, _ = await _logged(db_session)
        await _capture(db_session, driver, record_id, who_receives="counter_staff")
        await _site_known(db_session, record_id)

        assert (await _tier(db_session, record_id))["tier"] == "bronze"
        await _scored(db_session, record_id)
        row = await _tier(db_session, record_id)
        assert (row["tier"], row["has_dropoff"], row["has_pickup"]) == ("silver", True, False)

    async def test_with_the_counter_captured_too_it_is_gold(self, db_session):
        driver, record_id, pickup_id, _ = await _logged(db_session)
        await _capture(db_session, driver, record_id, who_receives="counter_staff")
        await _capture(db_session, driver, pickup_id)
        await _site_known(db_session, record_id)
        await _site_known(db_session, pickup_id)
        await _scored(db_session, record_id)

        row = await _tier(db_session, record_id)

        assert (row["tier"], row["elements_missing"]) == ("gold", 0)

    async def test_without_a_deadline_there_is_no_delivery_profile(self, db_session):
        _, record_id, _, _ = await _logged(db_session, job=_job(dropoff_window_close=None))

        row = await _tier(db_session, record_id)

        assert row["has_delivery_profile"] is False

    async def test_the_view_returns_the_grade(self, db_session):
        _, _, _, view = await _logged(db_session)

        assert (view.tier, view.elements_missing) == ("reference", 3)


class TestNothingThatRunsTheBusinessReadsIt:
    async def test_a_logged_stop_cannot_be_arrived_at_or_completed_and_pays_nothing(self, db_session, real_redis_client):
        driver, record_id, pickup_id, _ = await _logged(db_session)
        driver.employment_type = "gig"
        await db_session.commit()
        authed = _authed(driver.id, driver.hub_id)

        for call in (
            lambda: arrive_at_stop(str(record_id), ArriveBody(), driver=authed, session=db_session),
            lambda: complete_stop(str(record_id), CompleteStopBody(method="photo"), driver=authed, session=db_session),
            lambda: complete_stop(str(pickup_id), CompleteStopBody(method="photo"), driver=authed, session=db_session),
        ):
            with pytest.raises(HTTPException) as refused:
                await call()
            assert refused.value.status_code == 409
        assert await db_session.scalar(select(func.count()).select_from(GigPayout)) == 0

    async def test_not_the_drivers_route_or_a_trip(self, db_session):
        driver, _, _, _ = await _logged(db_session)
        authed = _authed(driver.id, driver.hub_id)

        assert await get_my_route(driver=authed, session=db_session) is None
        assert await list_my_trips(driver=authed, session=db_session) == []

    async def test_not_an_lmx_delivery_in_the_operating_figures(self, db_session):
        driver, _, _, _ = await _logged(db_session)
        since = datetime.now(timezone.utc) - timedelta(days=1)

        per_day = await _deliveries_by_driver_day(db_session, since)
        assert not any(driver_id == driver.id for driver_id, _ in per_day)
        # Left out, not counted as an order nobody can assess.
        (rate,) = await _sla_hit_rates(db_session, since)
        assert rate.not_measured == "no delivered orders in the window"

    async def test_the_door_is_not_a_dock_lmx_serves(self, db_session):
        _, _, _, view = await _logged(db_session)
        order = await db_session.get(Order, uuid.UUID(view.order_id))

        served = await db_session.scalar(
            select(Location.id).where(Location.id == order.delivery_location_id, is_a_served_dock())
        )

        assert served is None

    async def test_it_reaches_the_labelling_queue(self, db_session):
        _, record_id, _, _ = await _logged(db_session)

        queued = await labelling_queue(db_session, scorer_id="scorer-9", region="TX", limit=50)

        assert str(record_id) in {str(row["stop_id"]) for row in queued}
