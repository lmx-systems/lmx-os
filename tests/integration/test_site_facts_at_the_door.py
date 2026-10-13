"""DE-15 at the door: the survey asks the site facts still open, and a person's
answer - a confirmation included - lifts the desk's cap on the grade.

`app/identity/site_facts.py`. The path that matters end to end: a site the desk
filled from public data, a technician's visit that captures its door (bronze,
because the place facts are only the desk's), the same visit confirming them
(silver), and the three machines scored (gold).
"""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.driver_routes import (
    arrive_at_stop,
    complete_stop,
    get_my_route,
    get_stop_site_facts,
    record_dock_survey,
    record_handoff_photo_route,
    record_stop_site_facts,
)
from app.identity.site_facts import (
    SOURCE_SURVEY,
    SiteFactRefused,
    facts_to_ask,
    record_site_facts,
    set_site_fact,
)
from app.models.handoff_point import HandoffPoint
from app.models.location import Location
from app.models.machine_label import MACHINES, MachineLabel
from app.models.site_address_share import SiteAddressShare
from app.models.site_draw import SiteDraw
from app.record.desk_prefill import DESK, SiteEvidence, apply_desk_facts
from app.record.site_draws import draw_test_sites
from app.identity.dock_survey import surveys_recorded_today
from app.schemas.driver_app import ArriveBody, CompleteStopBody, DockSurveyBody, HandoffPhotoBody, SiteFactsBody
from tests.integration.test_dock_survey import _authed, _captured_door, _dropoff, _world
from tests.integration.test_visits import DOOR_PIN, STOP_PIN, _a_day, _site, _tier

pytestmark = pytest.mark.integration

DOOR_ANSWERS = dict(
    stop_point="lot", door_path="ground_level", obstruction="none", landing_surface="paved_lot",
    handoff_type="roll_up_bay", continuous_sidewalk=True, overhead=[],
)


async def _desk_site(db_session) -> Location:
    """A delivered-to site whose three place facts only the desk knows."""
    site = await _site(db_session)
    site.kind_of_site, site.setting, site.shares_address = None, None, None
    await db_session.flush()
    await apply_desk_facts(
        db_session,
        [SiteEvidence(location_id=site.id, matched=True, kind_of_site="body_shop", setting="suburb", shares_address=True)],
    )
    await db_session.commit()
    return site


async def _run_the_visit(db_session, site):
    tech, day = await _a_day(db_session, site)
    (stop_id,) = day.stop_ids
    authed = _authed(tech.id, tech.hub_id)
    await arrive_at_stop(str(stop_id), ArriveBody(lat=STOP_PIN[0], lng=STOP_PIN[1]), driver=authed, session=db_session)
    await complete_stop(
        str(stop_id), CompleteStopBody(method="photo", door_lat=DOOR_PIN[0], door_lng=DOOR_PIN[1]),
        driver=authed, session=db_session,
    )
    return stop_id, authed


