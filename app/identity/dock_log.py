"""Staging a public dock survey, and importing one a person has approved (`DRV-7`).

Two functions, and the distance between them is the point.

`stage_submission` is reachable by anyone on the internet and writes only to
`dock_log_submissions`, a table nothing reads for any operational purpose.
`import_submission` writes to `receiver_profiles` and is reachable only by an
authenticated operator who has looked at the row.

`ROADMAP_1.5.md`'s `DRV-7` row requires exactly that gap: *"a stranger's
submission must never write to `receiver_profiles` directly — it is an
unauthenticated public surface writing into the layer `M5` trains on."*

## The three rules the import honours

The row states them, and each one is a way this could quietly corrupt the
training set rather than fail:

**Match a `Location` by coordinates.** Not by name and not by address. A
submitter chooses both of those, so matching on either lets anybody attach a
survey to a dock of their choosing by typing its name. Coordinates can be
falsified too, which is why the match is a *suggestion to a reviewer* and never
automatic — `location_id` is set by a person, and this function refuses a
submission that has none.

**Write only to a dock none of our own drivers has surveyed.** `is_surveyed`
means a driver stood there. A stranger's answers are evidence; a driver's are
observation, and `SOURCE_SURVEYED` already encodes that distinction everywhere
else in this module.

**Never overwrite one that has.** The second rule stated as a refusal rather
than a filter, because the two fail differently: a filter that silently skips
looks identical to an import that worked, and a reviewer would never learn that
the row they approved changed nothing.

## Why a rejected submission is kept

`rejected_reason` rather than a delete. A pattern of plausible junk from one
address is the only evidence that this surface is being abused, and it is
invisible if each row is removed as it is dismissed. The rows are inert either
way — nothing reads an unimported submission.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.profile import profile_for
from app.models.dock_log_submission import DockLogSubmission
from app.models.location import Location
from app.models.receiver_profile import (
    CURB_ACCESS,
    DOOR_PATHS,
    LANDING_SURFACES,
    OBSTRUCTIONS,
    SOURCE_STATED,
    STOP_POINTS,
    WALK_DISTANCE_BANDS,
    WHO_RECEIVES,
)

# The eight answer fields and the vocabulary each is checked against. One list,
# used by the validator, the copier and the counter, so a ninth question added
# to the form cannot be silently dropped by the import.
ANSWER_VOCABULARIES: tuple[tuple[str, tuple[str, ...] | None], ...] = (
    ("stop_point", STOP_POINTS),
    ("curb_access", CURB_ACCESS),
    ("walk_distance_band", WALK_DISTANCE_BANDS),
    ("door_path", DOOR_PATHS),
    ("obstruction", OBSTRUCTIONS),
    ("who_receives", WHO_RECEIVES),
    ("landing_surface", LANDING_SURFACES),
    # A boolean has no vocabulary. Named here anyway so the tuple is the whole
    # answer set rather than most of it.
    ("appointment_required", None),
)

# How close a candidate dock has to be to be worth showing a reviewer. About
# 150 m at these latitudes - loose, because browser geolocation indoors is
# routinely off by a hundred metres and a suggestion that is merely nearby is
# still useful to somebody who can read the address. Tight enough that a
# submitter cannot reach across a city.
MATCH_RADIUS_DEGREES = 0.0015


class VocabularyError(ValueError):
    """An answer outside its vocabulary. Refused, never stored."""


class NotReviewed(Exception):
    """Nobody has matched this submission to a dock."""


class AlreadyImported(Exception):
    """This submission has already been applied to a profile."""


class DockAlreadySurveyed(Exception):
    """One of our own drivers has surveyed this dock. A stranger does not overwrite it."""


def validate_answers(body: object) -> int:
    """Check every answer against its vocabulary and count the ones present.

    Raises before anything is written, and names the offending field. These
    become `M5`'s labels: a stray value does not fail at write time, it fails
    months later as a class the model has one example of.
    """
    answered = 0
    for field, allowed in ANSWER_VOCABULARIES:
        value = getattr(body, field, None)
        if value is None:
            continue
        answered += 1
        if allowed is not None and value not in allowed:
            raise VocabularyError(f"{field}={value!r} is not one of {allowed}")
    return answered


async def stage_submission(
    session: AsyncSession, body: object, *, submitted_from_ip: str | None
) -> DockLogSubmission:
    """Record a public survey. Writes to the staging table and nothing else.

    No `Location` lookup happens here, and that is deliberate rather than
    lazy. Matching at submission time would mean the response could differ
    depending on whether we already hold a dock, which turns this form into a
    way to enumerate our customers' addresses one guess at a time.
    """
    answered = validate_answers(body)
    submission = DockLogSubmission(
        business_name=body.business_name,
        submitted_address=body.submitted_address,
        lat=body.lat,
        lng=body.lng,
        submitted_from_ip=submitted_from_ip,
        **{field: getattr(body, field) for field, _ in ANSWER_VOCABULARIES},
    )
    session.add(submission)
    await session.flush()
    # `answered` is returned via the caller rather than stored - the model
    # computes it from the columns so the two cannot disagree.
    assert submission.answered_count == answered
    return submission


async def candidate_locations(
    session: AsyncSession, submission: DockLogSubmission, *, limit: int = 5
) -> list[Location]:
    """Docks near enough to be worth a reviewer's glance. A suggestion, never a match.

    Empty when the submission carries no coordinates, which is a real outcome:
    a survey with an address alone is matched by a person reading it, and
    returning everything would be worse than returning nothing.

    A bounding box rather than a true distance, and deliberately so — this
    orders a shortlist for a human, and the error a box introduces at the
    corners is smaller than the error browser geolocation already carries.
    Merged locations are excluded: `IDN-2` points them at a survivor, and
    attaching a survey to a row that is no longer the dock would bury it.
    """
    if submission.lat is None or submission.lng is None:
        return []
    result = await session.execute(
        select(Location)
        .where(
            Location.merged_into_id.is_(None),
            Location.lat.is_not(None),
            Location.lng.is_not(None),
            Location.lat.between(
                submission.lat - MATCH_RADIUS_DEGREES, submission.lat + MATCH_RADIUS_DEGREES
            ),
            Location.lng.between(
                submission.lng - MATCH_RADIUS_DEGREES, submission.lng + MATCH_RADIUS_DEGREES
            ),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def import_submission(
    session: AsyncSession,
    submission: DockLogSubmission,
    *,
    reviewed_by_ops_user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> DockLogSubmission:
    """Apply a reviewed submission to its dock's profile.

    Refuses rather than skips, in all three cases. A skip that returned
    normally would be indistinguishable from an import that worked, and the
    reviewer who approved the row would never learn it changed nothing.

    Answers are written as `SOURCE_STATED`, never `SOURCE_SURVEYED`. Somebody
    told us this; we did not see it. That distinction is the same one the
    inherited-dwell columns exist to preserve, and collapsing it here would
    make a stranger's claim indistinguishable from our own driver's observation
    in the data `M5` learns from.
    """
    if submission.location_id is None:
        raise NotReviewed("Nobody has matched this submission to a dock")
    if submission.imported_at is not None:
        raise AlreadyImported("This submission has already been applied")

    location = await session.get(Location, submission.location_id)
    if location is None:
        raise NotReviewed("The matched dock no longer exists")

    profile = await profile_for(session, location, create=True)
    if profile.is_surveyed:
        raise DockAlreadySurveyed(
            "One of our own drivers has surveyed this dock - a public submission does not overwrite it"
        )

    for field, _allowed in ANSWER_VOCABULARIES:
        value = getattr(submission, field)
        if value is not None:
            setattr(profile, field, value)

    profile.access_source = SOURCE_STATED
    # Not `surveyed_at`. That column means a driver of ours stood there, and it
    # is what `dock_needs_survey` reads to decide whether to ask again - so
    # stamping it here would stop us ever surveying a dock a stranger described.
    submission.imported_at = now or datetime.now(timezone.utc)
    if submission.reviewed_at is None:
        submission.reviewed_at = submission.imported_at
    if reviewed_by_ops_user_id is not None:
        submission.reviewed_by_ops_user_id = reviewed_by_ops_user_id
    await session.flush()
    return submission
