"""Whether to ask a driver about this dock, and recording what they say (`DRV-7`).

The writer `IDN-4`'s surveyed columns never had. `app/identity/profile.py` holds
everything `M5` needs to learn from - `landing_surface`, `curb_access`,
`door_path`, `obstruction`, `who_receives`, plus the access facts - every one
validated against a fixed vocabulary, tested, and written by nothing:
`set_access` and `set_autonomy_fit` have sat in `tests/test_no_new_orphans.py`
as *"profile field with no live writer"* since they were added.

`MODEL_AND_DATA_BRIEF.md` §6 calls the survey weeks of fieldwork regardless of
volume. It is weeks of fieldwork only if somebody has to go and do it, and our
drivers are already standing at every one of those docks.

## The decision lives here, not on the phone

`THE_DRIVER_APP.md` §6: *do not restate a condition another module owns.* Both
bugs that document records came from two modules holding one rule and drifting.
So the server computes `dock_needs_survey` and the app renders what it is told.

Three reasons to ask, and they are all cheap to check:

**Never surveyed, or surveyed too long ago.** A dock's door moves, its gate gets
a code, its dock crew changes. A year is the stated interval.

**Not more than three in a shift.** A first week at a new customer would
otherwise turn every stop into paperwork, and a driver who is asked eight
questions at all twenty stops stops answering carefully by the fourth. The
remaining docks wait for the next visit, which is what a standing survey is for.

**Only at a delivery that actually happened.** Never a pickup - the dock being
surveyed is the *receiver's*, and a shop's own yard is not it. Never a failed
stop, because a driver who could not deliver may not have reached the door.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.hub_calendar import hub_day_bounds, hub_local_date
from app.identity.profile import profile_for
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.order import Order
from app.models.receiver_profile import ReceiverProfile
from app.models.stop import Stop, StopOrder

# How long a dock's answers are trusted before somebody is asked again.
RESURVEY_AFTER_DAYS = 365

# Per driver, per service date. Enough that a new customer's docks get covered
# over a few shifts; few enough that no shift becomes a survey round.
MAX_SURVEYS_PER_SHIFT = 3


async def location_for_stop(session: AsyncSession, stop: Stop) -> Location | None:
    """The dock this stop delivers to, or None when the record cannot say.

    The order's `delivery_location_id`, which intake resolves from the delivery
    address through `IDN-1`. This went through the order's shop before, and the
    shop is the *pickup*: every survey taken at a delivery door was filed under
    the distributor's yard, and asked for again there a year later.

    None is a real answer and the caller must treat it as one: an address that
    named no place gets no dock deliberately, because the alternative was every
    such address collapsing into one shared fictional dock. Orders from before
    the column existed have none either.
    """
    location_id = (
        await session.execute(
            select(Order.delivery_location_id)
            .join(StopOrder, StopOrder.order_id == Order.id)
            .where(StopOrder.stop_id == stop.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if location_id is None:
        return None
    return await session.get(Location, location_id)


async def surveys_recorded_today(
    session: AsyncSession, driver_id: uuid.UUID, *, on: date | None = None
) -> int:
    """How many docks this driver has already surveyed on this service date.

    Counted from `surveyed_at`/`surveyed_by_driver_id` rather than from a
    separate tally, so the cap cannot drift from the thing it is capping.

    The service date is the driver's hub's, and `on` is read on its clock. It
    used to be UTC's, which ends at 4 or 5pm in Los Angeles: the cap reset
    mid-shift, and a driver could meet it twice in one working day.
    """
    driver = await session.get(Driver, driver_id)
    hub = await session.get(Hub, driver.hub_id) if driver is not None else None
    if hub is not None:
        start, end = hub_day_bounds(
            hub, on or hub_local_date(hub, datetime.now(timezone.utc))
        )
    else:
        # No hub to read a clock from, so UTC's day, as before.
        start = datetime.combine(
            on or datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc
        )
        end = start + timedelta(days=1)
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ReceiverProfile)
            .where(
                ReceiverProfile.surveyed_by_driver_id == driver_id,
                ReceiverProfile.surveyed_at >= start,
                ReceiverProfile.surveyed_at < end,
            )
        )
        or 0
    )


async def dock_needs_survey(
    session: AsyncSession,
    stop: Stop,
    driver_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether to put the survey in front of this driver at this stop.

    `now` is injectable for the same reason `evaluate_driver_documents` takes
    `today`: a year is a date boundary, and a test that happens to run on the
    day a fixture ages out would pass or fail depending on the clock.
    """
    if stop.stop_type != "dropoff":
        return False
    if stop.status == "failed":
        return False

    location = await location_for_stop(session, stop)
    if location is None:
        return False

    if await surveys_recorded_today(session, driver_id) >= MAX_SURVEYS_PER_SHIFT:
        return False

    profile = await profile_for(session, location, create=False)
    if profile is None or not profile.is_surveyed or profile.surveyed_at is None:
        # `is_surveyed` already requires `surveyed_at`; the last clause restates it
        # for the comparison below.
        return True

    at = now or datetime.now(timezone.utc)
    return profile.surveyed_at < at - timedelta(days=RESURVEY_AFTER_DAYS)