class TestTheDoorLiftsTheDeskCap:
    async def test_desk_site_then_captured_door_then_confirmed_then_scored(self, db_session, real_redis_client):
        site = await _desk_site(db_session)
        stop_id, authed = await _run_the_visit(db_session, site)

        # The door, captured, with the place facts left alone: the desk's cap.
        await record_dock_survey(str(stop_id), DockSurveyBody(**DOOR_ANSWERS), driver=authed, session=db_session)
        for slot in ("approach", "stop_point", "path", "handoff"):
            await record_handoff_photo_route(
                str(stop_id), HandoffPhotoBody(slot=slot, url=f"handoff/{slot}.jpg"), driver=authed, session=db_session
            )
        row = await _tier(db_session, stop_id)
        assert (row["has_dropoff"], row["desk_site"], row["tier"]) == (True, True, "bronze")

        # One tap each on the desk's own guesses: nothing changes in the row
        # but where each fact came from, and that is what the grade reads.
        await record_dock_survey(
            str(stop_id),
            DockSurveyBody(kind_of_site="body_shop", setting="suburb", shares_address=True),
            driver=authed, session=db_session,
        )
        await db_session.refresh(site)
        assert (site.kind_of_site, site.setting, site.shares_address) == ("body_shop", "suburb", True)
        assert site.site_fact_sources == {f: SOURCE_SURVEY for f in ("kind_of_site", "setting", "shares_address")}
        row = await _tier(db_session, stop_id)
        assert (row["desk_site"], row["tier"]) == (False, "silver")

        point = (await db_session.scalars(select(HandoffPoint).where(HandoffPoint.location_id == site.id))).one()
        for machine in MACHINES:
            db_session.add(
                MachineLabel(
                    stop_id=stop_id, handoff_point_id=point.id, machine=machine, verdict="yes",
                    reason_codes=[], scorer_id="scorer-1", machine_limits_version="v1",
                )
            )
        await db_session.commit()
        assert (await _tier(db_session, stop_id))["tier"] == "gold"

    async def test_one_fact_left_to_the_desk_keeps_the_cap(self, db_session, real_redis_client):
        site = await _desk_site(db_session)
        stop_id, authed = await _run_the_visit(db_session, site)
        for slot in ("approach", "stop_point", "path", "handoff"):
            await record_handoff_photo_route(
                str(stop_id), HandoffPhotoBody(slot=slot, url=f"handoff/{slot}.jpg"), driver=authed, session=db_session
            )

        # "Not sure" about the setting sends nothing for it.
        await record_dock_survey(
            str(stop_id),
            DockSurveyBody(**DOOR_ANSWERS, kind_of_site="parts_store_counter", shares_address=False),
            driver=authed, session=db_session,
        )

        await db_session.refresh(site)
        assert (site.kind_of_site, site.shares_address, site.setting) == ("parts_store_counter", False, "suburb")
        assert site.site_fact_sources == {"kind_of_site": SOURCE_SURVEY, "shares_address": SOURCE_SURVEY, "setting": DESK}
        assert (await _tier(db_session, stop_id))["tier"] == "bronze"


class TestWhatTheSurveyAsks:
    async def test_the_stop_carries_the_open_facts_and_the_desks_guesses(self, db_session):
        site = await _desk_site(db_session)
        tech, day = await _a_day(db_session, site)

        route = await get_my_route(driver=_authed(tech.id, tech.hub_id), session=db_session)

        (stop,) = route.stops
        assert stop.dock_needs_survey is True
        assert stop.site_facts is not None
        assert (stop.site_facts.kind_of_site, stop.site_facts.setting, stop.site_facts.shares_address) == (
            "body_shop", "suburb", True,
        )
        assert stop.site_facts.ask == ["kind_of_site", "setting", "shares_address"]

    async def test_what_a_person_or_the_import_said_is_not_asked_again(self, db_session):
        # The import's kind and shared-address answer, and an unknown setting.
        site = await _site(db_session, setting=None)
        tech, day = await _a_day(db_session, site)

        view = await get_stop_site_facts(str(day.stop_ids[0]), driver=_authed(tech.id, tech.hub_id), session=db_session)

        assert view.ask == ["setting"]

    async def test_a_kind_outside_the_vocabulary_is_refused(self, db_session):
        site = await _site(db_session)
        tech, day = await _a_day(db_session, site)

        with pytest.raises(HTTPException) as refused:
            await record_dock_survey(
                str(day.stop_ids[0]), DockSurveyBody(kind_of_site="bakery"),
                driver=_authed(tech.id, tech.hub_id), session=db_session,
            )

        assert refused.value.status_code == 422
        assert "kind_of_site" in refused.value.detail


