"""
Integration coverage for the gig-platform job store (docs/ROADMAP.md G3)
against real Postgres.

The two behaviours worth protecting hardest, because the offsite flagged
both as expensive to get wrong:

1. Assignment scope is a per-job property, not a system mode. Both
   onboarding tracks run simultaneously during any migration, so one
   optimizer run has to handle pinned and poolable jobs at once. "Getting
   this wrong means a rewrite."
2. A collected parcel is a hard pin on ANY track. Reassignment after pickup
   needs a physical handoff, so pooling only buys anything between accept
   and pickup - a carrier-track job that has been picked up is just as
   immovable as a gig-track one.

Calls the route functions directly, same pattern as the other integration
tests here.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.admin_routes import list_gig_jobs
from app.api.driver_routes import (
    list_my_gig_jobs,
    record_my_gig_job,
    update_my_gig_job_status,
)
from app.driver_auth.dependencies import AuthedDriver
from app.gig_platform import service as gig_store
from app.models.driver import Driver
from app.models.hub import Hub
from app.schemas.gig import GigJobIntake, GigJobStatusUpdate

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


async def _seed_driver(db_session):
    hub_id, driver_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Gig Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Driver(
            id=driver_id, hub_id=hub_id, name="Rich", phone=f"+1512555{uuid.uuid4().int % 10000:04d}",
            vehicle_capacity_units=5,
        )
    )
    await db_session.commit()
    return hub_id, driver_id


def _authed(hub_id, driver_id) -> AuthedDriver:
    return AuthedDriver(driver_id=str(driver_id), hub_id=str(hub_id), device_id="test-device")


def _intake(**overrides) -> GigJobIntake:
    defaults = dict(
        source_platform="dispatch",
        platform_job_ref=f"S{uuid.uuid4().int % 10_000_000}.002-HOU1",
        pickup_address="1200 E 6th St, Austin TX",
        pickup_lat=30.2669,
        pickup_lng=-97.7325,
        dropoff_address="900 Congress Ave, Austin TX",
        dropoff_lat=30.2729,
        dropoff_lng=-97.7414,
        pickup_window_open=NOW,
        pickup_window_close=NOW + timedelta(minutes=70),
        dropoff_window_open=NOW + timedelta(minutes=30),
        dropoff_window_close=NOW + timedelta(minutes=180),
        pay_cents=2517,
        distance_miles="8.40",
    )
    defaults.update(overrides)
    return GigJobIntake(**defaults)


async def test_recording_an_offer_pins_it_to_the_capturing_driver(db_session):
    """Gig track: the offer arrived on this driver's individual platform
    account, which is exactly what makes it non-poolable."""
    hub_id, driver_id = await _seed_driver(db_session)

    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )

    assert view.driver_id == str(driver_id)
    assert view.hub_id == str(hub_id)
    assert view.assignment_scope == "pinned_to_driver"
    assert view.is_pinned_to_driver is True
    assert view.status == "offered"
    assert view.pay_cents == 2517


async def test_a_carrier_track_job_is_poolable_until_it_is_picked_up(db_session):
    """The rewrite-risk case. Scope is per-job, and physical possession
    overrides it in one direction only."""
    hub_id, driver_id = await _seed_driver(db_session)

    view = await record_my_gig_job(
        _intake(assignment_scope="any_driver"),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )
    assert view.is_pinned_to_driver is False

    for step in ("accepted", "picked_up"):
        view = await update_my_gig_job_status(
            view.gig_job_id,
            GigJobStatusUpdate(status=step),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )

    # Still declared poolable, but a collected parcel is in one vehicle.
    assert view.assignment_scope == "any_driver"
    assert view.is_pinned_to_driver is True


async def test_pinned_and_poolable_jobs_coexist_in_one_hub(db_session):
    """Both tracks run simultaneously during a migration, so a single hub
    read must return a mix rather than assuming one mode."""
    hub_id, driver_id = await _seed_driver(db_session)

    await record_my_gig_job(
        _intake(source_platform="curri"), driver=_authed(hub_id, driver_id), session=db_session
    )
    await record_my_gig_job(
        _intake(source_platform="roadie", assignment_scope="any_driver"),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    jobs = await list_gig_jobs(str(hub_id), session=db_session, _admin=None)
    assert {j.assignment_scope for j in jobs} == {"pinned_to_driver", "any_driver"}


async def test_the_same_platform_job_cannot_be_recorded_twice(db_session):
    """Intake paths are expected to overlap once G1/G2 exist - a
    notification and a manual entry capturing the same offer. Two rows would
    corrupt the density figures that decide when batching is possible."""
    hub_id, driver_id = await _seed_driver(db_session)
    intake = _intake()

    await record_my_gig_job(intake, driver=_authed(hub_id, driver_id), session=db_session)

    with pytest.raises(HTTPException) as exc:
        await record_my_gig_job(intake, driver=_authed(hub_id, driver_id), session=db_session)
    assert exc.value.status_code == 409


async def test_the_same_ref_on_a_different_platform_is_a_different_job(db_session):
    """Dedupe is scoped to the platform - two platforms can independently
    mint the same reference string."""
    hub_id, driver_id = await _seed_driver(db_session)
    ref = "S4588150.002-HOU1"

    await record_my_gig_job(
        _intake(source_platform="dispatch", platform_job_ref=ref),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )
    await record_my_gig_job(
        _intake(source_platform="curri", platform_job_ref=ref),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    jobs = await list_my_gig_jobs(driver=_authed(hub_id, driver_id), session=db_session)
    assert len(jobs) == 2


async def test_a_collapsed_card_capture_is_recorded_but_not_sequenceable(db_session):
    """A screenshot of a collapsed offer hides the dropoff address behind a
    chevron (G2). That's enough to reject an offer and not enough to plan
    one - so it must be storable, and must say so."""
    hub_id, driver_id = await _seed_driver(db_session)

    view = await record_my_gig_job(
        _intake(
            intake_source="share_sheet",
            dropoff_address=None, dropoff_lat=None, dropoff_lng=None,
        ),
        driver=_authed(hub_id, driver_id),
        session=db_session,
    )

    assert view.intake_source == "share_sheet"
    assert view.is_sequenceable is False


async def test_a_full_capture_is_sequenceable(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )
    assert view.is_sequenceable is True


async def test_a_drivers_day_spans_platforms_in_pickup_order(db_session):
    """The reason the store is multi-platform: one ordered day instead of
    three apps reconciled by hand."""
    hub_id, driver_id = await _seed_driver(db_session)

    for platform, offset in (("roadie", 120), ("curri", 30), ("dispatch", 75)):
        await record_my_gig_job(
            _intake(
                source_platform=platform,
                pickup_window_open=NOW + timedelta(minutes=offset),
                pickup_window_close=NOW + timedelta(minutes=offset + 60),
            ),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )

    jobs = await list_my_gig_jobs(driver=_authed(hub_id, driver_id), session=db_session)
    assert [j.source_platform for j in jobs] == ["curri", "dispatch", "roadie"]


async def test_lifecycle_transitions_stamp_their_own_timestamps(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )
    assert view.accepted_at is None

    for step in ("accepted", "picked_up", "delivered"):
        view = await update_my_gig_job_status(
            view.gig_job_id,
            GigJobStatusUpdate(status=step),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )

    assert view.status == "delivered"
    assert view.accepted_at is not None
    assert view.picked_up_at is not None
    assert view.delivered_at is not None


async def test_a_job_cannot_skip_pickup_on_its_way_to_delivered(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )

    with pytest.raises(HTTPException) as exc:
        await update_my_gig_job_status(
            view.gig_job_id,
            GigJobStatusUpdate(status="delivered"),
            driver=_authed(hub_id, driver_id),
            session=db_session,
        )
    assert exc.value.status_code == 409


async def test_repeating_the_same_status_is_idempotent(db_session):
    """A driver double-tapping on a flaky connection is not an error, and
    the first timestamp is the true one."""
    hub_id, driver_id = await _seed_driver(db_session)
    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )

    first = await update_my_gig_job_status(
        view.gig_job_id, GigJobStatusUpdate(status="accepted"),
        driver=_authed(hub_id, driver_id), session=db_session,
    )
    again = await update_my_gig_job_status(
        view.gig_job_id, GigJobStatusUpdate(status="accepted"),
        driver=_authed(hub_id, driver_id), session=db_session,
    )
    assert again.accepted_at == first.accepted_at


async def test_a_declined_offer_is_kept_rather_than_deleted(db_session):
    """Why we passed on a job is training data as much as why we took it."""
    hub_id, driver_id = await _seed_driver(db_session)
    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )

    declined = await update_my_gig_job_status(
        view.gig_job_id, GigJobStatusUpdate(status="declined"),
        driver=_authed(hub_id, driver_id), session=db_session,
    )
    assert declined.status == "declined"
    assert len(await list_my_gig_jobs(driver=_authed(hub_id, driver_id), session=db_session)) == 1


async def test_a_driver_cannot_touch_another_drivers_gig_job(db_session):
    hub_id, driver_id = await _seed_driver(db_session)
    _, other_driver_id = await _seed_driver(db_session)

    view = await record_my_gig_job(
        _intake(), driver=_authed(hub_id, driver_id), session=db_session
    )

    with pytest.raises(HTTPException) as exc:
        await update_my_gig_job_status(
            view.gig_job_id,
            GigJobStatusUpdate(status="accepted"),
            driver=_authed(hub_id, other_driver_id),
            session=db_session,
        )
    # 404 not 403 - a distinct 403 would confirm the id is real.
    assert exc.value.status_code == 404


async def test_offered_at_is_preserved_for_the_intake_latency_question(db_session):
    """The gap between when the platform surfaced an offer and when its
    pickup window opens is the open question of whether intake speed is the
    binding constraint. The screenshotted offer had four minutes left of a
    seventy-minute window."""
    hub_id, driver_id = await _seed_driver(db_session)
    surfaced = NOW + timedelta(minutes=66)

    view = await record_my_gig_job(
        _intake(offered_at=surfaced), driver=_authed(hub_id, driver_id), session=db_session
    )

    assert view.offered_at == surfaced
    remaining = view.pickup_window_close - view.offered_at
    assert remaining == timedelta(minutes=4)


def test_an_inverted_pickup_window_is_rejected_at_the_edge():
    """Most likely a date rolling over midnight during extraction. Left
    unchecked it reaches the accept-gate, where it silently makes every
    offer look infeasible."""
    with pytest.raises(ValidationError):
        _intake(
            pickup_window_open=NOW + timedelta(minutes=70),
            pickup_window_close=NOW,
        )


def test_an_inverted_dropoff_window_is_rejected_at_the_edge():
    with pytest.raises(ValidationError):
        _intake(
            dropoff_window_open=NOW + timedelta(minutes=180),
            dropoff_window_close=NOW + timedelta(minutes=30),
        )


async def test_hub_listing_can_filter_by_status(db_session):
    hub_id, driver_id = await _seed_driver(db_session)

    keep = await record_my_gig_job(
        _intake(source_platform="curri", offered_at=NOW),
        driver=_authed(hub_id, driver_id), session=db_session,
    )
    await record_my_gig_job(
        _intake(source_platform="roadie", offered_at=NOW - timedelta(minutes=5)),
        driver=_authed(hub_id, driver_id), session=db_session,
    )
    await update_my_gig_job_status(
        keep.gig_job_id, GigJobStatusUpdate(status="accepted"),
        driver=_authed(hub_id, driver_id), session=db_session,
    )

    accepted = await list_gig_jobs(str(hub_id), status="accepted", session=db_session, _admin=None)
    assert [j.gig_job_id for j in accepted] == [keep.gig_job_id]


async def test_the_store_refuses_a_duplicate_and_hands_back_the_existing_row(db_session):
    """Service-level contract the endpoints build on: the caller decides
    whether a duplicate is a 409 or a no-op, so it needs the existing row
    rather than a bare integrity error."""
    hub_id, driver_id = await _seed_driver(db_session)
    intake = _intake()

    first = await gig_store.record_job(
        db_session, intake, hub_id=str(hub_id), driver_id=str(driver_id)
    )

    with pytest.raises(gig_store.DuplicateGigJob) as exc:
        await gig_store.record_job(
            db_session, intake, hub_id=str(hub_id), driver_id=str(driver_id)
        )
    assert exc.value.existing.id == first.id
