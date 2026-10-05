"""The hold ends in time to make the promise, not only when the tier's window does.

The design doc's Section 5: "Hold deadline = SLA commitment time minus estimated
drive time minus 5-minute buffer." Intake set the hold to the request time plus
the tier's hold window (T2: 90 minutes) whatever the client had been promised, and
held an externally promised order until its delivery window closed. A lone order
used to leave the queue at once, which hid all of it. Since the queue waits for
partners as Section 6 says, every lone order is held until this deadline.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.batch_queue.store import HoldQueueStore
from app.billing.rates import distance_between
from app.ingestion.service import ingest_lmx_order
from app.models.client_sla_term import ClientSlaTerm
from app.sla.engine import DEFAULT_HOLD_WINDOW_MINUTES, HOLD_DEADLINE_BUFFER
from app.travel import minutes_for_miles
from tests.integration.test_adhoc_pickup import (
    DROP_LAT,
    DROP_LNG,
    PICKUP_LAT,
    PICKUP_LNG,
    FakeGeocoder,
    _order,
    _seed,
)

pytestmark = pytest.mark.integration

DRIVE = timedelta(
    minutes=minutes_for_miles(distance_between(PICKUP_LAT, PICKUP_LNG, DROP_LAT, DROP_LNG))
)
T2_HOLD = timedelta(minutes=DEFAULT_HOLD_WINDOW_MINUTES["T2"])


async def _term(db_session, client_id, minutes):
    db_session.add(
        ClientSlaTerm(
            client_id=client_id, sla_tier="T2", delivery_target_minutes=minutes, credit_percent=0
        )
    )
    await db_session.commit()


async def _ingest(db_session, hub_id, client_id, **overrides):
    return await ingest_lmx_order(
        db_session,
        HoldQueueStore(),
        _order(hub_id, client_id, **overrides),
        geocoder=FakeGeocoder(),
    )


def _close(actual: datetime, expected: datetime) -> bool:
    # Intake reads the clock once for the tier hold and the order carries the
    # request time it was handed; they differ by the time the call takes.
    return abs(actual - expected) < timedelta(seconds=2)


async def test_a_tight_contract_term_ends_the_hold_early(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)
    await _term(db_session, client_id, minutes=60)

    order = await _ingest(db_session, hub_id, client_id)

    promise = order.requested_at + timedelta(minutes=60)
    assert _close(order.hold_deadline, promise - DRIVE - HOLD_DEADLINE_BUFFER)
    assert order.hold_deadline < order.requested_at + T2_HOLD


async def test_a_generous_term_leaves_the_tiers_hold(db_session, real_redis_client):
    """The tier's window is how long batching may wait. A promise further off
    than that doesn't let it wait longer."""
    hub_id, client_id = await _seed(db_session)
    await _term(db_session, client_id, minutes=240)

    order = await _ingest(db_session, hub_id, client_id)

    assert _close(order.hold_deadline, order.requested_at + T2_HOLD)


async def test_without_a_term_the_tiers_hold_stands(db_session, real_redis_client):
    """No promise was made, so there is none to protect - and inventing one
    would be the guess `delivery_commitment` refuses to make."""
    hub_id, client_id = await _seed(db_session)

    order = await _ingest(db_session, hub_id, client_id)

    assert _close(order.hold_deadline, order.requested_at + T2_HOLD)


async def test_an_external_order_leaves_in_time_for_its_window(db_session, real_redis_client):
    """It used to be held until its delivery window closed, so it went out late
    by the whole drive."""
    hub_id, client_id = await _seed(db_session)
    window_end = datetime.now(timezone.utc) + timedelta(hours=1)

    order = await _ingest(
        db_session,
        hub_id,
        client_id,
        sla_owner="EXTERNAL",
        delivery_window_start=datetime.now(timezone.utc),
        delivery_window_end=window_end,
    )

    assert _close(order.hold_deadline, window_end - DRIVE - HOLD_DEADLINE_BUFFER)


async def test_a_promise_already_too_tight_to_hold_releases_at_once(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)
    await _term(db_session, client_id, minutes=3)

    order = await _ingest(db_session, hub_id, client_id)

    assert order.hold_deadline <= datetime.now(timezone.utc)


async def test_the_queue_holds_against_the_same_deadline(db_session, real_redis_client):
    hub_id, client_id = await _seed(db_session)
    await _term(db_session, client_id, minutes=60)

    order = await _ingest(db_session, hub_id, client_id)

    [held] = await HoldQueueStore().get_all(str(hub_id))
    assert held.hold_deadline == order.hold_deadline
