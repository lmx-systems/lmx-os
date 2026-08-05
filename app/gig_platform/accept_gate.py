"""
The accept-gate (docs/ROADMAP.md G4) - take it or skip it.

The commercially interesting piece of the gig path. A platform offer lives
for roughly 45 seconds, so this has a budget of under ten, and it runs on
whatever the driver's phone captured - which may be a collapsed card missing
the dropoff entirely.

CHECKS ARE ORDERED CHEAPEST-FIRST AND SHORT-CIRCUIT. That ordering is the
design, not an optimization detail:

  1. reachability      can the driver physically get to the pickup in time?
  2. self-consistency  is the job possible at all, ignoring everyone else?
  3. placement         does slotting it in break a window already promised?
  4. capacity          is there room in the vehicle?
  5. economics         does it make money once deadhead is counted? (G7)

The first two need only the offer itself and the driver's position, and they
reject most offers - including, notably, the one real offer we have a
screenshot of, which surfaced with four minutes left of a seventy-minute
pickup window and fails at step 1. Steps 3-5 are the expensive ones and are
never reached for an offer that was never feasible.

Step 3 is also the one with teeth beyond economics: a late delivery caused
by an LMX OS route is a Service Failure on the *driver's own* platform
account, counting toward their deactivation risk (G13). With three drivers,
losing one is a third of capacity. So this gate must never accept into a
plan it cannot deliver, even when the money looks good - the hard-stop rule
is a policy decision, and the code implements it rather than weighing it.

A sixth check, a sibling bonus for jobs sharing a base reference, belongs
here per the roadmap. It is deliberately absent: parsing refs like
"S4588150.002-HOU1" is G8's detector, and guessing the format here would
bake in an assumption that item should own. The seam is marked below.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.gig_platform.economics import (
    MarginalEconomics,
    evaluate_marginal_economics,
    minutes_for_miles,
)
from app.models.gig_job import GigJob
from app.batch_queue.clustering import miles_between

# Verdict reason codes. Stable strings rather than prose because they are
# training data: why we passed on a job is as informative as why we took it,
# and free text would make that unqueryable.
UNREACHABLE = "unreachable"
IMPOSSIBLE_WINDOWS = "impossible_windows"
BREAKS_COMMITMENT = "breaks_commitment"
OVER_CAPACITY = "over_capacity"
UNPROFITABLE = "unprofitable"
NOT_SEQUENCEABLE = "not_sequenceable"
ACCEPTABLE = "acceptable"

# Safety margin applied to every arrival estimate. Drive times here come from
# a straight-line estimate, not a real routing API (E1 is blocked on a Google
# Cloud account), so arriving "exactly on time" in the model means arriving
# late in reality. Being conservative costs a marginal job; being optimistic
# costs a driver's platform standing.
ARRIVAL_BUFFER_MINUTES = 5.0


@dataclass(frozen=True)
class AcceptVerdict:
    accept: bool
    reason: str
    detail: str
    economics: MarginalEconomics | None = None


def _arrival_minutes(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> float:
    return minutes_for_miles(miles_between(from_lat, from_lng, to_lat, to_lng)) + ARRIVAL_BUFFER_MINUTES


def evaluate_offer(
    *,
    offer: GigJob,
    driver_lat: float,
    driver_lng: float,
    committed: list[GigJob],
    capacity_units: int,
    now: datetime | None = None,
) -> AcceptVerdict:
    """Decide whether this driver should take this offer.

    `committed` is everything the driver has already accepted and not yet
    delivered - the promises this offer has to fit around.
    """
    now = now or datetime.now(timezone.utc)

    # ---- 1. Reachability -------------------------------------------------
    # Can the driver be at the pickup before the window shuts? Uses only the
    # offer and the driver's position, so it works on a collapsed-card
    # capture and kills most offers before anything expensive runs.
    if offer.pickup_lat is None or offer.pickup_lng is None:
        return AcceptVerdict(
            accept=False,
            reason=UNREACHABLE,
            detail="The offer has no pickup coordinates, so reachability can't be established.",
        )

    eta_minutes = _arrival_minutes(
        driver_lat, driver_lng, float(offer.pickup_lat), float(offer.pickup_lng)
    )
    arrival = now + timedelta(minutes=eta_minutes)
    if arrival > offer.pickup_window_close:
        late_by = (arrival - offer.pickup_window_close).total_seconds() / 60.0
        return AcceptVerdict(
            accept=False,
            reason=UNREACHABLE,
            detail=(
                f"Pickup closes in "
                f"{(offer.pickup_window_close - now).total_seconds() / 60.0:.0f} min "
                f"and the drive is about {eta_minutes:.0f} min - roughly "
                f"{late_by:.0f} min short."
            ),
        )

    # ---- 2. Self-consistency --------------------------------------------
    # Ignoring every other job: is this one even possible? A dropoff window
    # that closes before you could drive there from the pickup is a job
    # nobody could complete.
    if offer.dropoff_lat is None or offer.dropoff_lng is None:
        # A collapsed-card capture. Enough to have got this far, not enough
        # to place or price - so this is a "can't tell", surfaced honestly
        # rather than guessed either way.
        return AcceptVerdict(
            accept=False,
            reason=NOT_SEQUENCEABLE,
            detail="Reachable, but the dropoff address is missing - expand the offer card to decide.",
        )

    leg_minutes = minutes_for_miles(
        miles_between(
            float(offer.pickup_lat), float(offer.pickup_lng),
            float(offer.dropoff_lat), float(offer.dropoff_lng),
        )
    )
    if offer.dropoff_window_close is not None:
        earliest_pickup = max(arrival, offer.pickup_window_open)
        if earliest_pickup + timedelta(minutes=leg_minutes) > offer.dropoff_window_close:
            return AcceptVerdict(
                accept=False,
                reason=IMPOSSIBLE_WINDOWS,
                detail=(
                    f"The {leg_minutes:.0f} min delivery leg doesn't fit between the pickup "
                    "and the dropoff deadline."
                ),
            )

    # ---- 3. Placement ----------------------------------------------------
    # Does taking this break something already promised? This is the G13
    # hard stop: a missed window lands on the driver's own platform standing,
    # so an offer that endangers a commitment is refused regardless of pay.
    conflict = _first_broken_commitment(
        offer=offer, committed=committed, now=now, arrival=arrival, leg_minutes=leg_minutes
    )
    if conflict is not None:
        return AcceptVerdict(
            accept=False,
            reason=BREAKS_COMMITMENT,
            detail=conflict,
        )

    # ---- 4. Capacity -----------------------------------------------------
    # One unit per job: the platforms describe parcel-scale work, and nothing
    # in an offer payload carries a real volume figure to do better with.
    in_possession = [j for j in committed if j.status in ("accepted", "picked_up")]
    if len(in_possession) + 1 > capacity_units:
        return AcceptVerdict(
            accept=False,
            reason=OVER_CAPACITY,
            detail=f"Already holding {len(in_possession)} of {capacity_units} slots.",
        )

    # ---- 5. Marginal economics (G7) --------------------------------------
    # Only now, having established the job is possible, is it worth costing.
    # The next committed pickup is where the driver wants to end up; absent
    # one, economics falls back to their current position.
    next_pickup = _next_committed_pickup(committed, after=offer.dropoff_window_open or arrival)
    economics = evaluate_marginal_economics(
        pay_cents=offer.pay_cents,
        driver_lat=driver_lat,
        driver_lng=driver_lng,
        pickup_lat=float(offer.pickup_lat),
        pickup_lng=float(offer.pickup_lng),
        dropoff_lat=float(offer.dropoff_lat),
        dropoff_lng=float(offer.dropoff_lng),
        reposition_lat=next_pickup[0] if next_pickup else None,
        reposition_lng=next_pickup[1] if next_pickup else None,
    )

    if not economics.is_profitable:
        return AcceptVerdict(
            accept=False,
            reason=UNPROFITABLE,
            detail=(
                f"Pays ${offer.pay_cents / 100:.2f} against about "
                f"${economics.total_cost_cents / 100:.2f} of cost - "
                f"{economics.deadhead_miles:.1f} mi of that is unpaid deadhead."
            ),
            economics=economics,
        )

    # ---- 6. Sibling bonus (G8) -------------------------------------------
    # SEAM, deliberately not implemented here. A job sharing a base reference
    # with one already held collapses the marginal cost of the second, and is
    # the cheapest real batching signal available - but extracting that base
    # from a platform-specific ref format is G8's detector, not a guess to
    # bake in here. When it lands, it raises a marginal `accept` rather than
    # overturning any rejection above, since none of those are about money.

    return AcceptVerdict(
        accept=True,
        reason=ACCEPTABLE,
        detail=(
            f"About ${economics.margin_cents / 100:.2f} margin over "
            f"{economics.total_minutes:.0f} min, including "
            f"{economics.deadhead_miles:.1f} mi deadhead."
        ),
        economics=economics,
    )


def _first_broken_commitment(
    *,
    offer: GigJob,
    committed: list[GigJob],
    now: datetime,
    arrival: datetime,
    leg_minutes: float,
) -> str | None:
    """The first already-promised window this offer would endanger.

    Deliberately conservative and deliberately simple: it asks whether
    finishing this offer still leaves time to reach each committed pickup,
    rather than attempting a full re-sequence. A real insertion optimizer is
    G5's job and needs the solver (blocked on E1); this runs in a driver's
    45-second offer window and errs toward refusing.
    """
    completion = max(arrival, offer.pickup_window_open) + timedelta(minutes=leg_minutes)

    for job in sorted(committed, key=lambda j: j.pickup_window_open):
        if job.status not in ("accepted", "offered"):
            continue
        if job.pickup_lat is None or job.pickup_lng is None:
            continue
        if job.pickup_window_close <= now:
            continue

        travel = _arrival_minutes(
            float(offer.dropoff_lat), float(offer.dropoff_lng),
            float(job.pickup_lat), float(job.pickup_lng),
        )
        if completion + timedelta(minutes=travel) > job.pickup_window_close:
            return (
                f"Would land at {job.source_platform} job {job.platform_job_ref} "
                "after its pickup window closes."
            )
    return None


def _next_committed_pickup(
    committed: list[GigJob], *, after: datetime
) -> tuple[float, float] | None:
    for job in sorted(committed, key=lambda j: j.pickup_window_open):
        if job.pickup_window_open >= after and job.pickup_lat is not None and job.pickup_lng is not None:
            return float(job.pickup_lat), float(job.pickup_lng)
    return None
