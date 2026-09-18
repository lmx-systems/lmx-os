"""One order, all the way through, with every writer asserted in sequence.

Nine changes wired the record and identity layers — `REC-1`..`REC-4`, `IDN-1`,
`IDN-2`, `IDN-4` — and each was tested on its own. **Nobody had watched the
whole thing run once.**

That gap is the same shape as the one the audit found. Every piece passing in
isolation is exactly the state the codebase was already in when `REC-3`'s ledger
was empty in production: the unit tests were green and nothing joined them.

So this test does not call any recorder directly. It puts an order in through
intake, runs a real dispatch cycle, has a driver accept and deliver through the
driver endpoints, runs the nightly work, and then reads the record back through
the console's own endpoint. Every assertion is about something a writer did as a
side effect of ordinary operation.

`tests/integration/test_the_switches.py::TestTheWholeChain` is the sibling of
this for the commercial chain — enrol, ingest, cost, settle. This one is the
record chain.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.driver_routes import accept_offer, arrive_at_stop, complete_stop, scan_parcels
from app.batch_queue.store import HeldOrder, HoldQueueStore
from app.driver_auth.dependencies import AuthedDriver
from app.fleet_state.manager import FleetStateManager
from app.geocoding.base import BaseGeocoder, GeocodeResult
from app.identity.profile import refresh_hub_dwell_statistics
from app.ingestion.service import ingest_lmx_order
from app.models.client import Client
from app.models.decision_snapshot import DecisionSnapshot
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.ops_user import VIEWER_ROLE
from app.models.order import OrderStatus
from app.models.outcome_entry import KIND_DELIVERED, OutcomeEntry
from app.models.receiver_profile import ReceiverProfile
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser
from app.optimizer.google_routes_client import RouteOptimizationClient
from app.optimizer.service import DispatchOptimizerService
from app.record.linkage import run_linkage_detectors
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody
from app.schemas.fleet import DriverLocation, DriverState
from app.schemas.lmx_order import LMXOrder
from app.schemas.optimizer import RouteAssignment, RouteVisit

pytestmark = pytest.mark.integration

PICKUP = "1200 E 6th St, Austin TX"
PICKUP_LAT, PICKUP_LNG = 30.2646, -97.7302
DROP_LAT, DROP_LNG = 30.2729, -97.7414

OPS = AuthedOpsUser(
    ops_user_id="u1", email="dispatcher@lmxit.com", name="Dispatcher", role=VIEWER_ROLE
)


class _Geocoder(BaseGeocoder):
    provider_name = "fake"

    async def geocode(self, address: str) -> GeocodeResult | None:
        return GeocodeResult(
            lat=PICKUP_LAT, lng=PICKUP_LNG, display_name=address, provider="fake"
        )


class _AssigningRouteClient(RouteOptimizationClient):
    """A solver that puts every stop on the one driver's route.

    Real enough for the chain: what matters downstream is that the cycle
    produces an assignment, records it, and creates an offer. Which order the
    visits come in is the solver's business and `test_route_sequencing.py`'s.
    """

    engine_name = "spy_assigning"

    def __init__(self, driver_id: str) -> None:
        self.driver_id = driver_id

    async def optimize(self, drivers, stops):
        if not drivers or not stops:
            return [], [s.stop_id for s in stops]
        visits = []
        for stop in stops:
            visits.append(RouteVisit(order_id=stop.stop_id, kind="pickup"))
        for stop in stops:
            visits.append(RouteVisit(order_id=stop.stop_id, kind="delivery"))
        return [RouteAssignment(driver_id=self.driver_id, visits=visits)], []


async def _seed(db_session):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Chain Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file")
    )
    await db_session.commit()

    driver_id = uuid.uuid4()
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Sam O.",
            phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=5,
        )
    )
    await db_session.commit()

    fleet = FleetStateManager()
    await fleet.upsert_driver_state(
        DriverState(
            driver_id=str(driver_id), hub_id=str(hub_id), status="available", capacity_units=5
        )
    )
    await fleet.update_driver_location(
        DriverLocation(
            driver_id=str(driver_id), lat=30.26, lng=-97.73,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        ),
        hub_id=str(hub_id),
    )
    return hub_id, client_id, driver_id


def _order(hub_id, client_id) -> LMXOrder:
    return LMXOrder(
        source_system="client_portal",
        source_order_ref=f"CHAIN-{uuid.uuid4().hex[:8]}",
        hub_id=str(hub_id),
        client_id=str(client_id),
        pickup_address=PICKUP,
        drop_address_raw="900 Congress Ave, Austin TX",
        drop_lat=DROP_LAT,
        drop_lng=DROP_LNG,
        received_at=datetime.now(timezone.utc),
    )


async def _release_now(queue: HoldQueueStore, hub_id, order_id) -> None:
    """Bring this order's hold deadline forward so the next cycle releases it.

    Re-adds the held order with a past deadline, which is how the queue is
    driven - there is no setter, and waiting out a real SLA hold would make this
    test take ninety minutes. The hold window itself is `app/batch_queue`'s
    business and is tested there.
    """
    held = next(
        h for h in await queue.get_all(str(hub_id)) if h.order_id == str(order_id)
    )
    await queue.add(
        str(hub_id),
        HeldOrder(
            order_id=held.order_id,
            shop_lat=held.shop_lat,
            shop_lng=held.shop_lng,
            sla_tier=held.sla_tier,
            hold_deadline=datetime.now(timezone.utc) - timedelta(minutes=1),
            held_since=held.held_since,
            shop_name=held.shop_name,
            delivery_lat=held.delivery_lat,
            delivery_lng=held.delivery_lng,
        ),
    )


class TestTheRecordChain:
    async def test_one_order_start_to_finish(self, db_session, real_redis_client):
        """Intake to read-back, asserting each writer as it fires.

        The numbered steps are the sequence a real order takes. A failure names
        the link that broke rather than a symptom three steps later.
        """
        hub_id, client_id, driver_id = await _seed(db_session)
        queue = HoldQueueStore()

        # ------------------------------------------------------------------
        # 1. IDN-1 - intake creates the shop AND links it to a physical dock.
        #    Nothing set `Shop.location_id` outside a one-off script until #75,
        #    so every dock created by ordinary intake was invisible to the
        #    identity layer.
        # ------------------------------------------------------------------
        order = await ingest_lmx_order(
            db_session, queue, _order(hub_id, client_id), geocoder=_Geocoder()
        )
        shop = await db_session.get(Shop, order.shop_id)
        assert shop.location_id is not None, "IDN-1: intake did not link a dock"
        dock = await db_session.get(Location, shop.location_id)
        assert dock is not None

        # ------------------------------------------------------------------
        # 2. REC-1 + AGT-4 - a real cycle records what it decided and why.
        #    `run_hold_cycle` always returned a reason and `run_cycle` threw it
        #    away until #70.
        # ------------------------------------------------------------------
        await _release_now(queue, hub_id, order.id)
        await DispatchOptimizerService(
            route_client=_AssigningRouteClient(str(driver_id))
        ).run_cycle(str(hub_id))

        snapshot = await db_session.scalar(
            select(DecisionSnapshot)
            .where(DecisionSnapshot.hub_id == hub_id)
            .order_by(DecisionSnapshot.decided_at.desc())
        )
        assert snapshot is not None, "REC-1: the cycle recorded no decision"
        assert snapshot.hold_decisions, "AGT-4: the cycle recorded no hold reasons"
        assert any(
            d.get("order_id") == str(order.id) for d in snapshot.hold_decisions
        ), "AGT-4: this order's reason was not recorded"

        # ------------------------------------------------------------------
        # 3. The driver takes the offer and delivers it, through the real
        #    endpoints rather than by setting a status.
        # ------------------------------------------------------------------
        offer = await db_session.scalar(
            select(RouteOffer).where(RouteOffer.driver_id == driver_id)
        )
        assert offer is not None, "the cycle produced no offer for the assigned driver"

        authed = AuthedDriver(
            driver_id=str(driver_id), hub_id=str(hub_id), device_id="chain-device"
        )
        route = await accept_offer(str(offer.id), driver=authed, session=db_session)
        pickup = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
        dropoff = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

        await arrive_at_stop(pickup, driver=authed, session=db_session)
        await scan_parcels(
            pickup, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session
        )
        await complete_stop(
            pickup, CompleteStopBody(method="photo", photo_url="https://example.com/p.jpg"),
            driver=authed, session=db_session,
        )
        await arrive_at_stop(dropoff, driver=authed, session=db_session)
        await complete_stop(
            dropoff, CompleteStopBody(method="photo", photo_url="https://example.com/d.jpg"),
            driver=authed, session=db_session,
        )

        await db_session.refresh(order)
        assert order.status == OrderStatus.delivered

        # ------------------------------------------------------------------
        # 4. REC-3 - the delivery wrote an outcome, and it cites the cycle that
        #    assigned it. The ledger had no writer at all until #74, and
        #    `decision_snapshot_id` was null on every row it did have.
        # ------------------------------------------------------------------
        outcome = await db_session.scalar(
            select(OutcomeEntry).where(
                OutcomeEntry.subject_id == order.id, OutcomeEntry.kind == KIND_DELIVERED
            )
        )
        assert outcome is not None, "REC-3: the delivery recorded no outcome"
        assert outcome.values["delivered_at"], "REC-3: the outcome has no delivery time"
        assert outcome.decision_snapshot_id == snapshot.id, (
            "REC-1/REC-3: the outcome did not cite the cycle that assigned the order"
        )

        # ------------------------------------------------------------------
        # 5. IDN-4 - the nightly refresh computes dwell for the dock the order
        #    was collected from. It had no caller until #75, so every profile
        #    held whatever a one-off script last left there.
        # ------------------------------------------------------------------
        refreshed = await refresh_hub_dwell_statistics(db_session, hub_id=hub_id)
        assert refreshed == 1, "IDN-4: the dock this order used was not refreshed"
        profile = await db_session.scalar(
            select(ReceiverProfile).where(ReceiverProfile.location_id == dock.id)
        )
        assert profile is not None and profile.dwell_observed_at is not None

        # ------------------------------------------------------------------
        # 6. REC-4 - the detectors run and raise questions rather than errors.
        #    One clean order should raise nothing, which is the answer that
        #    proves they ran rather than the answer that proves they found
        #    something.
        # ------------------------------------------------------------------
        raised = await run_linkage_detectors(db_session, hub_id=hub_id)
        assert isinstance(raised, dict) and len(raised) == 3, (
            "REC-4: all three detectors should report, even at zero"
        )

        # ------------------------------------------------------------------
        # 7. The read-back. Everything above, seen the way the console sees it.
        # ------------------------------------------------------------------
        from app.api.routes import record_health

        await db_session.commit()
        health = await record_health(
            hub_id=hub_id, window_days=30, session=db_session, _ops=OPS
        )

        assert health.decisions_recorded >= 1
        assert health.outcomes_recorded == 1
        assert health.outcomes_linked_to_a_decision == 1
        assert health.decision_link_percentage == 100.0

        by_name = {w.name: w for w in health.writers}
        assert by_name["Decisions (REC-1)"].last_written_at is not None
        assert by_name["Delivery outcomes (REC-3)"].last_written_at is not None

    async def test_the_chain_leaves_no_order_behind(self, db_session, real_redis_client):
        """A second order through the same hub, to catch the failure a
        single-order test cannot see: a writer keyed on something that happens
        to be unique when there is one of them.
        """
        hub_id, client_id, driver_id = await _seed(db_session)
        queue = HoldQueueStore()

        orders = []
        for _ in range(2):
            order = await ingest_lmx_order(
                db_session, queue, _order(hub_id, client_id), geocoder=_Geocoder()
            )
            await _release_now(queue, hub_id, order.id)
            orders.append(order)

        await DispatchOptimizerService(
            route_client=_AssigningRouteClient(str(driver_id))
        ).run_cycle(str(hub_id))

        snapshot = await db_session.scalar(
            select(DecisionSnapshot)
            .where(DecisionSnapshot.hub_id == hub_id)
            .order_by(DecisionSnapshot.decided_at.desc())
        )
        recorded = {d["order_id"] for d in snapshot.hold_decisions}
        assert {str(o.id) for o in orders} <= recorded, (
            "AGT-4: a cycle recorded a reason for one order and not the other"
        )

        # Both share a pickup address, so both reach the same dock - which is
        # the point of `Location` existing and the thing a per-shop key would
        # get wrong.
        shops = [await db_session.get(Shop, o.shop_id) for o in orders]
        assert shops[0].location_id == shops[1].location_id

        assert await db_session.scalar(
            select(func.count()).select_from(Location)
        ) == 1, "two orders from one address created two docks"