class TestSiteFactWriters:
    async def test_every_writer_records_its_source_per_fact(self, db_session):
        site = await _site(db_session)
        site.shares_address = None
        await db_session.flush()
        await apply_desk_facts(
            db_session,
            [SiteEvidence(location_id=site.id, matched=True, kind_of_site=None, setting=None, shares_address=True)],
        )
        assert site.site_fact_sources == {"shares_address": DESK}
        # The desk leaves the site-wide source alone; its own entry says desk.
        assert site.site_source == "import"

        # The import confirming it from the distributor's own accounts.
        set_site_fact(site, "shares_address", True, "import")
        await db_session.commit()

        assert site.site_fact_sources == {"shares_address": "import"}
        assert facts_to_ask(site) == []

    async def test_an_answer_lands_on_the_site_a_merged_dock_now_is(self, db_session):
        survivor = await _site(db_session, setting=None)
        merged = Location(normalized_address=f"{uuid.uuid4().hex[:8]}merged", address="merged", merged_into_id=survivor.id)
        db_session.add(merged)
        await db_session.flush()

        written = await record_site_facts(db_session, merged, setting="city_core")
        await db_session.commit()

        assert written is not None and written.site.id == survivor.id
        await db_session.refresh(survivor)
        assert (survivor.setting, (survivor.site_fact_sources or {}).get("setting")) == ("city_core", SOURCE_SURVEY)
        await db_session.refresh(merged)
        assert merged.setting is None

    async def test_nothing_answered_writes_nothing(self, db_session):
        site = await _site(db_session)

        assert await record_site_facts(db_session, site) is None
        assert site.site_fact_sources is None

    async def test_a_setting_outside_the_vocabulary_is_refused(self, db_session):
        with pytest.raises(SiteFactRefused):
            await record_site_facts(db_session, await _site(db_session), setting="rural")


class TestTestSitesStayDrawn:
    async def test_correcting_a_drawn_sites_kind_moves_neither_its_draw_nor_its_group(self, db_session):
        """D-TEST: a site is drawn once, ever, into the group it had then. A later
        answer at the door changes what the site is now - and nothing about the
        draw, so it can never move a site in or out of the test pool."""
        sites = [await _site(db_session, region="TX", deliveries=0) for _ in range(4)]
        await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")
        await db_session.commit()
        before = {
            d.location_id: (d.kind_of_site, d.setting, d.is_test, d.position)
            for d in (await db_session.scalars(select(SiteDraw).where(SiteDraw.region == "TX"))).all()
        }
        assert set(before) == {s.id for s in sites}

        for site in sites:
            await record_site_facts(db_session, site, kind_of_site="dealership_counter", setting="city_core")
        await db_session.commit()
        again = await draw_test_sites(db_session, region="TX", share=0.25, drawn_by="test")
        await db_session.commit()

        after = {
            d.location_id: (d.kind_of_site, d.setting, d.is_test, d.position)
            for d in (await db_session.scalars(select(SiteDraw).where(SiteDraw.region == "TX"))).all()
        }
        assert after == before
        assert sum(again.by_group.values()) == 0


async def _desk_dock(db_session):
    """A drop-off route at a door already captured and surveyed this year - so
    the survey is not due - whose site's three facts only the desk knows."""
    hub, client, shop, driver, location = await _world(db_session)
    await apply_desk_facts(
        db_session,
        [SiteEvidence(location_id=location.id, matched=True, kind_of_site="body_shop", setting="suburb", shares_address=True)],
    )
    await db_session.commit()
    await _captured_door(db_session, location)
    surveyed = await _dropoff(db_session, hub, client, shop, driver, dock=location)
    await record_dock_survey(
        str(surveyed.id), DockSurveyBody(landing_surface="paved_lot"), driver=_authed(driver, hub), session=db_session,
    )
    stop = await _dropoff(db_session, hub, client, shop, driver, status="pending", dock=location)
    return stop, location, _authed(driver, hub), driver


