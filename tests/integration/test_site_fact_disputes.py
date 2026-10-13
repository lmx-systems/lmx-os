"""DE-15, the #225 review: one answer per stop, only from a finished door, and
somebody settles the answers that disagree.

`app/identity/site_facts.py`. The three findings: a retry after a lost response
stored a second dispute; any stop of the driver's - pending, cancelled, a
distributor's counter - could answer about a site, and a pickup fell through to
whichever delivery door its first order named; and the disputes were written
for a person to settle with nothing that showed them to anyone.
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import record_dock_survey, record_stop_site_facts
from app.api.routes import attention_counts, settle_site_fact_dispute, site_fact_disputes
from app.identity.dock_survey import location_for_stop
from app.identity.site_facts import (
    MAX_DISPUTES,
    SOURCE_OPS,
    SOURCE_SURVEY,
    NothingToSettle,
    record_site_facts,
    settle_dispute,
)
from app.models.location import Location
from app.models.ops_user import DISPATCHER_ROLE
from app.models.stop import Stop, StopOrder
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.driver_app import DockSurveyBody, SiteFactsBody
from app.schemas.reporting import SettleSiteFactBody
from tests.integration.test_dock_survey import _authed, _dropoff, _world

pytestmark = pytest.mark.integration

OPS = AuthedOpsUser(
    ops_user_id=str(uuid.uuid4()), email="dispatcher@example.com", name="Dispatcher", role=DISPATCHER_ROLE
)


async def _a_settled_setting(db_session):
    """A finished drop-off at a site whose setting a person already gave."""
    hub, client, shop, driver, location = await _world(db_session)
    location.setting = "suburb"
    location.site_fact_sources = {"setting": "import"}
    await db_session.commit()
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
    return stop, location, _authed(driver, hub), hub


class TestOneAnswerPerStop:
    async def test_a_retry_after_a_lost_response_leaves_one_dispute(self, db_session):
        stop, location, authed, _ = await _a_settled_setting(db_session)

        for _ in range(2):
            await record_stop_site_facts(
                str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session
            )

        await db_session.refresh(location)
        assert location.setting == "suburb"
        assert [d["value"] for d in location.site_fact_answers["setting"]["disputes"]] == ["city_core"]

    async def test_a_retry_of_an_applied_answer_changes_nothing(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        authed = _authed(driver, hub)

        for _ in range(2):
            answered = await record_stop_site_facts(
                str(stop.id), SiteFactsBody(setting="suburb"), driver=authed, session=db_session
            )

        assert answered.disputed == []
        await db_session.refresh(location)
        entry = location.site_fact_answers["setting"]
        assert (location.setting, entry["value"], entry["disputes"]) == ("suburb", "suburb", [])

    async def test_a_survey_replay_repeats_its_facts_without_a_second_dispute(self, db_session):
        stop, location, authed, _ = await _a_settled_setting(db_session)

        for _ in range(2):
            await record_dock_survey(
                str(stop.id), DockSurveyBody(landing_surface="paved_lot", setting="city_core"),
                driver=authed, session=db_session,
            )

        await db_session.refresh(location)
        assert len(location.site_fact_answers["setting"]["disputes"]) == 1

    async def test_a_different_second_answer_from_the_same_stop_is_refused(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        authed = _authed(driver, hub)
        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="suburb"), driver=authed, session=db_session)

        with pytest.raises(HTTPException) as refused:
            await record_stop_site_facts(
                str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session
            )

        assert refused.value.status_code == 409
        await db_session.refresh(location)
        assert location.setting == "suburb"

    async def test_the_survey_still_saves_when_its_facts_contradict_the_stops_earlier_answer(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        authed = _authed(driver, hub)
        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="suburb"), driver=authed, session=db_session)

        result = await record_dock_survey(
            str(stop.id), DockSurveyBody(landing_surface="paved_lot", setting="city_core"),
            driver=authed, session=db_session,
        )

        assert result.site_facts_disputed == []
        await db_session.refresh(location)
        assert location.setting == "suburb"

    async def test_a_facts_disputes_are_capped_oldest_dropped(self, db_session):
        stop, location, _, _ = await _a_settled_setting(db_session)

        for _ in range(MAX_DISPUTES + 3):
            await record_site_facts(db_session, location, setting="city_core", by=uuid.uuid4(), stop_id=uuid.uuid4())
        await db_session.commit()

        await db_session.refresh(location)
        assert len(location.site_fact_answers["setting"]["disputes"]) == MAX_DISPUTES


class TestOnlyAFinishedDoorAnswers:
    @pytest.mark.parametrize("status", ["pending", "cancelled", "failed"])
    async def test_an_unfinished_stop_is_a_409_and_writes_nothing(self, db_session, status):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, status=status, dock=location)

        with pytest.raises(HTTPException) as refused:
            await record_stop_site_facts(
                str(stop.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session
            )

        assert refused.value.status_code == 409
        await db_session.refresh(location)
        assert (location.setting, location.site_fact_answers) == (None, None)

    async def test_a_pickup_carrying_two_orders_names_no_door_and_is_refused(self, db_session):
        """The review's scenario: a pickup fell through to `Order.delivery_location_id
        ... LIMIT 1`, filing the counter's answer under whichever door came first."""
        hub, client, shop, driver, door_a = await _world(db_session)
        door_b = Location(normalized_address=f"9birchrow{uuid.uuid4().hex[:6]}", address="9 Birch Row")
        db_session.add(door_b)
        await db_session.commit()
        first = await _dropoff(db_session, hub, client, shop, driver, dock=door_a)
        second = await _dropoff(db_session, hub, client, shop, driver, dock=door_b)
        pickup = Stop(route_id=first.route_id, stop_type="pickup", status="completed", sequence=0)
        db_session.add(pickup)
        await db_session.flush()
        for dropoff in (first, second):
            order_id = await db_session.scalar(select(StopOrder.order_id).where(StopOrder.stop_id == dropoff.id))
            db_session.add(StopOrder(stop_id=pickup.id, order_id=order_id))
        await db_session.commit()

        assert await location_for_stop(db_session, pickup) is None
        with pytest.raises(HTTPException) as refused:
            await record_stop_site_facts(
                str(pickup.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session
            )

        assert refused.value.status_code == 409
        for door in (door_a, door_b):
            await db_session.refresh(door)
            assert door.setting is None

    async def test_a_survey_at_an_unfinished_stop_writes_no_facts(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, status="pending", dock=location)

        await record_dock_survey(
            str(stop.id), DockSurveyBody(landing_surface="paved_lot", setting="suburb"),
            driver=_authed(driver, hub), session=db_session,
        )

        await db_session.refresh(location)
        assert location.setting is None


class TestAPersonSettlesThem:
    async def test_the_console_lists_them_counts_them_and_keeping_settles_them(self, db_session):
        stop, location, authed, hub = await _a_settled_setting(db_session)
        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)

        listed = await site_fact_disputes(limit=50, session=db_session, _ops=OPS)
        (dispute,) = [d for d in listed if d.location_id == str(location.id)]
        assert (dispute.fact, dispute.current, dispute.current_source) == ("setting", "suburb", "import")
        assert [(a.value, a.driver_name) for a in dispute.answers] == [("city_core", "Sam O.")]
        counts = await attention_counts(hub_id=hub, session=db_session, _ops=OPS)
        assert counts.site_fact_disputes >= 1

        await settle_site_fact_dispute(
            location.id, "setting", SettleSiteFactBody(), session=db_session, ops=OPS
        )

        await db_session.refresh(location)
        assert (location.setting, location.site_fact_sources["setting"]) == ("suburb", SOURCE_OPS)
        entry = location.site_fact_answers["setting"]
        assert entry["disputes"] == []
        (settled,) = entry["settled"]
        assert (settled["by"], settled["kept"], len(settled["disputes"])) == (OPS.ops_user_id, True, 1)
        listed = await site_fact_disputes(limit=50, session=db_session, _ops=OPS)
        assert all(d.location_id != str(location.id) for d in listed)

    async def test_taking_the_disputed_value_applies_it_and_a_late_retry_stays_settled(self, db_session):
        stop, location, authed, _ = await _a_settled_setting(db_session)
        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)

        await settle_site_fact_dispute(
            location.id, "setting", SettleSiteFactBody(take="city_core"), session=db_session, ops=OPS
        )
        # The phone's retry of that answer, arriving after it was settled.
        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)

        await db_session.refresh(location)
        assert (location.setting, location.site_fact_sources["setting"]) == ("city_core", SOURCE_OPS)
        assert location.site_fact_answers["setting"]["disputes"] == []

    async def test_a_value_nobody_gave_or_nothing_to_settle_is_refused(self, db_session):
        stop, location, authed, _ = await _a_settled_setting(db_session)

        with pytest.raises(HTTPException) as nothing:
            await settle_site_fact_dispute(location.id, "setting", SettleSiteFactBody(), session=db_session, ops=OPS)
        assert nothing.value.status_code == 409

        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)
        with pytest.raises(NothingToSettle):
            await settle_dispute(db_session, location, "setting", take="suburb_fringe", by=uuid.uuid4())

    async def test_an_applied_survey_answer_is_still_survey_sourced(self, db_session):
        hub, client, shop, driver, location = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)

        await record_stop_site_facts(
            str(stop.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session
        )

        await db_session.refresh(location)
        assert location.site_fact_sources["setting"] == SOURCE_SURVEY
