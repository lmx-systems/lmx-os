"""IDN-4: dwell, hours, access and autonomy flags, queryable per dock.

`docs/ROADMAP_1.5.md` Phase 1. The profile hangs off `Location` and never off
`Shop` (§2.2b): two accounts at one loading bay share its door and its dwell,
not its contract.

The distinction these tests keep returning to is between the three kinds of
knowledge stored here - observed, stated, surveyed. They are not
interchangeable, and the dwell columns in particular are a summary of what has
happened, not a promise about what will. `M1`/`PRD-5` is the model; this is one
of its inputs.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.identity import (
    profile_for,
    refresh_dwell_statistics,
    resolve_location,
    set_access,
    set_autonomy_fit,
    set_receiving_hours,
)
from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop

pytestmark = pytest.mark.integration

BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


async def _seed_dock(db_session, address="500 Dock Rd, Austin, TX"):
    hub = Hub(id=uuid.uuid4(), name="Profile Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Profile Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    driver = Driver(
        id=uuid.uuid4(), hub_id=hub.id, name="Sam O.",
        phone=f"+1555555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=10,
    )
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)

    location = await resolve_location(db_session, address=address)
    shop = Shop(client_id=client.id, name="Profile Shop", address=address,
                lat=30.0, lng=-97.0, location_id=location.id)
    db_session.add(shop)
    await db_session.flush()
    return location, shop, route


async def _stop(db_session, route, shop, *, seconds, sequence, status="completed"):
    arrived = BASE + timedelta(minutes=sequence * 30)
    db_session.add(
        Stop(
            route_id=route.id, shop_id=shop.id, sequence=sequence, status=status,
            arrived_at=arrived,
            completed_at=(arrived + timedelta(seconds=seconds)) if seconds is not None else None,
        )
    )
    await db_session.flush()


async def test_a_dock_with_no_history_has_no_dwell_rather_than_a_zero(db_session):
    """An absent number and a zero mean completely different things here."""
    location, _, _ = await _seed_dock(db_session)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 0
    assert profile.dwell_p50_seconds is None
    assert profile.dwell_p90_seconds is None
    assert profile.has_dwell is False


async def test_dwell_is_measured_in_seconds_not_minutes(db_session):
    """The design partner's export rounds to minutes and 65.5% of stops come out
    at zero dwell. Storing minutes here would reproduce that defect exactly."""
    location, shop, route = await _seed_dock(db_session)
    for index, seconds in enumerate([20, 35, 50]):
        await _stop(db_session, route, shop, seconds=seconds, sequence=index)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 3
    assert profile.dwell_p50_seconds == 35, "a 35-second stop must not round to zero"


async def test_a_p90_is_withheld_until_there_are_enough_stops_to_mean_anything(db_session):
    """The p50 is still stored, with its sample count beside it.

    A p90 over a handful of stops is the number a reader is most likely to quote
    and least entitled to.
    """
    location, shop, route = await _seed_dock(db_session)
    for index, seconds in enumerate([30, 40, 600]):
        await _stop(db_session, route, shop, seconds=seconds, sequence=index)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 3
    assert profile.dwell_p50_seconds == 40
    assert profile.dwell_p90_seconds is None, "three stops is not a p90"

    for index in range(3, 13):
        await _stop(db_session, route, shop, seconds=45, sequence=index)
    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 13
    assert profile.dwell_p90_seconds is not None


async def test_a_failed_stop_is_counted_as_censored_not_averaged_in(db_session):
    """M1b. A failed stop never produced a true dwell, only a lower bound.

    Averaging those in drags the worst docks downward - the one direction that
    turns into a broken promise rather than a merely wrong number.
    """
    location, shop, route = await _seed_dock(db_session)
    await _stop(db_session, route, shop, seconds=30, sequence=0)
    await _stop(db_session, route, shop, seconds=40, sequence=1)
    # Driver gave up after twelve minutes. Real dwell is "more than 720s".
    await _stop(db_session, route, shop, seconds=720, sequence=2, status="failed")

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 2, "the failed stop is not an observation"
    assert profile.dwell_p50_seconds == 35
    assert profile.dwell_censored_count == 1, "but it is not thrown away either"


async def test_a_completion_before_its_arrival_is_excluded_not_clamped(db_session):
    """A clock or tap problem, not a zero-second stop.

    Clamping to zero would drag a fast dock's median down and look exactly like
    the minute-rounding defect this table exists to replace.
    """
    location, shop, route = await _seed_dock(db_session)
    await _stop(db_session, route, shop, seconds=40, sequence=0)
    await _stop(db_session, route, shop, seconds=-300, sequence=1)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 1
    assert profile.dwell_p50_seconds == 40


async def test_the_profile_follows_a_merge(db_session):
    """An absorbed dock is not a place. Asking for its profile must not orphan
    everything known about the surviving door."""
    location, _, _ = await _seed_dock(db_session)
    alias = await resolve_location(db_session, address="500 Dock Road, Austin, TX")
    alias.merged_into_id = location.id
    await db_session.flush()

    await set_access(db_session, location, appointment_required=True)
    via_alias = await profile_for(db_session, alias)

    assert via_alias is not None
    assert via_alias.location_id == location.id
    assert via_alias.appointment_required is True


async def test_hours_distinguish_closed_from_not_stated(db_session):
    """A day mapped to [] is a fact. A day that is absent is a gap."""
    location, _, _ = await _seed_dock(db_session)

    profile = await set_receiving_hours(
        db_session,
        location,
        {"mon": [["08:00", "12:00"], ["13:00", "17:00"]], "sat": []},
    )

    assert profile.receiving_hours["mon"] == [["08:00", "12:00"], ["13:00", "17:00"]]
    assert profile.receiving_hours["sat"] == [], "closed"
    assert "sun" not in profile.receiving_hours, "not stated"
    assert profile.hours_stated_at is not None


@pytest.mark.parametrize(
    "bad",
    [
        {"monday": [["08:00", "17:00"]]},          # wrong key
        {"mon": "08:00-17:00"},                    # not a list
        {"mon": [["08:00"]]},                      # not a pair
        {"mon": [["8:00", "17:00"]]},              # not zero-padded
        {"mon": [["25:00", "26:00"]]},             # not a real time
        {"mon": [["17:00", "08:00"]]},             # does not move forward
    ],
)
async def test_a_malformed_timetable_is_refused(db_session, bad):
    """JSONB accepts anything, so this is the only place the shape is checked.
    A malformed day would read as "not stated" forever."""
    location, _, _ = await _seed_dock(db_session)
    with pytest.raises(ValueError):
        await set_receiving_hours(db_session, location, bad)


async def test_the_autonomy_labels_are_queryable_per_dock(db_session):
    """IDN-4's done-when, for the half that SUP-3 and SUP-4 are waiting on."""
    location, _, _ = await _seed_dock(db_session)

    await set_autonomy_fit(
        db_session, location,
        landing_surface="paved_lot", curb_access="direct", door_path="loading_dock",
        obstruction="gate", who_receives="dock_crew",
    )
    profile = await profile_for(db_session, location)

    assert profile.landing_surface == "paved_lot"
    assert profile.curb_access == "direct"
    assert profile.door_path == "loading_dock"
    assert profile.obstruction == "gate"
    assert profile.who_receives == "dock_crew"
    assert profile.is_surveyed is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"landing_surface": "helipad"},
        {"curb_access": "somewhat"},
        {"door_path": "window"},
        {"obstruction": "dragon"},
        {"who_receives": "whoever"},
    ],
)
async def test_a_value_outside_the_vocabulary_is_refused(db_session, kwargs):
    """These are M5's labels. An unrecognised value does not fail at write time -
    it fails months later as a class with one example, which is noise."""
    location, _, _ = await _seed_dock(db_session)
    with pytest.raises(ValueError):
        await set_autonomy_fit(db_session, location, **kwargs)


