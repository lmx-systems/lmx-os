"""The public Dock Log, and the gap between it and `M5`'s training data (`DRV-7`).

`ROADMAP_1.5.md`'s `DRV-7` row states the constraint these tests exist to hold:
**a stranger's submission must never write to `receiver_profiles` directly.**

So the tests that matter most here are not the feature ones. They are the four
that prove the separation is real:

  - a submission writes to the staging table and touches no profile;
  - an import refuses a row nobody matched to a dock;
  - an import refuses a dock one of our own drivers has surveyed, rather than
    skipping it quietly;
  - an imported dock is still asked for a survey by the next driver who
    delivers there, because a stranger's answers are `stated` and not
    `surveyed`.

The last one is the subtle one. Copying the answers is easy to get right;
copying them in a way that convinces `dock_needs_survey` the dock is done is
the mistake that would cost us the observation permanently.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.public_routes import submit_dock_log
from app.identity.dock_log import (
    AlreadyImported,
    DockAlreadySurveyed,
    NotReviewed,
    candidate_locations,
    import_submission,
    stage_submission,
    validate_answers,
)
from app.identity.profile import profile_for
from app.models.dock_log_submission import DockLogSubmission
from app.models.location import Location
from app.models.receiver_profile import SOURCE_STATED
from app.schemas.dock_log import DockLogSubmissionBody

pytestmark = pytest.mark.integration


class _FakeRequest:
    """Minimal stand-in for the bits of Request the endpoint reads."""

    def __init__(self, ip: str) -> None:
        self.client = type("C", (), {"host": ip})()


def _ip() -> str:
    """A fresh IP per test - the limiter is real Redis and would otherwise
    carry state between tests."""
    return f"203.0.113.{uuid.uuid4().int % 250}"


def _body(**overrides) -> DockLogSubmissionBody:
    payload = dict(
        business_name="Mercer Street Auto",
        submitted_address="14 Mercer Street, Austin",
        lat=30.2672,
        lng=-97.7431,
        stop_point="loading_dock",
        curb_access="direct",
        landing_surface="paved_lot",
    )
    payload.update(overrides)
    return DockLogSubmissionBody(**payload)


async def _seed_location(db_session, *, lat=30.2672, lng=-97.7431, address="14 Mercer Street") -> Location:
    location = Location(
        normalized_address=f"{address.lower()}-{uuid.uuid4().hex[:6]}",
        address=address,
        lat=lat,
        lng=lng,
    )
    db_session.add(location)
    await db_session.commit()
    return location


# ---------------------------------------------------------------------------
# The separation - what these tests exist for
# ---------------------------------------------------------------------------


async def test_a_submission_writes_to_staging_and_touches_no_profile(db_session, real_redis_client):
    """The whole design in one assertion.

    An unauthenticated caller creates a row in `dock_log_submissions` and
    nothing anywhere else. `receiver_profiles` is the layer `M5` trains on, and
    there is no path from this endpoint to it that does not pass through a
    person.
    """
    location = await _seed_location(db_session)
    result = await submit_dock_log(_body(), _FakeRequest(_ip()), session=db_session)

    assert result.accepted is True
    assert result.answers_recorded == 3

    staged = (await db_session.execute(select(DockLogSubmission))).scalars().all()
    assert len(staged) == 1
    assert staged[0].location_id is None, "nothing matches a dock at submission time"
    assert staged[0].imported_at is None

    profile = await profile_for(db_session, location, create=False)
    assert profile is None, "no profile was created or touched"


async def test_an_import_refuses_a_submission_nobody_matched(db_session):
    """`location_id` comes from an operator. A submission carrying none is not
    importable, because the only identity a submitter supplies is one they
    chose."""
    submission = await stage_submission(db_session, _body(), submitted_from_ip=None)
    await db_session.commit()

    with pytest.raises(NotReviewed):
        await import_submission(db_session, submission)


async def test_an_import_refuses_a_dock_our_own_driver_surveyed(db_session):
    """Refused, not skipped.

    A skip that returned normally would be indistinguishable from an import
    that worked, and the reviewer who approved the row would never learn it
    changed nothing.
    """
    location = await _seed_location(db_session)
    profile = await profile_for(db_session, location, create=True)
    profile.landing_surface = "gravel"
    profile.surveyed_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert profile.is_surveyed

    submission = await stage_submission(db_session, _body(), submitted_from_ip=None)
    submission.location_id = location.id
    await db_session.commit()

    with pytest.raises(DockAlreadySurveyed):
        await import_submission(db_session, submission)

    await db_session.refresh(profile)
    assert profile.landing_surface == "gravel", "the driver's answer survived"


async def test_an_imported_dock_is_still_surveyed_by_the_next_driver(db_session):
    """The subtle one, and the expensive one to get wrong.

    Copying the answers is easy. Copying them in a way that convinces
    `dock_needs_survey` the dock is done would cost us the observation
    permanently - `is_surveyed` requires `surveyed_at`, and a stranger does not
    set it. The answers land as `stated`; only a driver's are `surveyed`.
    """
    location = await _seed_location(db_session)
    submission = await stage_submission(db_session, _body(), submitted_from_ip=None)
    submission.location_id = location.id
    await db_session.commit()

    await import_submission(db_session, submission)
    await db_session.commit()

    profile = await profile_for(db_session, location, create=False)
    assert profile is not None
    assert profile.landing_surface == "paved_lot", "the answers were copied"
    assert profile.stop_point == "loading_dock"
    assert profile.access_source == SOURCE_STATED
    assert profile.surveyed_at is None, "a stranger did not stand at this door"
    assert profile.is_surveyed is False, "our own drivers will still be asked"


async def test_a_submission_cannot_be_imported_twice(db_session):
    location = await _seed_location(db_session)
    submission = await stage_submission(db_session, _body(), submitted_from_ip=None)
    submission.location_id = location.id
    await db_session.commit()

    await import_submission(db_session, submission)
    await db_session.commit()

    with pytest.raises(AlreadyImported):
        await import_submission(db_session, submission)


# ---------------------------------------------------------------------------
# What the endpoint refuses, and what it gives away
# ---------------------------------------------------------------------------


async def test_an_unknown_answer_is_refused_rather_than_stored(db_session, real_redis_client):
    """These become `M5`'s labels. A stray value does not fail at write time -
    it fails months later as a class the model has one example of."""
    with pytest.raises(HTTPException) as exc_info:
        await submit_dock_log(
            _body(stop_point="helipad"), _FakeRequest(_ip()), session=db_session
        )
    assert exc_info.value.status_code == 422
    assert "stop_point" in str(exc_info.value.detail)

    assert (await db_session.execute(select(DockLogSubmission))).scalars().all() == []


async def test_a_submission_nobody_could_place_is_refused(db_session, real_redis_client):
    """No coordinates and no address. A survey nobody can attach to a place is
    worth nothing, and storing it costs a reviewer a row to dismiss."""
    with pytest.raises(HTTPException) as exc_info:
        await submit_dock_log(
            _body(lat=None, lng=None, submitted_address=None),
            _FakeRequest(_ip()),
            session=db_session,
        )
    assert exc_info.value.status_code == 422


async def test_a_submission_with_no_answers_is_refused(db_session, real_redis_client):
    """The driver app accepts an empty survey and returns 200, because there the
    submission itself is information - that driver stood at this dock and had
    nothing to say. Here it carries none."""
    with pytest.raises(HTTPException) as exc_info:
        await submit_dock_log(
            _body(stop_point=None, curb_access=None, landing_surface=None),
            _FakeRequest(_ip()),
            session=db_session,
        )
    assert exc_info.value.status_code == 422
    assert (await db_session.execute(select(DockLogSubmission))).scalars().all() == []


async def test_the_response_says_nothing_about_docks_we_already_hold(db_session, real_redis_client):
    """A reply that varied by whether the address is known would make this form
    a way to enumerate our customers' docks one guess at a time.

    Same address submitted twice - once with a matching `Location` in the
    database and once without - and the two responses are identical.
    """
    without = await submit_dock_log(_body(), _FakeRequest(_ip()), session=db_session)
    await _seed_location(db_session)
    with_match = await submit_dock_log(_body(), _FakeRequest(_ip()), session=db_session)

    assert without.model_dump() == with_match.model_dump()


# ---------------------------------------------------------------------------
# Matching is a suggestion
# ---------------------------------------------------------------------------


async def test_candidates_are_nearby_docks_and_exclude_merged_ones(db_session):
    """`IDN-2` points a merged location at a survivor. Attaching a survey to a
    row that is no longer the dock would bury it."""
    near = await _seed_location(db_session, lat=30.2673, lng=-97.7432, address="Near St")
    far = await _seed_location(db_session, lat=31.5, lng=-97.0, address="Far St")
    merged = await _seed_location(db_session, lat=30.2672, lng=-97.7431, address="Merged St")
    merged.merged_into_id = near.id
    await db_session.commit()

    submission = await stage_submission(db_session, _body(), submitted_from_ip=None)
    await db_session.commit()

    found = {loc.id for loc in await candidate_locations(db_session, submission)}
    assert near.id in found
    assert far.id not in found
    assert merged.id not in found


async def test_a_submission_without_coordinates_suggests_nothing(db_session):
    """Empty is a real answer: a survey with an address alone is matched by a
    person reading it, and returning everything would be worse than nothing."""
    await _seed_location(db_session)
    submission = await stage_submission(
        db_session, _body(lat=None, lng=None), submitted_from_ip=None
    )
    await db_session.commit()

    assert await candidate_locations(db_session, submission) == []


def test_validate_answers_counts_only_what_was_given():
    """Skipping is free and must stay free - the count is what the submitter is
    told, and it has to match what they actually answered."""
    assert validate_answers(_body(stop_point=None, curb_access=None, landing_surface=None)) == 0
    assert validate_answers(_body()) == 3
    assert validate_answers(_body(appointment_required=False)) == 4, "false is an answer"
