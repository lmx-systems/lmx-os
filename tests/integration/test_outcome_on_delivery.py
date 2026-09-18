"""REC-3: the ledger has a writer (`docs/ROADMAP_AUDIT_2026-09.md`).

`REC-3` read `BUILT` with an empty table. `record_delivery_outcome` was
written, tested and merged, and nothing called it: `complete_stop` advanced the
order to `delivered`, paid the gig driver, adjusted the vehicle load, and
recorded no outcome. Every measurement built on the ledger had no rows to read.

These tests go through `complete_stop` rather than calling the recorder, because
calling the recorder is exactly what the eighteen existing tests in
`test_outcome_ledger.py` already did while the product called it never. A test
that proves the ledger works is not a test that proves anything reaches it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.api.driver_routes import accept_offer, arrive_at_stop, complete_stop, scan_parcels
from app.driver_auth.dependencies import AuthedDriver
from app.models.client import Client
from app.models.client_sla_term import ClientSlaTerm
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.outcome_entry import OutcomeEntry
from app.models.route_offer import RouteOffer
from app.models.shop import Shop
from app.record.outcomes import outcomes_for
from app.schemas.driver_app import CompleteStopBody, ScanParcelsBody

pytestmark = pytest.mark.integration

KIND_DELIVERED = "delivered"


async def _seed(db_session, *, delivery_target_minutes: int | None = None, requested_ago_minutes=30):
    hub_id, client_id, shop_id, driver_id = (uuid.uuid4() for _ in range(4))
    db_session.add(Hub(id=hub_id, name="Outcome Hub", lat=34.05, lng=-118.25))
    await db_session.commit()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add_all([
        Shop(id=shop_id, client_id=client_id, name="Outcome Shop", address="1 Main St", lat=34.06, lng=-118.24),
        Driver(id=driver_id, hub_id=hub_id, name="Sam D.", phone="+15555550304", vehicle_capacity_units=5),
    ])
    await db_session.commit()

    if delivery_target_minutes is not None:
        db_session.add(
            ClientSlaTerm(
                client_id=client_id,
                sla_tier="T2",
                delivery_target_minutes=delivery_target_minutes,
                credit_percent=50,
            )
        )
        await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id, client_id=client_id, shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:6]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2",
        # `assigned`, which is what a dispatch cycle leaves behind. Seeded as
        # `held` the whole lifecycle is skipped silently - `held -> picked_up`
        # is not a legal transition, so the order never reaches `delivered` and
        # no outcome is written for a reason that has nothing to do with REC-3.
        status=OrderStatus.assigned,
        requested_at=now - timedelta(minutes=requested_ago_minutes),
        delivery_address="14 Oak Ave", delivery_lat=34.053, delivery_lng=-118.253,
        delivery_contact_name="J. Rivera",
    )
    db_session.add(order)
    await db_session.commit()

    offer = RouteOffer(
        hub_id=hub_id, driver_id=driver_id, status="offered",
        stop_payload=[{
            "order_id": str(order.id), "lat": 34.06, "lng": -118.24,
            "sla_tier": "T2", "shop_name": "Outcome Shop",
        }],
        offered_at=now, expires_at=now + timedelta(minutes=2),
    )
    db_session.add(offer)
    await db_session.commit()

    authed = AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")
    route = await accept_offer(str(offer.id), driver=authed, session=db_session)
    pickup = next(s.stop_id for s in route.stops if s.stop_type == "pickup")
    dropoff = next(s.stop_id for s in route.stops if s.stop_type == "dropoff")

    await arrive_at_stop(pickup, driver=authed, session=db_session)
    await scan_parcels(pickup, ScanParcelsBody(scanned_count=1), driver=authed, session=db_session)
    await complete_stop(
        pickup, CompleteStopBody(method="photo", photo_url="https://example.com/p.jpg"),
        driver=authed, session=db_session,
    )
    return authed, order, dropoff


async def _deliver(db_session, authed, dropoff):
    await arrive_at_stop(dropoff, driver=authed, session=db_session)
    return await complete_stop(
        dropoff, CompleteStopBody(method="photo", photo_url="https://example.com/d.jpg"),
        driver=authed, session=db_session,
    )


class TestADeliveryWritesAnOutcome:
    async def test_completing_a_dropoff_records_one(self, db_session, real_redis_client):
        """The whole finding, in one assertion. Before this the count was zero
        for every delivery the system had ever made."""
        authed, order, dropoff = await _seed(db_session)

        await _deliver(db_session, authed, dropoff)

        entries = await outcomes_for(db_session, subject_id=order.id)
        assert len(entries) == 1
        assert entries[0].kind == KIND_DELIVERED

    async def test_a_pickup_does_not(self, db_session, real_redis_client):
        """Only a dropoff is a delivery. Recording one at collection would make
        the on-time rate count every order twice, once against a promise it had
        not yet had a chance to miss."""
        authed, order, _dropoff = await _seed(db_session)

        assert await outcomes_for(db_session, subject_id=order.id) == []

    async def test_the_outcome_carries_the_real_delivery_time(
        self, db_session, real_redis_client
    ):
        authed, order, dropoff = await _seed(db_session)

        await _deliver(db_session, authed, dropoff)
        await db_session.refresh(order)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.values["delivered_at"] == order.delivered_at.isoformat()


class TestTheCommitmentItIsJudgedAgainst:
    async def test_a_delivery_inside_the_term_is_on_time(
        self, db_session, real_redis_client
    ):
        """Ordered 30 minutes ago against a 180-minute target."""
        authed, order, dropoff = await _seed(
            db_session, delivery_target_minutes=180, requested_ago_minutes=30
        )

        await _deliver(db_session, authed, dropoff)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.values["on_time"] is True
        assert entry.values["lateness_seconds"] < 0
        assert entry.values["commitment_source"] == "tier_term"

    async def test_a_delivery_past_the_term_is_late_and_says_by_how_much(
        self, db_session, real_redis_client
    ):
        """Signed lateness, not a boolean - the savings statement needs the
        magnitude and the on-time rate needs the sign, from the same row."""
        authed, order, dropoff = await _seed(
            db_session, delivery_target_minutes=10, requested_ago_minutes=120
        )

        await _deliver(db_session, authed, dropoff)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.values["on_time"] is False
        assert entry.values["lateness_seconds"] > 0

    async def test_no_term_records_no_promise_rather_than_a_success(
        self, db_session, real_redis_client
    ):
        """A client with no contract term has been promised nothing. Recording
        `on_time: true` would flatter every figure that reads this, and it is
        the flattering direction that makes it worth a test."""
        authed, order, dropoff = await _seed(db_session, delivery_target_minutes=None)

        await _deliver(db_session, authed, dropoff)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.values["on_time"] is None
        assert entry.values["lateness_seconds"] is None
        assert entry.values["commitment_source"] == "none"


class TestItIsWrittenOnceAndOnlyOnce:
    async def test_completing_the_same_stop_twice_records_one_outcome(
        self, db_session, real_redis_client
    ):
        """An offline queue retrying is ordinary, not exceptional. The ledger is
        append-only, so a second row could not be taken back - and two outcomes
        for one delivery would double-count it in every rate computed from them.
        """
        authed, order, dropoff = await _seed(db_session)

        await _deliver(db_session, authed, dropoff)
        await complete_stop(
            dropoff, CompleteStopBody(method="photo", photo_url="https://example.com/d.jpg"),
            driver=authed, session=db_session,
        )

        assert len(await outcomes_for(db_session, subject_id=order.id)) == 1

    async def test_it_is_in_the_same_transaction_as_the_delivery(
        self, db_session, real_redis_client
    ):
        """Unlike the payout and the notifications, which are deliberately
        outside it. A failed SMS must never roll back a completed delivery; this
        is our own record of that delivery, and a delivered order with no
        outcome row is the state REC-3 exists to prevent."""
        authed, order, dropoff = await _seed(db_session)

        await _deliver(db_session, authed, dropoff)
        await db_session.refresh(order)

        # Both visible without any further commit from the test.
        assert order.status == OrderStatus.delivered
        assert await db_session.scalar(
            select(func.count()).select_from(OutcomeEntry).where(
                OutcomeEntry.subject_id == order.id
            )
        ) == 1


class TestItLinksBackToTheDecision:
    async def test_an_order_with_no_recorded_cycle_links_to_nothing(
        self, db_session, real_redis_client
    ):
        """These routes come from an offer, not from a recorded dispatch cycle,
        so there is no snapshot to cite. Null is the honest answer - picking the
        nearest snapshot would manufacture a provenance link that reads exactly
        like a real one."""
        authed, order, dropoff = await _seed(db_session)

        await _deliver(db_session, authed, dropoff)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.decision_snapshot_id is None

    async def test_the_link_is_found_when_a_cycle_did_assign_it(
        self, db_session, real_redis_client
    ):
        """REC-1 and REC-3 are built to be compared and were joinable by nothing
        - `decision_snapshot_id` was null on every row, because nothing wrote
        it."""
        from app.record.decisions import record_decision
        from app.schemas.optimizer import (
            CyclePlan,
            DriverCandidate,
            RouteAssignment,
            RouteVisit,
            StopCandidate,
        )

        authed, order, dropoff = await _seed(db_session)
        key = str(order.id)
        decided_at = order.requested_at + timedelta(minutes=1)

        snapshot = await record_decision(
            db_session,
            CyclePlan(
                hub_id=str(order.hub_id),
                planned_at=decided_at,
                hub_closed=False,
                held_order_count=1,
                released_order_ids=[key],
                shop_name_by_order_id={key: "Outcome Shop"},
                fleet_snapshot=[],
                stops=[
                    StopCandidate(
                        stop_id=key, order_ids=[key], lat=34.06, lng=-118.24,
                        weight_units=1.0, sla_tier="T2",
                        collect_by=decided_at + timedelta(minutes=90),
                    )
                ],
                drivers=[
                    DriverCandidate(
                        driver_id="driver-1", lat=34.05, lng=-118.25,
                        capacity_remaining_units=40.0,
                    )
                ],
                assignments=[
                    RouteAssignment(
                        driver_id="driver-1",
                        visits=[
                            RouteVisit(order_id=key, kind="pickup"),
                            RouteVisit(order_id=key, kind="delivery"),
                        ],
                    )
                ],
                unassigned_stop_ids=[],
                engine="stub_nearest_neighbor",
                plan_duration_seconds=0.02,
            ),
        )

        await _deliver(db_session, authed, dropoff)

        entry = (await outcomes_for(db_session, subject_id=order.id))[0]
        assert entry.decision_snapshot_id == snapshot.id
