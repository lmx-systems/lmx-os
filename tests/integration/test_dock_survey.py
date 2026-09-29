"""DRV-7: the writer `IDN-4`'s surveyed columns never had.

`app/identity/profile.py` has held everything `M5` needs to learn from since it
was written — `landing_surface`, `curb_access`, `door_path`, `obstruction`,
`who_receives`, plus the access facts — every one validated against a fixed
vocabulary, tested, and **written by nothing**. `set_access` and
`set_autonomy_fit` sat in `tests/test_no_new_orphans.py` as *"profile field with
no live writer"* until this endpoint existed, and the two entries come off the
allowlist in the same change.

What is tested here is the decision and the refusals, because those are where
being wrong is expensive: a survey shown at the wrong moment is paperwork a
driver learns to dismiss, and a value stored outside the vocabulary does not
fail at write time — it fails months later as a class the model has one example
of, indistinguishable from noise.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import record_dock_survey
from app.driver_auth.dependencies import AuthedDriver
from app.identity.dock_survey import (
    MAX_SURVEYS_PER_SHIFT,
    RESURVEY_AFTER_DAYS,
    dock_needs_survey,
)
from app.identity.profile import profile_for
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.order import Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder
from app.schemas.driver_app import DockSurveyBody

pytestmark = pytest.mark.integration


async def _world(db_session, *, linked=True):
    hub_id, client_id, shop_id, driver_id = (uuid.uuid4() for _ in range(4))
    db_session.add(Hub(id=hub_id, name="Survey Hub", lat=30.26, lng=-97.74))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()

    location = None
    if linked:
        location = Location(
            normalized_address=f"14quillonlane{uuid.uuid4().hex[:6]}",
            address="14 Quillon Lane",
            lat=30.26,
            lng=-97.74,
        )
        db_session.add(location)
        await db_session.commit()

    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Larkspur Panel",
            address="14 Quillon Lane",
            lat=30.26,
            lng=-97.74,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
            location_id=location.id if location else None,
        )
    )
    await db_session.commit()
    return hub_id, client_id, shop_id, driver_id, location


async def _dropoff(db_session, hub_id, client_id, shop_id, driver_id, status="completed"):
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=OrderStatus.delivered,
        requested_at=now,
        delivered_at=now,
        delivery_address="14 Quillon Lane",
        delivery_lat=30.26,
        delivery_lng=-97.74,
    )
    db_session.add(order)
    await db_session.commit()

    route = Route(hub_id=hub_id, driver_id=driver_id, status="active")
    db_session.add(route)
    await db_session.commit()
    stop = Stop(route_id=route.id, stop_type="dropoff", status=status, sequence=1)
    db_session.add(stop)
    await db_session.commit()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.commit()
    return stop


def _authed(driver_id, hub_id):
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="sim")


class TestWhenToAsk:
    async def test_an_unsurveyed_dock_is_asked_about(self, db_session):
        hub, client, shop, driver, _ = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        assert await dock_needs_survey(db_session, stop, driver) is True

    async def test_a_pickup_is_never_asked_about(self, db_session):
        # The dock being surveyed is the *receiver's*. A shop's own yard is not
        # it, and asking there would put a supplier's loading bay into M5's
        # labels as though it were a delivery address.
        hub, client, shop, driver, _ = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)
        stop.stop_type = "pickup"
        await db_session.commit()

        assert await dock_needs_survey(db_session, stop, driver) is False

    async def test_a_failed_stop_is_never_asked_about(self, db_session):
        # A driver who could not deliver may never have reached the door.
        hub, client, shop, driver, _ = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, status="failed")

        assert await dock_needs_survey(db_session, stop, driver) is False

    async def test_a_dock_that_is_not_a_dock_is_never_asked_about(self, db_session):
        # `IDN-1` leaves `location_id` null when an address names no place -
        # deliberately, because the alternative was every such address
        # collapsing into one shared fictional dock. Surveying that would put a
        # dozen unrelated businesses' answers on one row.
        hub, client, shop, driver, _ = await _world(db_session, linked=False)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        assert await dock_needs_survey(db_session, stop, driver) is False

    async def test_a_freshly_surveyed_dock_is_left_alone(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)
        await record_dock_survey(
            str(stop.id),
            DockSurveyBody(landing_surface="paved_lot"),
            driver=_authed(driver, hub),
            session=db_session,
        )

        assert await dock_needs_survey(db_session, stop, driver) is False

    async def test_a_dock_surveyed_over_a_year_ago_is_asked_again(self, db_session):
        # A door moves, a gate gets a code, a dock crew changes.
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)
        await record_dock_survey(
            str(stop.id),
            DockSurveyBody(landing_surface="paved_lot"),
            driver=_authed(driver, hub),
            session=db_session,
        )
        profile = await profile_for(db_session, location, create=False)
        profile.surveyed_at = datetime.now(timezone.utc) - timedelta(
            days=RESURVEY_AFTER_DAYS + 1
        )
        # Attributed to somebody else, or the per-shift cap below would be what
        # suppresses it rather than the interval.
        profile.surveyed_by_driver_id = None
        await db_session.commit()

        assert await dock_needs_survey(db_session, stop, driver) is True

    async def test_the_per_shift_cap_stops_asking(self, db_session):
        # A first week at a new customer would otherwise turn every stop into
        # paperwork, and a driver asked eight questions at twenty stops stops
        # answering carefully by the fourth.
        hub, client, shop, driver, _ = await _world(db_session)
        for _ in range(MAX_SURVEYS_PER_SHIFT):
            other = Location(
                normalized_address=f"other{uuid.uuid4().hex[:8]}",
                address="somewhere else",
                lat=30.2,
                lng=-97.7,
            )
            db_session.add(other)
            await db_session.commit()
            db_session.add(
                ReceiverProfile(
                    location_id=other.id,
                    landing_surface="paved_lot",
                    surveyed_at=datetime.now(timezone.utc),
                    surveyed_by_driver_id=driver,
                )
            )
            await db_session.commit()

        stop = await _dropoff(db_session, hub, client, shop, driver)

        assert await dock_needs_survey(db_session, stop, driver) is False

    async def test_another_drivers_surveys_do_not_count_against_this_one(self, db_session):
        hub, client, shop, driver, _ = await _world(db_session)
        # A real row: `surveyed_by_driver_id` is a foreign key, which is the
        # point of it - an unattributed judgement is not much better than no
        # judgement, so an id that names nobody must not be storable.
        somebody_else = uuid.uuid4()
        db_session.add(
            Driver(
                id=somebody_else,
                hub_id=hub,
                name="Another driver",
                phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
                vehicle_capacity_units=5,
            )
        )
        await db_session.commit()
        for _ in range(MAX_SURVEYS_PER_SHIFT):
            other = Location(
                normalized_address=f"other{uuid.uuid4().hex[:8]}",
                address="somewhere else",
                lat=30.2,
                lng=-97.7,
            )
            db_session.add(other)
            await db_session.commit()
            db_session.add(
                ReceiverProfile(
                    location_id=other.id,
                    landing_surface="paved_lot",
                    surveyed_at=datetime.now(timezone.utc),
                    surveyed_by_driver_id=somebody_else,
                )
            )
            await db_session.commit()

        stop = await _dropoff(db_session, hub, client, shop, driver)

        assert await dock_needs_survey(db_session, stop, driver) is True


class TestRecordingIt:
    async def test_it_writes_every_answer_to_the_dock(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        result = await record_dock_survey(
            str(stop.id),
            DockSurveyBody(
                stop_point="loading_dock",
                curb_access="direct",
                walk_distance_band="under_20m",
                door_path="ramp",
                obstruction="gate",
                who_receives="dock_crew",
                landing_surface="paved_lot",
                appointment_required=True,
            ),
            driver=_authed(driver, hub),
            session=db_session,
        )

        profile = await profile_for(db_session, location, create=False)
        assert result.is_surveyed is True
        assert profile.stop_point == "loading_dock"
        assert profile.curb_access == "direct"
        assert profile.walk_distance_band == "under_20m"
        assert profile.door_path == "ramp"
        assert profile.obstruction == "gate"
        assert profile.who_receives == "dock_crew"
        assert profile.landing_surface == "paved_lot"
        assert profile.appointment_required is True

    async def test_it_records_who_surveyed_it(self, db_session):
        # An unattributed judgement is not much better than no judgement: these
        # become M5's labels, and a bad surveyor has to be findable.
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        await record_dock_survey(
            str(stop.id),
            DockSurveyBody(landing_surface="grass"),
            driver=_authed(driver, hub),
            session=db_session,
        )

        profile = await profile_for(db_session, location, create=False)
        assert profile.surveyed_by_driver_id == driver

    async def test_an_empty_survey_is_accepted_and_records_nothing(self, db_session):
        # Every question has a skip, and skipping all of them is a valid
        # request. A measurement may fail; a delivery may not.
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        result = await record_dock_survey(
            str(stop.id), DockSurveyBody(), driver=_authed(driver, hub), session=db_session
        )

        profile = await profile_for(db_session, location, create=False)
        assert profile.landing_surface is None
        # `is_surveyed` means the autonomy columns are answered, not that
        # somebody pressed send.
        assert result.is_surveyed is False

    async def test_a_value_outside_the_vocabulary_is_refused(self, db_session):
        # It does not fail at write time - it fails months later as a class the
        # model has one example of, which is indistinguishable from noise.
        hub, client, shop, driver, _ = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        with pytest.raises(HTTPException) as exc:
            await record_dock_survey(
                str(stop.id),
                DockSurveyBody(landing_surface="helipad"),
                driver=_authed(driver, hub),
                session=db_session,
            )
        assert exc.value.status_code == 422
        assert "landing_surface" in str(exc.value.detail)

    async def test_a_stop_with_no_dock_is_a_409_not_a_silent_write(self, db_session):
        hub, client, shop, driver, _ = await _world(db_session, linked=False)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        with pytest.raises(HTTPException) as exc:
            await record_dock_survey(
                str(stop.id),
                DockSurveyBody(landing_surface="grass"),
                driver=_authed(driver, hub),
                session=db_session,
            )
        assert exc.value.status_code == 409

    async def test_sending_it_twice_does_not_make_a_second_profile(self, db_session):
        # The outbox retries. `profile_for` is keyed on the canonical dock, so a
        # replay overwrites rather than duplicating.
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)
        body = DockSurveyBody(landing_surface="gravel", door_path="steps")

        await record_dock_survey(
            str(stop.id), body, driver=_authed(driver, hub), session=db_session
        )
        await record_dock_survey(
            str(stop.id), body, driver=_authed(driver, hub), session=db_session
        )

        rows = (
            await db_session.execute(
                select(ReceiverProfile).where(ReceiverProfile.location_id == location.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_a_correction_at_the_same_door_wins(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        await record_dock_survey(
            str(stop.id),
            DockSurveyBody(door_path="steps"),
            driver=_authed(driver, hub),
            session=db_session,
        )
        await record_dock_survey(
            str(stop.id),
            DockSurveyBody(door_path="ramp"),
            driver=_authed(driver, hub),
            session=db_session,
        )

        profile = await profile_for(db_session, location, create=False)
        assert profile.door_path == "ramp"

    async def test_it_tells_the_app_how_many_more_it_will_ask_for(self, db_session):
        hub, client, shop, driver, _ = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver)

        result = await record_dock_survey(
            str(stop.id),
            DockSurveyBody(landing_surface="paved_lot"),
            driver=_authed(driver, hub),
            session=db_session,
        )

        assert result.surveys_remaining_today == MAX_SURVEYS_PER_SHIFT - 1