class TestTheFactsOnTheirOwn:
    async def test_a_captured_door_with_desk_facts_still_carries_them(self, db_session):
        """The review's finding: the facts went out only with the survey, and a
        door captured before the desk filled its site is past the survey's year
        and shift gates - so its record stayed capped for a year, unasked."""
        stop, location, authed, driver = await _desk_dock(db_session)

        route = await get_my_route(driver=authed, session=db_session)

        view = next(v for v in route.stops if v.stop_id == str(stop.id))
        assert view.dock_needs_survey is False
        assert view.site_facts is not None
        assert view.site_facts.ask == ["kind_of_site", "setting", "shares_address"]

    async def test_answering_them_alone_lifts_the_cap_and_is_not_a_survey(self, db_session):
        stop, location, authed, driver = await _desk_dock(db_session)
        surveys_before = await surveys_recorded_today(db_session, driver)

        answered = await record_stop_site_facts(
            str(stop.id),
            SiteFactsBody(kind_of_site="body_shop", setting="suburb", shares_address=True),
            driver=authed, session=db_session,
        )

        assert answered.ask == [] and answered.disputed == []
        await db_session.refresh(location)
        assert location.site_fact_sources == {f: SOURCE_SURVEY for f in ("kind_of_site", "setting", "shares_address")}
        assert location.site_fact_answers["setting"]["by"] == str(driver)
        assert location.site_fact_answers["setting"]["stop_id"] == str(stop.id)
        # Not a survey: it counts against neither the year nor the shift cap.
        assert await surveys_recorded_today(db_session, driver) == surveys_before
        # Nothing left open, so the stop no longer carries the facts.
        route = await get_my_route(driver=authed, session=db_session)
        view = next(v for v in route.stops if v.stop_id == str(stop.id))
        assert view.site_facts is None

    async def test_a_stop_with_no_dock_is_a_409(self, db_session):
        hub, client, shop, driver, _ = await _world(db_session, linked=False)
        stop = await _dropoff(db_session, hub, client, shop, driver, status="pending")

        with pytest.raises(HTTPException) as refused:
            await record_stop_site_facts(
                str(stop.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session,
            )

        assert refused.value.status_code == 409


class TestAnAnswerNeverOverwritesAPersons:
    async def test_a_disagreeing_answer_is_kept_as_a_dispute_not_applied(self, db_session):
        site = await _site(db_session, setting=None)
        first, second = uuid.uuid4(), uuid.uuid4()

        applied = await record_site_facts(db_session, site, setting="suburb", by=first)
        disputed = await record_site_facts(db_session, site, setting="city_core", by=second)
        await db_session.commit()

        assert (applied.applied, disputed.applied, disputed.disputed) == (["setting"], [], ["setting"])
        await db_session.refresh(site)
        assert site.setting == "suburb"
        entry = site.site_fact_answers["setting"]
        assert (entry["value"], entry["by"]) == ("suburb", str(first))
        assert [(d["value"], d["by"]) for d in entry["disputes"]] == [("city_core", str(second))]

    async def test_a_stale_offline_replay_cannot_undo_a_newer_answer(self, db_session):
        """Driver A's phone cached the site while nobody knew; B answered yes;
        A's "no", sent when A found signal, arrives after it."""
        site = await _site(db_session)
        site.shares_address = None
        await db_session.commit()

        await record_site_facts(db_session, site, shares_address=True, by=uuid.uuid4())
        replay = await record_site_facts(db_session, site, shares_address=False, by=uuid.uuid4())
        await db_session.commit()

        assert replay.disputed == ["shares_address"]
        await db_session.refresh(site)
        assert site.shares_address is True

    async def test_an_answer_agreeing_with_a_persons_changes_nothing(self, db_session):
        site = await _site(db_session)  # the import's kind and shared-address answer

        agreed = await record_site_facts(db_session, site, kind_of_site="body_shop", by=uuid.uuid4())
        await db_session.commit()

        assert (agreed.applied, agreed.disputed) == ([], [])
        await db_session.refresh(site)
        assert site.site_fact_sources is None
        assert site.site_fact_answers in (None, {})

    async def test_a_no_to_an_address_the_import_recorded_as_shared_is_a_dispute(self, db_session):
        site, other = await _site(db_session), await _site(db_session)
        site.shares_address = None
        db_session.add(SiteAddressShare(location_id=site.id, shares_with_location_id=other.id))
        await db_session.commit()

        answered = await record_site_facts(db_session, site, shares_address=False, by=uuid.uuid4())
        await db_session.commit()

        assert answered.disputed == ["shares_address"]
        await db_session.refresh(site)
        assert site.shares_address is None
