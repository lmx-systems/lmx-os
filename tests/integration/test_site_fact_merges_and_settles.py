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

async def _seen(db_session, location, fact) -> list[str]:
    """The keys of the answers the panel shows for this fact, as it sends them."""
    listed = await _list(db_session)
    return [a.key for d in listed if d.location_id == str(location.id) and d.fact == fact for a in d.answers]


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


class TestOnlyAChainsOutermostLinkComesUndone:
    """The #229 review: undoing A->B after B->C left A's carried answers open on
    C and back on A, and its shops to be moved a second time when B->C was
    undone. Only the outermost link of a chain comes undone now."""

    async def test_undoing_an_inner_link_is_refused(self, db_session):
        a, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        b = await _site(db_session, setting="suburb")
        c = await _site(db_session, setting="suburb")
        inner = await merge_locations(db_session, source=a, target=b, reason="same place")
        await merge_locations(db_session, source=b, target=c, reason="same place")
        await db_session.commit()
        b_id = b.id

        with pytest.raises(ValueError, match="undo that merge first"):
            await revert_merge(db_session, inner, ops_user_id=uuid.UUID(OPS.ops_user_id))
        await db_session.rollback()

        await db_session.refresh(a)
        await db_session.refresh(c)
        assert a.merged_into_id == b_id
        assert [d["value"] for d in c.site_fact_answers["setting"]["disputes"]] == ["city_core"]

    async def test_undoing_outer_then_inner_puts_every_answer_back(self, db_session):
        a, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        b = await _site(db_session, setting="suburb")
        c = await _site(db_session, setting="suburb")
        inner = await merge_locations(db_session, source=a, target=b, reason="same place")
        outer = await merge_locations(db_session, source=b, target=c, reason="same place")
        await db_session.commit()

        await revert_merge(db_session, outer, ops_user_id=uuid.UUID(OPS.ops_user_id))
        await revert_merge(db_session, inner, ops_user_id=uuid.UUID(OPS.ops_user_id))
        await db_session.commit()

        for site in (a, b, c):
            await db_session.refresh(site)
        assert (a.merged_into_id, b.merged_into_id) == (None, None)
        for emptied in (b, c):
            entry = (emptied.site_fact_answers or {}).get("setting", {})
            assert entry.get("disputes", []) == [] and entry.get("absorbed", []) == []
        assert [d["value"] for d in a.site_fact_answers["setting"]["disputes"]] == ["city_core"]
        listed = [(d.location_id, d.fact) for d in await _list(db_session)]
        assert (str(a.id), "setting") in listed
        assert not any(location_id in (str(b.id), str(c.id)) for location_id, _ in listed)


async def test_a_merge_refuses_a_dock_merged_elsewhere_after_it_was_resolved(db_session, monkeypatch):
    """The #229 review: two proposals naming one dock, confirmed together. Each
    resolves the dock before taking the lock; the second must see, under the
    lock, that the first has moved it on, rather than fold its answers into a
    second dock as well. Simulated by resolving to the stale row."""
    import app.identity.merge as merge_module
    from app.identity.merge import confirm_merge, propose_merge
    from app.models.location_merge import STATUS_PROPOSED

    a, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
    b = await _site(db_session, setting="suburb")
    c = await _site(db_session, setting="suburb")
    second = await propose_merge(db_session, source=a, target=c, reason="queued second")
    await merge_locations(db_session, source=a, target=b, reason="confirmed first")
    await db_session.commit()

    async def as_it_was(_session, location):
        # What the second confirmation resolved before the first committed.
        return location

    monkeypatch.setattr(merge_module, "canonical_location", as_it_was)
    with pytest.raises(ValueError, match="a moment ago"):
        await confirm_merge(db_session, second, ops_user_id=uuid.UUID(OPS.ops_user_id))
    await db_session.rollback()

    await db_session.refresh(second)
    await db_session.refresh(c)
    assert second.status == STATUS_PROPOSED
    assert (c.site_fact_answers or {}).get("setting", {}).get("disputes", []) == []


