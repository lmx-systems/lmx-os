"""IDN-1 and IDN-4: shops reach the identity layer, and dwell is refreshed.

Two findings from `docs/ROADMAP_AUDIT_2026-09.md`, and the second could not be
fixed without the first.

**Nothing ever set `Shop.location_id`.** `resolve_location`'s only caller was
`scripts/load_identity_from_export.py`, a one-off backfill — so every shop
created since that script ran had no dock. The ad-hoc pickup path, which is
LMX Link's whole premise, created places the identity layer never saw.

**Nothing ever called `refresh_dwell_statistics`.** Every `ReceiverProfile`'s
dwell figures were whatever the backfill last left there, and
`MODEL_AND_DATA_BRIEF.md` specifies `M1` to read them.

Wiring only the second would have shipped a nightly refresh that covers a frozen
subset of docks and looks from every angle like it works. The last two tests
here are the ones that would have caught that.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.batch_queue.store import HoldQueueStore
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.identity.profile import refresh_hub_dwell_statistics
from app.ingestion.service import ingest_lmx_order
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop
from app.schemas.lmx_order import LMXOrder

pytestmark = pytest.mark.integration

ADDRESS = "1200 E 6th St, Austin TX"
LAT, LNG = 30.2646, -97.7302


class FakeGeocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(lat=LAT, lng=LNG, display_name=address, provider="fake")


async def _seed(db_session):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Dock Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    return hub_id, client_id


def _order(hub_id, client_id, *, pickup=ADDRESS) -> LMXOrder:
    return LMXOrder(
        source_system="client_portal",
        source_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        hub_id=str(hub_id),
        client_id=str(client_id),
        pickup_address=pickup,
        drop_address_raw="900 Congress Ave, Austin TX",
        drop_lat=30.2729,
        drop_lng=-97.7414,
        received_at=datetime.now(timezone.utc),
    )


class TestAShopReachesTheIdentityLayer:
    async def test_an_adhoc_pickup_creates_a_dock(self, db_session, real_redis_client):
        """The finding, in one assertion. Before this the ad-hoc path created a
        shop with a null `location_id` every time, so the dock it named was
        invisible to every per-dock statistic."""
        hub_id, client_id = await _seed(db_session)

        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )

        shop = await db_session.get(Shop, order.shop_id)
        assert shop.location_id is not None
        dock = await db_session.get(Location, shop.location_id)
        assert dock.lat == pytest.approx(LAT)

    async def test_two_shops_at_one_address_share_one_dock(
        self, db_session, real_redis_client
    ):
        """The reason a dock is not just a shop. Two customers collecting from
        the same building are one physical place, and dwell observed there is
        one sample set - which is the whole argument for `Location` existing."""
        hub_id, one = await _seed(db_session)
        two = uuid.uuid4()
        db_session.add(Client(id=two, hub_id=hub_id, name="Other", pos_system="flat_file"))
        await db_session.commit()

        first = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, one), geocoder=FakeGeocoder()
        )
        second = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, two), geocoder=FakeGeocoder()
        )

        shop_one = await db_session.get(Shop, first.shop_id)
        shop_two = await db_session.get(Shop, second.shop_id)
        assert shop_one.id != shop_two.id
        assert shop_one.location_id == shop_two.location_id

    async def test_an_address_naming_no_place_leaves_the_dock_null(
        self, db_session, real_redis_client
    ):
        """`N/A` names nowhere. Collecting those into one shared fictional dock
        is the failure the `Location` table exists to prevent, so the honest
        record is no dock at all - and the order still ingests, because an
        identity question must never be why a delivery fails."""
        hub_id, client_id = await _seed(db_session)

        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id, pickup="N/A"),
            geocoder=FakeGeocoder(),
        )

        shop = await db_session.get(Shop, order.shop_id)
        assert shop.location_id is None
        assert order.id is not None

    async def test_onboarding_a_client_links_its_shops_too(
        self, db_session, real_redis_client
    ):
        """The other creation site. One of two wired is a subset that looks like
        a whole."""
        from app.api.admin_routes import onboard_client
        from app.models.ops_user import ADMIN_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser
        from app.schemas.admin import ClientOnboardingBody, ShopOnboardingInput

        hub_id, _client_id = await _seed(db_session)

        result = await onboard_client(
            ClientOnboardingBody(
                hub_id=str(hub_id),
                name="Onboarded Partner",
                pos_system="flat_file",
                shops=[
                    ShopOnboardingInput(
                        name="Main Counter", address=ADDRESS, lat=LAT, lng=LNG,
                        external_ref="MAIN-1",
                    )
                ],
                rates=[],
                portal_email=f"{uuid.uuid4().hex[:8]}@example.com",
                portal_password="a-long-enough-password",
            ),
            session=db_session,
            _admin=AuthedOpsUser(
                ops_user_id="u1", email="a@example.com", name="Admin", role=ADMIN_ROLE
            ),
        )

        shop = await db_session.get(Shop, uuid.UUID(result.shop_ids[0]))
        assert shop.location_id is not None


class TestDwellIsActuallyRefreshed:
    async def _stop_at(self, db_session, hub_id, shop, *, seconds, when=None):
        when = when or datetime.now(timezone.utc) - timedelta(hours=2)
        driver = Driver(
            hub_id=hub_id, name="Sam D.", phone=f"+1555555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
        db_session.add(driver)
        await db_session.flush()
        route = Route(hub_id=hub_id, driver_id=driver.id, status="completed")
        db_session.add(route)
        await db_session.flush()
        # No `hub_id` on a Stop - it belongs to a route, and the route to a hub.
        stop = Stop(
            route_id=route.id, shop_id=shop.id, stop_type="pickup",
            sequence=1, status="completed",
            arrived_at=when, completed_at=when + timedelta(seconds=seconds),
        )
        db_session.add(stop)
        await db_session.flush()
        return stop

    async def test_it_computes_a_median_from_completed_stops(
        self, db_session, real_redis_client
    ):
        hub_id, client_id = await _seed(db_session)
        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )
        shop = await db_session.get(Shop, order.shop_id)
        for seconds in (100, 200, 300):
            await self._stop_at(db_session, hub_id, shop, seconds=seconds)
        await db_session.commit()

        refreshed = await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)

        assert refreshed == 1
        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == shop.location_id)
        )
        assert profile.dwell_sample_count == 3
        assert profile.dwell_p50_seconds == 200

    async def test_a_p90_is_withheld_on_a_thin_sample(
        self, db_session, real_redis_client
    ):
        """A p90 over three stops is the number a reader is most likely to quote
        and least entitled to. Asserted here because the refresher is the first
        thing that ever produced one."""
        hub_id, client_id = await _seed(db_session)
        order = await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )
        shop = await db_session.get(Shop, order.shop_id)
        for seconds in (100, 200, 300):
            await self._stop_at(db_session, hub_id, shop, seconds=seconds)
        await db_session.commit()

        await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)

        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == shop.location_id)
        )
        assert profile.dwell_p50_seconds is not None
        assert profile.dwell_p90_seconds is None

    async def test_a_dock_with_no_completed_stops_is_not_touched(
        self, db_session, real_redis_client
    ):
        """No observations is not a dwell of zero, and a profile stamped with an
        observation time it never made would read as measured."""
        hub_id, client_id = await _seed(db_session)
        await ingest_lmx_order(
            db_session, HoldQueueStore(), _order(hub_id, client_id), geocoder=FakeGeocoder()
        )
        await db_session.commit()

        refreshed = await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)

        assert refreshed == 0
        assert await db_session.scalar(
            select(func.count()).select_from(ReceiverProfile)
        ) == 0

    async def test_it_covers_a_dock_the_adhoc_path_created(
        self, db_session, real_redis_client
    ):
        """The test that ties the two findings together.

        Wiring the refresh without wiring the links would have produced a
        nightly job that runs, reports a count, and silently covers only the
        docks a one-off script loaded months ago. Every dock in this test came
        from ordinary intake.
        """
        hub_id, client_id = await _seed(db_session)
        shops = []
        for address in (ADDRESS, "900 Congress Ave, Austin TX", "100 Test Rd, Austin TX"):
            order = await ingest_lmx_order(
                db_session, HoldQueueStore(), _order(hub_id, client_id, pickup=address),
                geocoder=FakeGeocoder(),
            )
            shop = await db_session.get(Shop, order.shop_id)
            shops.append(shop)
            await self._stop_at(db_session, hub_id, shop, seconds=120)
        await db_session.commit()

        refreshed = await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)

        assert refreshed == 3
        assert await db_session.scalar(
            select(func.count()).select_from(ReceiverProfile)
        ) == 3

    async def test_another_hubs_docks_are_left_alone(
        self, db_session, real_redis_client
    ):
        """The refresh is per hub, on each hub's own nightly tick. A hub-wide
        job that recomputed every dock in the system would do the same work
        once per hub, every night."""
        hub_id, client_id = await _seed(db_session)
        other_hub, other_client = await _seed(db_session)

        for hub, client in ((hub_id, client_id), (other_hub, other_client)):
            order = await ingest_lmx_order(
                db_session, HoldQueueStore(),
                _order(hub, client, pickup=f"{hub} Test St, Austin TX"),
                geocoder=FakeGeocoder(),
            )
            shop = await db_session.get(Shop, order.shop_id)
            await self._stop_at(db_session, hub, shop, seconds=90)
        await db_session.commit()

        assert await refresh_hub_dwell_statistics(db_session, hub_id=hub_id) == 1

    async def test_the_order_is_stalest_first(self, db_session, real_redis_client):
        """So a hub with more docks than the cap makes progress every night
        instead of recomputing the same first page forever."""
        hub_id, client_id = await _seed(db_session)
        for address in ("10 A St, Austin TX", "20 B St, Austin TX"):
            order = await ingest_lmx_order(
                db_session, HoldQueueStore(), _order(hub_id, client_id, pickup=address),
                geocoder=FakeGeocoder(),
            )
            shop = await db_session.get(Shop, order.shop_id)
            await self._stop_at(db_session, hub_id, shop, seconds=60)
        await db_session.commit()

        await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)
        # One is now freshly observed; the other is refreshed first next time.
        profiles = list(await db_session.scalars(select(ReceiverProfile)))
        assert len(profiles) == 2
        profiles[0].dwell_observed_at = datetime.now(timezone.utc) - timedelta(days=7)
        await db_session.commit()

        assert await refresh_hub_dwell_statistics(db_session, hub_id=hub_id, limit=1) == 1
        await db_session.refresh(profiles[0])
        assert profiles[0].dwell_observed_at > datetime.now(timezone.utc) - timedelta(minutes=5)
