"""IDN-4: a dwell measured by whoever was driving before us.

`M1`'s problem is cold start. A dock we have never delivered to has no dwell of
its own, and `refresh_dwell_statistics` can only compute from stops we made. The
design partner's second-precision export carries real observations for hundreds
of the same physical docks.

The tests that matter are the ones about **not blending**. An inherited figure
and one of our own are different measurements, and every way they could quietly
become one number is a separate test here: the nightly refresh overwriting it,
the estimator averaging them, and a figure arriving with no attribution.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.identity.inherited_dwell import (
    MIN_OWN_SAMPLES,
    dwell_estimate,
    import_inherited_dwell,
)
from app.identity.profile import refresh_hub_dwell_statistics
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.receiver_profile import SOURCE_INHERITED, SOURCE_OBSERVED, ReceiverProfile
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop

pytestmark = pytest.mark.integration

SOURCE = "design partner, incumbent platform"
FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 6, 30, tzinfo=timezone.utc)


async def _dock(db_session, *, ref: str, address: str | None = None):
    """A dock reached by a shop carrying the export's account id.

    The join the importer uses is `Shop.external_ref`, which
    `scripts/load_identity_from_export.py` writes - so a fixture that set
    `location_id` without it would pass while the real import matched nothing.
    """
    hub = await db_session.scalar(select(Hub).limit(1))
    if hub is None:
        hub = Hub(id=uuid.uuid4(), name="Dwell Hub", lat=30.27, lng=-97.74)
        db_session.add(hub)
        await db_session.flush()
    client = await db_session.scalar(select(Client).where(Client.hub_id == hub.id).limit(1))
    if client is None:
        client = Client(
            id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file"
        )
        db_session.add(client)
        await db_session.flush()

    dock = Location(
        normalized_address=(address or ref).lower().replace(" ", ""),
        address=address or f"{ref}, Austin, 78701",
        lat=30.26,
        lng=-97.74,
    )
    db_session.add(dock)
    await db_session.flush()
    db_session.add(
        Shop(
            client_id=client.id, name=f"Shop {ref}", address=dock.address,
            lat=30.26, lng=-97.74, external_ref=ref, location_id=dock.id,
        )
    )
    await db_session.flush()
    return hub, dock


class TestTheImport:
    async def test_it_gives_a_never_visited_dock_a_dwell(self, db_session):
        """The whole point. This dock has no stops of ours and would otherwise
        have no dwell at all for M1 to read."""
        _hub, dock = await _dock(db_session, ref="ACCT-1")

        report = await import_inherited_dwell(
            db_session, {"ACCT-1": [100.0, 200.0, 300.0]},
            source=SOURCE, observed_from=FROM, observed_to=TO,
        )

        assert report.docks_updated == 1
        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile.inherited_dwell_p50_seconds == 200
        assert profile.inherited_dwell_sample_count == 3
        assert profile.inherited_dwell_source == SOURCE
        assert profile.inherited_dwell_observed_from == FROM

    async def test_it_never_touches_our_own_figures(self, db_session):
        """The failure this whole design exists to prevent. An imported number
        in `dwell_p50_seconds` survives until 2am and is then replaced by a
        percentile over two deliveries."""
        _hub, dock = await _dock(db_session, ref="ACCT-2")
        db_session.add(
            ReceiverProfile(
                location_id=dock.id, dwell_sample_count=4, dwell_p50_seconds=42
            )
        )
        await db_session.flush()

        await import_inherited_dwell(
            db_session, {"ACCT-2": [900.0]}, source=SOURCE,
        )

        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile.dwell_p50_seconds == 42, "the import overwrote our own observation"
        assert profile.dwell_sample_count == 4
        assert profile.inherited_dwell_p50_seconds == 900

    async def test_the_nightly_refresh_does_not_erase_it(self, db_session):
        """The other direction, and the one a single import test would miss."""
        hub, dock = await _dock(db_session, ref="ACCT-3")
        await import_inherited_dwell(db_session, {"ACCT-3": [500.0]}, source=SOURCE)

        driver = Driver(
            hub_id=hub.id, name="Sam", phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
        db_session.add(driver)
        await db_session.flush()
        route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
        db_session.add(route)
        await db_session.flush()
        shop = await db_session.scalar(select(Shop).where(Shop.location_id == dock.id))
        when = datetime.now(timezone.utc) - timedelta(hours=2)
        db_session.add(
            Stop(
                route_id=route.id, shop_id=shop.id, stop_type="pickup", sequence=1,
                status="completed", arrived_at=when,
                completed_at=when + timedelta(seconds=60),
            )
        )
        await db_session.commit()

        await refresh_hub_dwell_statistics(db_session, hub_id=hub.id)

        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile.dwell_p50_seconds == 60, "our own figure should be computed"
        assert profile.inherited_dwell_p50_seconds == 500, "the refresh erased the import"

    async def test_an_unmatched_receiver_is_counted_not_guessed(self, db_session):
        """A receiver id with no shop means identity was never seeded for that
        account. That is a fact about the import order, not about the dock, and
        inventing a dock for it would create one nothing else can reach."""
        await _dock(db_session, ref="ACCT-4")

        report = await import_inherited_dwell(
            db_session, {"ACCT-4": [100.0], "NEVER-SEEN": [200.0]}, source=SOURCE
        )

        assert report.docks_updated == 1
        assert report.receivers_unmatched == 1

    async def test_a_receiver_with_no_usable_dwell_is_reported_separately(self, db_session):
        """Different from unmatched, and the two get confused. One means we do
        not know the dock; the other means we know it and the file said
        nothing."""
        await _dock(db_session, ref="ACCT-5")

        report = await import_inherited_dwell(
            db_session, {"ACCT-5": [0.0, -5.0]}, source=SOURCE
        )

        assert report.docks_updated == 0
        assert report.receivers_without_usable_dwell == 1
        assert report.receivers_unmatched == 0

    async def test_two_accounts_at_one_dock_pool_their_samples(self, db_session):
        """Several receiver ids can reach one dock - that is what IDN-2's merging
        produces - and the last one winning would throw away most of the
        evidence."""
        hub, dock = await _dock(db_session, ref="ACCT-6")
        client = await db_session.scalar(select(Client).where(Client.hub_id == hub.id))
        db_session.add(
            Shop(
                client_id=client.id, name="Second account", address=dock.address,
                lat=30.26, lng=-97.74, external_ref="ACCT-6B", location_id=dock.id,
            )
        )
        await db_session.flush()

        report = await import_inherited_dwell(
            db_session,
            {"ACCT-6": [100.0, 100.0, 100.0], "ACCT-6B": [300.0, 300.0]},
            source=SOURCE,
        )

        assert report.docks_updated == 1
        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile.inherited_dwell_sample_count == 5
        assert profile.inherited_dwell_p50_seconds == 100

    async def test_reimporting_replaces_rather_than_accumulates(self, db_session):
        """Re-running with a corrected file should leave the corrected figure,
        not the average of two attempts."""
        _hub, dock = await _dock(db_session, ref="ACCT-7")
        await import_inherited_dwell(db_session, {"ACCT-7": [100.0]}, source=SOURCE)
        await import_inherited_dwell(db_session, {"ACCT-7": [400.0, 400.0]}, source=SOURCE)

        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile.inherited_dwell_p50_seconds == 400
        assert profile.inherited_dwell_sample_count == 2

    async def test_the_database_refuses_an_unattributed_figure(self, db_session):
        """An inherited figure with no source is indistinguishable from an
        invented one, so the constraint is at the database rather than only in
        the importer."""
        _hub, dock = await _dock(db_session, ref="ACCT-8")
        db_session.add(ReceiverProfile(location_id=dock.id))
        await db_session.commit()

        with pytest.raises(Exception, match="inherited_dwell_attributed"):
            await db_session.execute(
                text(
                    "UPDATE receiver_profiles SET inherited_dwell_p50_seconds = 300 "
                    "WHERE location_id = :id"
                ),
                {"id": dock.id},
            )
        await db_session.rollback()


class TestWhichFigureAnswers:
    async def test_a_thin_own_sample_defers_to_the_inherited_one(self, db_session):
        profile = ReceiverProfile(
            location_id=uuid.uuid4(),
            dwell_sample_count=2,
            dwell_p50_seconds=30,
            inherited_dwell_p50_seconds=400,
            inherited_dwell_sample_count=500,
            inherited_dwell_source=SOURCE,
        )

        estimate = dwell_estimate(profile)

        assert estimate.p50_seconds == 400
        assert estimate.source == SOURCE_INHERITED
        assert "2 of our own" in estimate.why, "it should say we had our own and did not use it"

    async def test_enough_of_our_own_wins(self, db_session):
        profile = ReceiverProfile(
            location_id=uuid.uuid4(),
            dwell_sample_count=MIN_OWN_SAMPLES,
            dwell_p50_seconds=30,
            inherited_dwell_p50_seconds=400,
            inherited_dwell_sample_count=500,
            inherited_dwell_source=SOURCE,
        )

        estimate = dwell_estimate(profile)

        assert estimate.p50_seconds == 30
        assert estimate.is_ours

    async def test_it_never_blends_the_two(self, db_session):
        """A weighted average of our two deliveries and somebody else's four
        hundred is a number with no owner. Whatever comes back must be one of
        the two inputs, exactly."""
        profile = ReceiverProfile(
            location_id=uuid.uuid4(),
            dwell_sample_count=3,
            dwell_p50_seconds=30,
            inherited_dwell_p50_seconds=400,
            inherited_dwell_sample_count=500,
            inherited_dwell_source=SOURCE,
        )

        estimate = dwell_estimate(profile)

        assert estimate.p50_seconds in (30, 400)

    async def test_a_thin_own_sample_with_nothing_inherited_is_still_reported(self, db_session):
        """Thin is not nothing. Refusing here would throw away the only
        observation there is, and the `why` carries the caveat instead."""
        profile = ReceiverProfile(
            location_id=uuid.uuid4(), dwell_sample_count=2, dwell_p50_seconds=30
        )

        estimate = dwell_estimate(profile)

        assert estimate.p50_seconds == 30
        assert estimate.source == SOURCE_OBSERVED
        assert "thin" in estimate.why

    async def test_a_dock_with_neither_refuses(self, db_session):
        estimate = dwell_estimate(ReceiverProfile(location_id=uuid.uuid4()))

        assert estimate.p50_seconds is None
        assert estimate.source is None
        assert "no dwell" in estimate.why

    async def test_no_profile_at_all_refuses(self, db_session):
        estimate = dwell_estimate(None)

        assert estimate.p50_seconds is None
        assert estimate.source is None


class TestItIsVisibleInTheConsole:
    async def test_record_health_counts_whose_dwell_it_is(self, db_session):
        """The read-back. Without it an import that matched nothing looks
        identical to one that worked."""
        from app.reporting.record_health import build_record_health

        hub, _loc = await _dock(db_session, ref="ACCT-9")
        await _dock(db_session, ref="ACCT-10")
        await import_inherited_dwell(db_session, {"ACCT-9": [250.0]}, source=SOURCE)
        await db_session.commit()

        health = await build_record_health(db_session, hub_id=hub.id)

        assert health.dwell.docks == 2
        assert health.dwell.inherited == 1
        assert health.dwell.from_our_own == 0
        assert health.dwell.unknown == 1

    async def test_a_dock_we_have_never_visited_still_counts(self, db_session):
        """Docks we have not been to are the point - they are the ones an
        inherited figure exists for. A coverage count that only looked at
        visited docks would report full coverage while every new dock had
        nothing."""
        from app.reporting.record_health import build_record_health

        hub, _loc = await _dock(db_session, ref="ACCT-11")
        await db_session.commit()

        health = await build_record_health(db_session, hub_id=hub.id)

        assert health.dwell.docks == 1
        assert health.dwell.unknown == 1