class TestASettleSeesWhatItSettles:
    async def test_a_settle_refuses_an_answer_the_panel_never_showed(self, db_session):
        location, _stop, _authed_driver, (hub, client, shop, driver) = await _a_disputed_setting(db_session)
        [shown] = await _list(db_session)
        seen = [a.key for a in shown.answers]
        # Another door disagrees after the panel loaded.
        later = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        await record_stop_site_facts(
            str(later.id), SiteFactsBody(setting="city_core"), driver=_authed(driver, hub), session=db_session
        )

        with pytest.raises(HTTPException) as refused:
            await settle_site_fact_dispute(
                location.id, "setting", SettleSiteFactBody(take=None, seen=seen), session=db_session, ops=OPS
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
            SettleSiteFactBody(take="city_core", seen=[a.key for a in shown.answers]),
            session=db_session,
            ops=OPS,
        )

        await db_session.refresh(location)
        assert (location.setting, location.site_fact_sources["setting"]) == ("city_core", SOURCE_OPS)

    async def test_a_snapshot_missing_an_answer_is_refused(self, db_session):
        location, *_ = await _a_disputed_setting(db_session)

        with pytest.raises(HTTPException) as refused:
            await settle_site_fact_dispute(
                location.id, "setting", SettleSiteFactBody(seen=[]), session=db_session, ops=OPS
            )
        assert refused.value.status_code == 409

    async def test_a_settle_refuses_an_older_answer_a_merge_brought_in(self, db_session):
        """The #229 review: a merge carries a dispute over with the time it was
        given at its own door. A check on the newest time shown let it through -
        older than anything on the panel, never seen - and a "keep" then filed it
        as turned down for good."""
        source, *_ = await _a_disputed_setting(db_session)
        target, _stop, _authed_driver, _ = await _a_disputed_setting(db_session)
        # The source's answer is the older of the two; the panel shows only the
        # target's.
        shown = await _seen(db_session, target, "setting")
        assert len(shown) == 1

        await merge_locations(db_session, source=source, target=target, reason="same place")
        await db_session.commit()

        with pytest.raises(HTTPException) as refused:
            await settle_site_fact_dispute(
                target.id, "setting", SettleSiteFactBody(take=None, seen=shown), session=db_session, ops=OPS
            )
        assert refused.value.status_code == 409
        await db_session.refresh(target)
        assert len(target.site_fact_answers["setting"]["disputes"]) == 2
        assert not target.site_fact_answers["setting"].get("settled")
        # Looked at again, with both shown, it settles.
        await settle_site_fact_dispute(
            target.id,
            "setting",
            SettleSiteFactBody(take=None, seen=await _seen(db_session, target, "setting")),
            session=db_session,
            ops=OPS,
        )


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
        await settle_site_fact_dispute(
            location.id,
            "setting",
            SettleSiteFactBody(seen=await _seen(db_session, location, "setting")),
            session=db_session,
            ops=OPS,
        )

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
        await settle_site_fact_dispute(
            location.id,
            "shares_address",
            SettleSiteFactBody(seen=await _seen(db_session, location, "shares_address")),
            session=db_session,
            ops=OPS,
        )

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
        await settle_site_fact_dispute(
            location.id,
            "setting",
            SettleSiteFactBody(seen=await _seen(db_session, location, "setting")),
            session=db_session,
            ops=OPS,
        )
        location.setting = "city_core"
        location.site_fact_sources = {"setting": SOURCE_OPS}
        await db_session.commit()

        again = await _dropoff(db_session, hub, client, shop, driver, dock=location)
        answered = await record_stop_site_facts(
            str(again.id), SiteFactsBody(setting="suburb"), driver=_authed(driver, hub), session=db_session
        )

        assert answered.disputed == ["setting"]


