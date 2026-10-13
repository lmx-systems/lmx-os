"""DE-15, the #226/#228 review: answers survive a merge, a settle sees what it
settles, and a person's "keep" holds.

`app/identity/site_facts.py`, `app/identity/merge.py`. The findings:

  - merging a site hid its open disputes (the list and the badge read only
    sites as they are now) and lost what its stops had answered, so a retry
    from one of them was taken as a new answer at the surviving site;
  - a settle filed every open dispute, including one that came in after the
    dispatcher's panel loaded;
  - the badge counted sites and the panel facts;
  - keeping a fact against an answer re-raised the same answer at every door.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.driver_routes import record_stop_site_facts
from app.api.routes import attention_counts, settle_site_fact_dispute, site_fact_disputes
from app.identity.merge import merge_locations, revert_merge
from app.identity.site_facts import SOURCE_OPS
from app.models.location import Location
from app.models.ops_user import DISPATCHER_ROLE
from app.ops_auth.dependencies import AuthedOpsUser
from app.schemas.driver_app import SiteFactsBody
from app.schemas.reporting import SettleSiteFactBody
from tests.integration.test_dock_survey import _authed, _dropoff, _world

pytestmark = pytest.mark.integration

OPS = AuthedOpsUser(
    ops_user_id=str(uuid.uuid4()), email="dispatcher@example.com", name="Dispatcher", role=DISPATCHER_ROLE
)


async def _site(db_session, **facts) -> Location:
    site = Location(
        normalized_address=f"9examplerow{uuid.uuid4().hex[:6]}", address="9 Example Row", lat=30.27, lng=-97.73
    )
    for name, value in facts.items():
        setattr(site, name, value)
    site.site_fact_sources = {name: "import" for name in facts}
    db_session.add(site)
    await db_session.commit()
    return site


async def _a_disputed_setting(db_session):
    """A finished drop-off whose door said city centre, at a site the import
    recorded as a suburb: one open dispute."""
    hub, client, shop, driver, location = await _world(db_session)
    location.setting = "suburb"
    location.site_fact_sources = {"setting": "import"}
    await db_session.commit()
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
    authed = _authed(driver, hub)
    await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)
    return location, stop, authed, (hub, client, shop, driver)


async def _list(db_session):
    return await site_fact_disputes(limit=100, session=db_session, _ops=OPS)


class TestAMergeCarriesTheAnswers:
    async def test_an_open_dispute_moves_to_the_surviving_site(self, db_session):
        source, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        target = await _site(db_session, setting="suburb")

        await merge_locations(db_session, source=source, target=target, reason="same place")
        await db_session.commit()

        listed = [(d.location_id, d.fact, [a.value for a in d.answers]) for d in await _list(db_session)]
        assert (str(target.id), "setting", ["city_core"]) in listed
        assert not any(location_id == str(source.id) for location_id, _, _ in listed)

    async def test_an_applied_answer_the_survivor_disagrees_with_becomes_a_dispute(self, db_session):
        hub, client, shop, driver, source = await _world(db_session)
        stop = await _dropoff(db_session, hub, client, shop, driver, dock=source)
        await record_stop_site_facts(
            str(stop.id), SiteFactsBody(setting="city_core"), driver=_authed(driver, hub), session=db_session
        )
        target = await _site(db_session, setting="suburb")

        await merge_locations(db_session, source=source, target=target, reason="same place")
        await db_session.commit()

        await db_session.refresh(target)
        assert [d["value"] for d in target.site_fact_answers["setting"]["disputes"]] == ["city_core"]
        assert target.setting == "suburb"

    async def test_a_retry_from_a_merged_sites_stop_is_still_a_repeat(self, db_session):
        source, stop, authed, _ = await _a_disputed_setting(db_session)
        target = await _site(db_session, setting="suburb")
        await merge_locations(db_session, source=source, target=target, reason="same place")
        await db_session.commit()

        await record_stop_site_facts(str(stop.id), SiteFactsBody(setting="city_core"), driver=authed, session=db_session)

        await db_session.refresh(target)
        assert len(target.site_fact_answers["setting"]["disputes"]) == 1

    async def test_undoing_the_merge_takes_back_what_it_carried(self, db_session):
        source, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        target = await _site(db_session, setting="suburb")
        merge = await merge_locations(db_session, source=source, target=target, reason="same place")
        await db_session.commit()

        await revert_merge(db_session, merge, ops_user_id=uuid.UUID(OPS.ops_user_id))
        await db_session.commit()

        await db_session.refresh(target)
        await db_session.refresh(source)
        entry = (target.site_fact_answers or {}).get("setting", {})
        assert entry.get("disputes", []) == [] and entry.get("absorbed", []) == []
        # The merged site kept its own record, so it comes back as it was.
        assert [d["value"] for d in source.site_fact_answers["setting"]["disputes"]] == ["city_core"]
        listed = [(d.location_id, d.fact) for d in await _list(db_session)]
        assert (str(source.id), "setting") in listed


class TestASettleSeesWhatItSettles:
    async def test_a_settle_refuses_an_answer_the_panel_never_showed(self, db_session):
        location, _stop, _authed_driver, (hub, client, shop, driver) = await _a_disputed_setting(db_session)
        [shown] = await _list(db_session)
        seen = datetime.fromisoformat(shown.answers[-1].at)
        # Another door disagrees after the panel loaded.
        later = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        await record_stop_site_facts(
            str(later.id), SiteFactsBody(setting="city_core"), driver=_authed(driver, hub), session=db_session
        )

        with pytest.raises(HTTPException) as refused:
            await settle_site_fact_dispute(
                location.id, "setting", SettleSiteFactBody(take=None, seen_up_to=seen), session=db_session, ops=OPS
            )
        assert refused.value.status_code == 409
        await db_session.refresh(location)
        assert len(location.site_fact_answers["setting"]["disputes"]) == 2

    async def test_a_settle_on_what_was_shown_goes_through(self, db_session):
        location, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        [shown] = await _list(db_session)

        await settle_site_fact_dispute(
            location.id,
            "setting",
            SettleSiteFactBody(take="city_core", seen_up_to=datetime.fromisoformat(shown.answers[-1].at)),
            session=db_session,
            ops=OPS,
        )

        await db_session.refresh(location)
        assert (location.setting, location.site_fact_sources["setting"]) == ("city_core", SOURCE_OPS)

    async def test_an_old_snapshot_is_still_refused_after_its_own_answer(self, db_session):
        location, *_ = await _a_disputed_setting(db_session)
        before_any = datetime.now(timezone.utc) - timedelta(hours=1)

        with pytest.raises(HTTPException) as refused:
            await settle_site_fact_dispute(
                location.id, "setting", SettleSiteFactBody(seen_up_to=before_any), session=db_session, ops=OPS
            )
        assert refused.value.status_code == 409


async def test_the_badge_counts_facts_as_the_panel_does(db_session):
    hub, client, shop, driver, location = await _world(db_session)
    location.setting = "suburb"
    location.shares_address = True
    location.site_fact_sources = {"setting": "import", "shares_address": "import"}
    await db_session.commit()
    stop = await _dropoff(db_session, hub, client, shop, driver, dock=location)
    await record_stop_site_facts(
        str(stop.id),
        SiteFactsBody(setting="city_core", shares_address=False),
        driver=_authed(driver, hub),
        session=db_session,
    )

    counts = await attention_counts(hub_id=hub, session=db_session, _ops=OPS)

    panel = [d for d in await _list(db_session) if d.location_id == str(location.id)]
    assert len(panel) == 2
    assert counts.site_fact_disputes == len(await _list(db_session))


class TestAKeepHolds:
    async def test_the_same_answer_after_a_keep_is_not_raised_again(self, db_session):
        location, _stop, _authed_driver, (hub, client, shop, driver) = await _a_disputed_setting(db_session)
        await settle_site_fact_dispute(location.id, "setting", SettleSiteFactBody(), session=db_session, ops=OPS)

        again = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        answered = await record_stop_site_facts(
            str(again.id), SiteFactsBody(setting="city_core"), driver=_authed(driver, hub), session=db_session
        )

        assert answered.disputed == []
        await db_session.refresh(location)
        entry = location.site_fact_answers["setting"]
        assert entry["disputes"] == []
        assert [o["value"] for o in entry["overruled"]] == ["city_core"]
        # And a retry of that stop is still a repeat, not a contradiction.
        await record_stop_site_facts(
            str(again.id), SiteFactsBody(setting="city_core"), driver=_authed(driver, hub), session=db_session
        )

    async def test_keeping_unknown_against_the_imports_pair_holds_too(self, db_session):
        """The review's loop: a "no" to an address the import recorded as shared
        is a dispute against unknown; keeping unknown used to re-raise every
        later "no"."""
        from app.models.site_address_share import SiteAddressShare

        hub, client, shop, driver, location = await _world(db_session)
        neighbour = await _site(db_session)
        db_session.add(SiteAddressShare(location_id=location.id, shares_with_location_id=neighbour.id))
        await db_session.commit()
        first = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        await record_stop_site_facts(
            str(first.id), SiteFactsBody(shares_address=False), driver=_authed(driver, hub), session=db_session
        )
        await settle_site_fact_dispute(location.id, "shares_address", SettleSiteFactBody(), session=db_session, ops=OPS)

        second = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        answered = await record_stop_site_facts(
            str(second.id), SiteFactsBody(shares_address=False), driver=_authed(driver, hub), session=db_session
        )

        assert answered.disputed == []
        await db_session.refresh(location)
        assert location.shares_address is None
        assert location.site_fact_answers["shares_address"]["disputes"] == []

    async def test_a_different_answer_after_a_keep_is_still_a_dispute(self, db_session):
        location, _stop, _authed_driver, (hub, client, shop, driver) = await _a_disputed_setting(db_session)
        await settle_site_fact_dispute(location.id, "setting", SettleSiteFactBody(), session=db_session, ops=OPS)
        location.setting = "city_core"
        location.site_fact_sources = {"setting": SOURCE_OPS}
        await db_session.commit()

        again = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        answered = await record_stop_site_facts(
            str(again.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session
        )

        assert answered.disputed == ["setting"]