async def test_access_and_autonomy_writes_do_not_clobber_each_other(db_session):
    """Two survey passes, one profile. The second must not blank the first."""
    location, _, _ = await _seed_dock(db_session)

    await set_access(db_session, location, appointment_required=True, carry_effort="trolley")
    await set_autonomy_fit(db_session, location, landing_surface="gravel")
    profile = await profile_for(db_session, location)

    assert profile.appointment_required is True
    assert profile.carry_effort == "trolley"
    assert profile.landing_surface == "gravel"


async def test_one_dock_has_exactly_one_profile(db_session):
    """Two rows would be two answers to "how long does this place take"."""
    location, _, _ = await _seed_dock(db_session)

    first = await profile_for(db_session, location, create=True)
    second = await profile_for(db_session, location, create=True)
    third = await set_access(db_session, location, appointment_required=False)

    assert first.id == second.id == third.id


async def test_two_shops_at_one_dock_share_its_profile(db_session):
    """The reason this hangs off Location and not Shop."""
    location, shop, route = await _seed_dock(db_session)
    second_account = Shop(
        client_id=shop.client_id, name="Second account", address=shop.address,
        lat=30.0, lng=-97.0, location_id=location.id,
    )
    db_session.add(second_account)
    await db_session.flush()

    await _stop(db_session, route, shop, seconds=30, sequence=0)
    await _stop(db_session, route, second_account, seconds=50, sequence=1)

    profile = await refresh_dwell_statistics(db_session, location)

    assert profile.dwell_sample_count == 2, (
        "both accounts' stops are stops at the same door"
    )
    assert profile.dwell_p50_seconds == 40
