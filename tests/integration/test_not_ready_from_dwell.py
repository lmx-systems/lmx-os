"""`hold_window_too_short`, inferred from a pickup's dwell (app/learning_loop/not_ready.py).

The learning loop read this flag and nothing wrote it, so the loop never fired
and the scorecard's "held wrong" rate was never a measurement. These tests pin
the inference to the one claim it makes - a driver who stood at a counter far
longer than that counter usually takes was waiting - and then follow one flag
through to the two readers that must treat it differently: the loop, which
counts it, and the exception queue, which must not.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.hub_calendar import hub_day_bounds
from app.identity import resolve_location
from app.learning_loop.detection import DEFAULT_MIN_OCCURRENCES, HOLD_TOO_SHORT_FLAG
from app.learning_loop.not_ready import MAX_WAIT_SECONDS, flag_pickups_that_waited
from app.learning_loop.service import run_nightly_job
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.rules import ProposedRule
from app.models.shop import Shop
from app.models.stop import FLAG_SOURCE_DRIVER, FLAG_SOURCE_INFERRED, Stop, StopFlag, StopOrder
from app.reporting.exceptions import KIND_FLAGGED, build_exception_queue
from app.sla.engine import DEFAULT_HOLD_WINDOW_MINUTES

pytestmark = pytest.mark.integration

TZ = "America/Los_Angeles"
# A hub-local calendar day in the recent past. Recent, because the loop's
# `_load_recent_flags` looks back 14 days from the real clock and the flags
# written here are stamped with it.
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=2)).date()


async def _seed(db_session, *, sample_count=12, p50=120, p90=600):
    """A hub, a client, a shop at a dock with known dwell figures, and a driver."""
    hub = Hub(id=uuid.uuid4(), name="Not Ready Hub", timezone=TZ, lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    driver = Driver(
        id=uuid.uuid4(), hub_id=hub.id, name="Sam O.",
        phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=10,
    )
    db_session.add(driver)
    await db_session.flush()

    location = await resolve_location(
        db_session, address=f"{uuid.uuid4().int % 9000 + 100} Dock Rd, Austin, TX"
    )
    shop = Shop(
        client_id=client.id, name="Counter", address=location.address,
        lat=30.0, lng=-97.0, location_id=location.id,
    )
    db_session.add(shop)
    db_session.add(
        ReceiverProfile(
            location_id=location.id,
            dwell_sample_count=sample_count,
            dwell_p50_seconds=p50,
            dwell_p90_seconds=p90,
            dwell_observed_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await db_session.flush()
    return hub, client, shop, driver


async def _pickup(db_session, hub, shop, driver, *, seconds, day: date = YESTERDAY, hour=10):
    """A completed pickup at `shop` whose tap-to-tap dwell is `seconds`, on the
    hub-local calendar `day`."""
    start, _ = hub_day_bounds(hub, day)
    arrived = start + timedelta(hours=hour)
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    stop = Stop(
        route_id=route.id, shop_id=shop.id, stop_type="pickup", sequence=1,
        status="completed", arrived_at=arrived,
        completed_at=arrived + timedelta(seconds=seconds),
    )
    db_session.add(stop)
    await db_session.flush()
    return stop


async def _flags(db_session) -> list[StopFlag]:
    return list(await db_session.scalars(select(StopFlag)))


async def test_a_pickup_that_waited_far_longer_than_the_dock_usually_takes_is_flagged(db_session):
    """The claim, in one assertion. 1500 seconds at a counter whose p90 is 600
    is a driver waiting for an order; 90 seconds is a normal visit."""
    hub, _, shop, driver = await _seed(db_session)
    waited = await _pickup(db_session, hub, shop, driver, seconds=1500, hour=10)
    await _pickup(db_session, hub, shop, driver, seconds=90, hour=14)
    await db_session.commit()

    written = await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY)

    assert written == 1
    [flag] = await _flags(db_session)
    assert flag.stop_id == waited.id
    assert flag.flag_type == HOLD_TOO_SHORT_FLAG
    assert flag.source == FLAG_SOURCE_INFERRED
    assert flag.created_by_driver_id is None
    assert "1500" in flag.note
    assert "p50 120s / p90 600s" in flag.note


async def test_running_it_again_writes_nothing(db_session):
    """The nightly job is re-runnable, and a stop flagged twice would count
    twice toward a rule proposal."""
    hub, _, shop, driver = await _seed(db_session)
    await _pickup(db_session, hub, shop, driver, seconds=1500)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 1
    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0
    assert len(await _flags(db_session)) == 1


async def test_a_dock_we_barely_know_is_never_judged(db_session):
    """Four stops is not a baseline. The profile withholds a p90 below ten
    samples for the same reason, and the inference respects the same line."""
    hub, _, shop, driver = await _seed(db_session, sample_count=4, p50=120, p90=None)
    await _pickup(db_session, hub, shop, driver, seconds=1500)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0
    assert await _flags(db_session) == []


async def test_a_dwell_over_four_hours_is_a_parking_record_not_a_wait(db_session):
    hub, _, shop, driver = await _seed(db_session)
    await _pickup(db_session, hub, shop, driver, seconds=MAX_WAIT_SECONDS + 60, hour=6)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0


async def test_a_wait_within_the_docks_normal_range_is_not_flagged(db_session):
    """Longer than the median is not the test; longer than the p90 is."""
    hub, _, shop, driver = await _seed(db_session, p50=120, p90=600)
    await _pickup(db_session, hub, shop, driver, seconds=550)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0


async def test_a_fast_docks_baseline_is_never_under_five_minutes(db_session):
    """A counter with a p90 of 80 seconds: the 300s floor overrides it, so a
    200-second visit there is slow, not a wait, and a 400-second one is."""
    hub, _, shop, driver = await _seed(db_session, sample_count=12, p50=40, p90=80)
    await _pickup(db_session, hub, shop, driver, seconds=200, hour=9)
    waited = await _pickup(db_session, hub, shop, driver, seconds=400, hour=13)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 1
    [flag] = await _flags(db_session)
    assert flag.stop_id == waited.id
    assert "400" in flag.note


async def test_a_dock_with_samples_but_no_p90_is_not_judged(db_session):
    """The refresh always stores a p90 once there are ten samples, so this is a
    hand-edited profile - and the inference has no figure to judge against,
    not a fallback it should invent."""
    hub, _, shop, driver = await _seed(db_session, sample_count=12, p50=40, p90=None)
    await _pickup(db_session, hub, shop, driver, seconds=1500)
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0


async def test_only_the_hub_local_day_asked_for_is_considered(db_session):
    """Yesterday on the hub's clock, not UTC's - a stop at 7pm Pacific is the
    same day in Los Angeles and the next in UTC."""
    hub, _, shop, driver = await _seed(db_session)
    that_day = await _pickup(db_session, hub, shop, driver, seconds=1500, day=YESTERDAY, hour=19)
    await _pickup(
        db_session, hub, shop, driver, seconds=1500, day=YESTERDAY - timedelta(days=1), hour=19
    )
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 1
    # Which one matters: 7pm Pacific on the day before is already YESTERDAY in
    # UTC, so a UTC-bounded day would also flag exactly one stop - the wrong one.
    [flag] = await _flags(db_session)
    assert flag.stop_id == that_day.id


async def test_a_drop_off_is_not_a_pickup_and_is_not_flagged(db_session):
    """The flag means the shop was not ready. A long drop-off says something
    about the receiver, which is a different fact with no flag yet."""
    hub, _, shop, driver = await _seed(db_session)
    stop = await _pickup(db_session, hub, shop, driver, seconds=1500)
    stop.stop_type = "dropoff"
    await db_session.commit()

    assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=YESTERDAY) == 0


async def test_three_inferred_flags_on_one_shop_make_the_loop_propose_a_longer_window(db_session):
    """The point of writing the flag: the detector reads it. Three waits at the
    same shop in the lookback window is `DEFAULT_MIN_OCCURRENCES`, and the
    proposal lengthens that shop's T2 hold window by the standard step."""
    hub, _, shop, driver = await _seed(db_session)
    for offset in range(DEFAULT_MIN_OCCURRENCES):
        day = YESTERDAY - timedelta(days=offset)
        await _pickup(db_session, hub, shop, driver, seconds=1500, day=day)
        await db_session.commit()
        assert await flag_pickups_that_waited(db_session, hub_id=hub.id, day=day) == 1

    created = await run_nightly_job(db_session, hub_id=str(hub.id))

    assert len(created) == 1
    [proposal] = created
    assert proposal.rule_type == "sla_hold_window_override"
    assert proposal.scope == {"shop_id": str(shop.id)}
    assert proposal.supporting_annotation_count == DEFAULT_MIN_OCCURRENCES
    assert proposal.proposed_change["T2"] == DEFAULT_HOLD_WINDOW_MINUTES["T2"] + 10
    assert len(list(await db_session.scalars(select(ProposedRule)))) == 1