class TestAnUndoneMergeCanBeRedone:
    """The #230 review: taking out a wrong inner merge (A->B under B->C) means
    undoing the right outer one first, and nothing could bring B->C back - the
    detector never proposes a pair twice and no route merged directly. Redo is a
    new audited merge through the same checks as a confirmation."""

    async def _chain_undone(self, db_session):
        from app.models.shop import Shop
        from tests.integration.test_merge_review_wiring import _admin

        a, _stop, _driver, (hub, client, _shop, _d) = await _a_disputed_setting(db_session)
        b, _stop_b, _driver_b, _ = await _a_disputed_setting(db_session)
        c = await _site(db_session, setting="suburb")
        shop_at_b = Shop(
            id=uuid.uuid4(), client_id=client, name="Redo Test Panel", address="9 Example Row",
            lat=30.27, lng=-97.73, location_id=b.id,
        )
        db_session.add(shop_at_b)
        await db_session.commit()
        inner = await merge_locations(db_session, source=a, target=b, reason="same place")
        outer = await merge_locations(db_session, source=b, target=c, reason="same place")
        await db_session.commit()
        admin = await _admin(db_session)
        await db_session.commit()
        await revert_merge(db_session, outer, ops_user_id=uuid.UUID(admin.ops_user_id))
        await revert_merge(db_session, inner, ops_user_id=uuid.UUID(admin.ops_user_id))
        await db_session.commit()
        return a, b, c, inner, outer, shop_at_b, admin

    async def test_the_right_outer_merge_comes_back_and_the_wrong_inner_one_stays_out(self, db_session):
        from app.api.routes import redo_reverted_merge
        from app.identity.merge import recently_reverted_merges
        from app.models.location_merge import STATUS_APPLIED, STATUS_REVERTED

        a, b, c, inner, outer, shop_at_b, admin = await self._chain_undone(db_session)
        listed = {row.id for row in await recently_reverted_merges(db_session)}
        assert {inner.id, outer.id} <= listed

        view = await redo_reverted_merge(outer.id, session=db_session, admin=admin)

        assert view.id != outer.id and view.status == STATUS_APPLIED
        for row in (a, b, c, shop_at_b, outer):
            await db_session.refresh(row)
        assert outer.status == STATUS_REVERTED  # the undo stays on the record
        assert b.merged_into_id == c.id and a.merged_into_id is None
        assert shop_at_b.location_id == c.id
        # B's own open dispute is carried to C again; A's stays on A.
        assert [d["value"] for d in c.site_fact_answers["setting"]["disputes"]] == ["city_core"]
        assert [d["value"] for d in a.site_fact_answers["setting"]["disputes"]] == ["city_core"]
        disputed = {d.location_id for d in await _list(db_session)}
        assert str(c.id) in disputed and str(a.id) in disputed and str(b.id) not in disputed
        # Redone, so it leaves the redo list. So does the wrong inner pair, now
        # that B is merged again: redoing it would join C as well.
        listed = {row.id for row in await recently_reverted_merges(db_session)}
        assert outer.id not in listed and inner.id not in listed

    async def test_the_redone_merge_can_be_undone_again(self, db_session):
        from app.api.routes import redo_reverted_merge, revert_applied_merge

        a, b, c, _inner, outer, shop_at_b, admin = await self._chain_undone(db_session)
        view = await redo_reverted_merge(outer.id, session=db_session, admin=admin)
        await revert_applied_merge(view.id, session=db_session, admin=admin)

        for row in (b, c, shop_at_b):
            await db_session.refresh(row)
        assert b.merged_into_id is None and shop_at_b.location_id == b.id
        assert (c.site_fact_answers or {}).get("setting", {}).get("disputes", []) == []

    async def test_refused_while_a_side_is_merged_elsewhere(self, db_session):
        from app.api.routes import redo_reverted_merge
        from app.identity.merge import recently_reverted_merges

        _a, b, c, _inner, outer, _shop, admin = await self._chain_undone(db_session)
        d = await _site(db_session, setting="suburb")
        d_id, outer_id = d.id, outer.id
        await merge_locations(db_session, source=b, target=d, reason="same place")
        await db_session.commit()

        assert outer_id not in {row.id for row in await recently_reverted_merges(db_session)}
        with pytest.raises(HTTPException) as refused:
            await redo_reverted_merge(outer_id, session=db_session, admin=admin)
        assert refused.value.status_code == 409
        await db_session.rollback()
        await db_session.refresh(b)
        assert b.merged_into_id == d_id

    async def test_only_an_undone_merge_can_be_redone(self, db_session):
        from app.api.routes import redo_reverted_merge
        from tests.integration.test_merge_review_wiring import _admin

        a = await _site(db_session, setting="suburb")
        b = await _site(db_session, setting="suburb")
        applied = await merge_locations(db_session, source=a, target=b, reason="same place")
        await db_session.commit()
        admin = await _admin(db_session)
        await db_session.commit()

        with pytest.raises(HTTPException) as refused:
            await redo_reverted_merge(applied.id, session=db_session, admin=admin)
        assert refused.value.status_code == 409

    async def test_the_inner_link_refusal_says_the_outer_can_be_redone(self, db_session):
        a = await _site(db_session, setting="suburb")
        b = await _site(db_session, setting="suburb")
        c = await _site(db_session, setting="suburb")
        inner = await merge_locations(db_session, source=a, target=b, reason="same place")
        await merge_locations(db_session, source=b, target=c, reason="same place")
        await db_session.commit()

        with pytest.raises(ValueError, match="redone from the undone list"):
            await revert_merge(db_session, inner, ops_user_id=uuid.UUID(OPS.ops_user_id))
        await db_session.rollback()

    async def test_the_review_list_carries_undone_merges_when_asked(self, db_session):
        from app.api.routes import merge_proposals

        _a, _b, _c, inner, outer, _shop, _admin_user = await self._chain_undone(db_session)
        without = {p.id for p in await merge_proposals(include_applied=True, session=db_session, _ops=OPS)}
        with_reverted = {
            p.id
            for p in await merge_proposals(
                include_applied=True, include_reverted=True, session=db_session, _ops=OPS
            )
        }
        assert outer.id not in without
        assert {inner.id, outer.id} <= with_reverted
