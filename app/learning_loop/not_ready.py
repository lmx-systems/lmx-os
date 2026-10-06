"""Infer "the order wasn't ready when the driver arrived" from a pickup's dwell.

The learning loop (`detection.py`) reads `hold_window_too_short` flags and, until
this module, nothing wrote them: the driver app has no control for the flag, so
the loop's detector had never fired and the ops scorecard's "held wrong" rate
was the absence of an instrument. The product decision of 5 October 2026 is to
derive the flag rather than wait for a button.

The derivation is one comparison. A completed PICKUP stop whose dwell was far
longer than its dock's usual dwell is a driver who stood at the counter waiting
for an order that was not ready - which is exactly what "the hold window was
too short" means from the shop's side. The dock's usual dwell is `IDN-4`'s
`ReceiverProfile`, recomputed nightly from our own stops, so the baseline is
this counter's and not a pooled one; `MODEL_AND_DATA_BRIEF.md` §M1 is blunt
that a pooled number is wrong about every dock.

Every flag written here carries `source = 'inferred'` and no driver. A reader
that means "a driver reported this" (`app/reporting/exceptions.py`) filters on
the source; one that means "the window was wrong" (`app/reporting/operations.py`,
the loop itself) counts both. The two are not the same claim, and the column is
what keeps them apart.
"""
from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hub_calendar import hub_day_bounds
from app.identity.profile import _MIN_SAMPLES_FOR_P90, completed_dwell_rows
from app.learning_loop.detection import HOLD_TOO_SHORT_FLAG
from app.models.hub import Hub
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.stop import FLAG_SOURCE_INFERRED, Stop, StopFlag

logger = structlog.get_logger(__name__)

# Thresholds. A starting point taken from the brief's scale figures - the design
# partner's median counter dwell is about 2 minutes and its p90 about 13 - and
# nobody has measured them against live data yet. Recalibrate against the first
# weeks of real flags before trusting a rate built on them.
#
# A dock's own p90 is the baseline. The profile withholds one below
# `_MIN_SAMPLES_FOR_P90` stops, and a dock without one is not judged at all:
# there is no standing to call a visit long against a figure we don't have.
# The baseline never drops below this floor either: at a dock that usually
# takes 40 seconds, a 90-second visit is a slow visit, not a wait.
MIN_WAIT_SECONDS = 300
# Above this a dwell is a parking record - the driver went to lunch, the app
# stayed on "arrived" overnight - not a wait at a counter, and flagging it would
# teach the loop something that did not happen.
MAX_WAIT_SECONDS = 4 * 3600
# How many days back the nightly job judges. Yesterday is the day that matters,
# and it is judged before the dwell refresh folds it into the baseline; the
# rest is catch-up, so a night the job missed (the process down across the 2am
# hour, a transient error at the commit) does not lose that day's flags for
# good. Re-judging a day is free: a stop already flagged is skipped, and the
# unique index refuses a duplicate regardless.
LOOKBACK_DAYS = 7
_NOTE_MAX_LENGTH = 500


async def flag_pickups_that_waited(session: AsyncSession, *, hub_id, day: date) -> int:
    """Flag yesterday's pickups where the driver waited far longer than the dock
    usually takes. Returns how many flags were written.

    `day` is a hub-local calendar day, bounded the way `surveys_recorded_today`
    bounds one: a hub's night ends at its own midnight, not UTC's.

    Idempotent. A stop already carrying an inferred flag of this type is skipped
    here, and `uq_stop_flags_inferred_once` refuses a second one regardless.
    """
    # The scheduler passes the hub id as a string, as it does to every other step.
    hub_id = hub_id if isinstance(hub_id, uuid.UUID) else uuid.UUID(str(hub_id))
    hub = await session.get(Hub, hub_id)
    if hub is None:
        return 0
    start, end = hub_day_bounds(hub, day)

    rows = completed_dwell_rows()
    candidates = (
        await session.execute(
            select(Stop.id, rows.c.dwell, ReceiverProfile)
            .select_from(Stop)
            .join(Route, Stop.route_id == Route.id)
            .join(rows, rows.c.stop_id == Stop.id)
            .join(ReceiverProfile, ReceiverProfile.location_id == rows.c.location_id)
            .where(
                Route.hub_id == hub_id,
                Stop.stop_type == "pickup",
                Stop.completed_at >= start,
                Stop.completed_at < end,
                # No standing to judge a visit against a dock we barely know.
                ReceiverProfile.dwell_sample_count >= _MIN_SAMPLES_FOR_P90,
                ReceiverProfile.dwell_p90_seconds.is_not(None),
            )
        )
    ).all()
    if not candidates:
        logger.info("not_ready_inference_completed", hub_id=str(hub_id), day=day.isoformat(), flagged=0)
        return 0

    already = set(
        await session.scalars(
            select(StopFlag.stop_id).where(
                StopFlag.stop_id.in_([stop_id for stop_id, _, _ in candidates]),
                StopFlag.flag_type == HOLD_TOO_SHORT_FLAG,
                StopFlag.source == FLAG_SOURCE_INFERRED,
            )
        )
    )

    written = 0
    for stop_id, dwell, profile in candidates:
        if stop_id in already:
            continue
        dwell = int(dwell)
        if dwell > MAX_WAIT_SECONDS or dwell <= max(profile.dwell_p90_seconds, MIN_WAIT_SECONDS):
            continue
        observed = (
            f"{profile.dwell_observed_at:%Y-%m-%d}"
            if profile.dwell_observed_at is not None
            else "unknown"
        )
        note = (
            f"inferred: waited {dwell}s at a dock that usually takes "
            f"p50 {profile.dwell_p50_seconds}s / p90 {profile.dwell_p90_seconds}s "
            f"(dock figures as of {observed})"
        )
        session.add(
            StopFlag(
                stop_id=stop_id,
                flag_type=HOLD_TOO_SHORT_FLAG,
                source=FLAG_SOURCE_INFERRED,
                created_by_driver_id=None,
                note=note[:_NOTE_MAX_LENGTH],
            )
        )
        written += 1

    if written:
        await session.commit()
    logger.info(
        "not_ready_inference_completed", hub_id=str(hub_id), day=day.isoformat(), flagged=written
    )
    return written


__all__ = ["LOOKBACK_DAYS", "MAX_WAIT_SECONDS", "MIN_WAIT_SECONDS", "flag_pickups_that_waited"]