async def _open_order_at(db_session, hub, client, stop):
    order = Order(
        hub_id=hub.id, client_id=client.id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:6]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=OrderStatus.assigned,
        requested_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    db_session.add(order)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.flush()
    return order


async def test_an_inferred_flag_is_not_a_driver_report_and_does_not_reach_the_exception_queue(
    db_session,
):
    """Same flag type, same kind of stop, two sources. "Driver flagged" means a
    person was there and said so; the inference is the system reading a dwell,
    and its next action - "read the driver's note" - has no note to read."""
    hub, client, shop, driver = await _seed(db_session)
    reported = await _pickup(db_session, hub, shop, driver, seconds=1500, hour=9)
    inferred = await _pickup(db_session, hub, shop, driver, seconds=1500, hour=13)
    reported_order = await _open_order_at(db_session, hub, client, reported)
    await _open_order_at(db_session, hub, client, inferred)
    db_session.add(
        StopFlag(
            stop_id=reported.id, flag_type=HOLD_TOO_SHORT_FLAG, source=FLAG_SOURCE_DRIVER,
            created_by_driver_id=driver.id, note="stood here twenty minutes",
        )
    )
    db_session.add(
        StopFlag(
            stop_id=inferred.id, flag_type=HOLD_TOO_SHORT_FLAG, source=FLAG_SOURCE_INFERRED,
            created_by_driver_id=None, note="inferred: waited 1500s",
        )
    )
    await db_session.commit()

    queue = await build_exception_queue(db_session, hub_id=hub.id)

    assert [(i.kind, i.order_id) for i in queue.items] == [(KIND_FLAGGED, reported_order.id)]
    assert "twenty minutes" in queue.items[0].detail
