"""
Coverage for the gig density instrumentation (docs/ROADMAP.md G12).

This module exists to keep one claim honest: that the gig path is not
demonstrating batching yet. The number carrying that weight is
`sequenced_share` - the fraction of delivered jobs the driver was holding
concurrently with another - so the tests below pin down exactly what counts
as sequenced and, more importantly, what does not.

Back-to-back jobs with no overlap are the case that would quietly inflate
this into a batching claim if the definition slipped.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.admin_routes import get_gig_density
from app.models.driver import Driver
from app.models.gig_job import GigJob
from app.models.hub import Hub

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


async def _seed_hub(db_session, driver_count: int = 1):
    hub_id = uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Gig Hub", lat=30.267, lng=-97.743))
    await db_session.commit()

    driver_ids = []
    for i in range(driver_count):
        driver_id = uuid.uuid4()
        db_session.add(
            Driver(
                id=driver_id, hub_id=hub_id, name=f"Driver {i}",
                phone=f"+1512555{uuid.uuid4().int % 10000:04d}", vehicle_capacity_units=5,
            )
        )
        driver_ids.append(driver_id)
    await db_session.commit()
    return hub_id, driver_ids


def _job(hub_id, driver_id, *, status, accepted=None, delivered=None, offered=None, ref=None):
    return GigJob(
        hub_id=hub_id,
        driver_id=driver_id,
        source_platform="dispatch",
        intake_source="manual",
        platform_job_ref=ref or f"REF-{uuid.uuid4().hex[:10]}",
        pickup_address="pickup",
        pickup_window_open=NOW,
        pickup_window_close=NOW + timedelta(minutes=70),
        pay_cents=2517,
        status=status,
        offered_at=offered or NOW,
        accepted_at=accepted,
        delivered_at=delivered,
    )


async def test_offers_per_day_counts_every_offer_seen(db_session):
    """Supply, not throughput - a declined offer still says something about
    how much work is reachable, which is what decides whether automated
    intake is worth building."""
    hub_id, (driver_id,) = await _seed_hub(db_session)
    db_session.add_all(
        [
            _job(hub_id, driver_id, status="delivered", accepted=NOW, delivered=NOW + timedelta(minutes=40)),
            _job(hub_id, driver_id, status="declined"),
            _job(hub_id, driver_id, status="accepted", accepted=NOW),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=10, session=db_session, _admin=None)
    assert report.total_offers == 3
    assert report.offers_per_day == 0.3
    assert report.declined_count == 1
    assert report.accepted_count == 2  # committed = accepted + picked_up + delivered


async def test_back_to_back_jobs_are_not_counted_as_sequenced(db_session):
    """The case that would quietly turn "we did two jobs today" into a
    batching claim. Sequential work is not a pairing."""
    hub_id, (driver_id,) = await _seed_hub(db_session)
    db_session.add_all(
        [
            _job(
                hub_id, driver_id, status="delivered",
                accepted=NOW, delivered=NOW + timedelta(minutes=30),
            ),
            _job(
                hub_id, driver_id, status="delivered",
                accepted=NOW + timedelta(minutes=40), delivered=NOW + timedelta(minutes=70),
            ),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=10, session=db_session, _admin=None)
    assert report.delivered_count == 2
    assert report.measurable_delivered_count == 2
    assert report.sequenced_delivered_count == 0
    assert report.sequenced_share == 0.0


async def test_overlapping_possession_is_counted_as_sequenced(db_session):
    """Two jobs in the vehicle's plan at once - the real thing."""
    hub_id, (driver_id,) = await _seed_hub(db_session)
    db_session.add_all(
        [
            _job(
                hub_id, driver_id, status="delivered",
                accepted=NOW, delivered=NOW + timedelta(minutes=60),
            ),
            _job(
                hub_id, driver_id, status="delivered",
                accepted=NOW + timedelta(minutes=20), delivered=NOW + timedelta(minutes=45),
            ),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=10, session=db_session, _admin=None)
    assert report.sequenced_delivered_count == 2
    assert report.sequenced_share == 1.0


async def test_overlap_across_different_drivers_is_not_sequencing(db_session):
    """Two drivers working simultaneously is a fleet, not a paired route."""
    hub_id, (driver_a, driver_b) = await _seed_hub(db_session, driver_count=2)
    db_session.add_all(
        [
            _job(hub_id, driver_a, status="delivered", accepted=NOW, delivered=NOW + timedelta(minutes=60)),
            _job(hub_id, driver_b, status="delivered", accepted=NOW, delivered=NOW + timedelta(minutes=60)),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=10, session=db_session, _admin=None)
    assert report.sequenced_delivered_count == 0
    assert report.active_driver_count == 2


async def test_a_delivered_job_missing_timestamps_is_excluded_from_the_denominator(db_session):
    """Counting an unmeasurable job as un-sequenced would bias the headline
    number downward, which is the wrong direction to be wrong in when the
    whole point is deciding whether pairing is happening."""
    hub_id, (driver_id,) = await _seed_hub(db_session)
    db_session.add_all(
        [
            _job(hub_id, driver_id, status="delivered", accepted=NOW, delivered=NOW + timedelta(minutes=30)),
            _job(hub_id, driver_id, status="delivered", accepted=None, delivered=None),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=10, session=db_session, _admin=None)
    assert report.delivered_count == 2
    assert report.measurable_delivered_count == 1


async def test_jobs_per_driver_per_day_uses_days_actually_worked(db_session):
    """A part-time driver shouldn't drag the fleet's throughput down by
    counting calendar days they never worked."""
    hub_id, (driver_id,) = await _seed_hub(db_session)
    # Anchored to a fixed hour, not just NOW-3d. The two jobs below are meant to
    # land on the SAME calendar day two hours apart, and NOW-relative arithmetic
    # silently breaks that when the suite runs within two hours of UTC midnight -
    # `day_one + 2h` rolls into the next date and the driver looks like they
    # worked three days instead of two. Cost a real debugging detour once.
    day_one = (NOW - timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    db_session.add_all(
        [
            _job(hub_id, driver_id, status="delivered", offered=day_one,
                 accepted=day_one, delivered=day_one + timedelta(minutes=30)),
            _job(hub_id, driver_id, status="delivered", offered=day_one,
                 accepted=day_one + timedelta(hours=2), delivered=day_one + timedelta(hours=3)),
            _job(hub_id, driver_id, status="delivered", offered=NOW,
                 accepted=NOW, delivered=NOW + timedelta(minutes=30)),
        ]
    )
    await db_session.commit()

    # Three jobs over two worked days, not over the fourteen-day window.
    report = await get_gig_density(str(hub_id), days=14, session=db_session, _admin=None)
    assert report.jobs_per_driver_per_day == 1.5


async def test_the_pilot_baseline_travels_with_every_report(db_session):
    """Rich's control group, so a reading is never rendered against nothing."""
    hub_id, _ = await _seed_hub(db_session)
    report = await get_gig_density(str(hub_id), days=14, session=db_session, _admin=None)
    assert report.pilot_jobs_per_driver_per_day == 1.8


async def test_an_empty_hub_reports_nulls_rather_than_misleading_zeroes(db_session):
    """"We accepted none of nothing" is not a 0% acceptance rate, and a 0.0
    sequenced share on no data would read as evidence of no batching."""
    hub_id, _ = await _seed_hub(db_session)

    report = await get_gig_density(str(hub_id), days=14, session=db_session, _admin=None)
    assert report.total_offers == 0
    assert report.acceptance_rate is None
    assert report.jobs_per_driver_per_day is None
    assert report.sequenced_share is None


async def test_jobs_outside_the_window_are_excluded(db_session):
    hub_id, (driver_id,) = await _seed_hub(db_session)
    db_session.add_all(
        [
            _job(hub_id, driver_id, status="delivered", offered=NOW - timedelta(days=30),
                 accepted=NOW - timedelta(days=30), delivered=NOW - timedelta(days=30)),
            _job(hub_id, driver_id, status="delivered", offered=NOW,
                 accepted=NOW, delivered=NOW + timedelta(minutes=30)),
        ]
    )
    await db_session.commit()

    report = await get_gig_density(str(hub_id), days=7, session=db_session, _admin=None)
    assert report.total_offers == 1
