"""
How long a journey takes, in one place.

**Four surfaces show a customer or a driver a time derived from these numbers**, and
they must never disagree: the gig accept-gate deciding whether a job pays, the client
portal's delivery estimate on the confirmation screen, the recipient's tracking page,
and `app/delivery/eta.py`'s per-stop ETAs. A driver, a recipient and a counter person
reading three different arrival times for the same delivery is the failure this file
prevents.

**Why it moved here.** It lived in `app/gig_platform/economics.py`, which is a demand
path - so the dispatch core depended on one particular way of getting work, for a
primitive that has nothing to do with gig platforms. The sharing was always right; the
address was wrong, and `tests/test_architecture_boundaries.py` had a test whose only job
was to stop that dependency growing. A top-level module rather than a package, matching
`app/hub_calendar.py` and `app/client_ip.py` - the established shape here for a small
thing everything may read.

What stayed behind in `economics.py` is genuinely gig economics: vehicle cost per mile,
driver cost per hour, and the reposition-charge fraction. Those answer "is this job
worth taking", which is a question only that demand path asks.

**These are placeholders, and the honest description is a guess with a shared
denominator.** A real travel-time model means the routing integration making live calls
(`E1`), after which `Stop.planned_eta` can carry the solver's road-network intervals
instead of this arithmetic - see `app/delivery/eta.py`'s closing note. Until then the
value of this module is not accuracy but consistency.

Not moved, deliberately: `miles_between` still lives in `app/batch_queue/clustering.py`
beside `cluster_members`, which uses it. It is used *with* `minutes_for_miles` at every
call site and would sit naturally here, but it has seven importers and no boundary
problem behind it - core owning a geometry helper that the edge reads is allowed and
harmless. Worth doing on a day when something else is already touching those files.
"""
from __future__ import annotations

# PLACEHOLDER. Average speed used to turn miles into minutes. Metro surface streets with
# stops, deliberately not highway speed.
PLACEHOLDER_AVERAGE_SPEED_MPH = 18.0

# PLACEHOLDER. Fixed time on the ground at each end - parking, finding the counter,
# waiting, paperwork, proof of delivery.
PLACEHOLDER_STOP_SERVICE_MINUTES = 8.0


def minutes_for_miles(miles: float) -> float:
    """Drive time at the placeholder average speed.

    A crude estimate on purpose. The real ETA source is Google Route Optimization, which
    has never made one live call (`E1`) - and the gig accept-gate needs an answer in
    milliseconds regardless, so a straight-line estimate is the right shape even once a
    real routing API exists. Same "unconfigured -> usable stub" convention as every other
    external dependency in this codebase.

    Guards against a zero or negative speed rather than raising: this is called on the
    path that quotes a customer, and a misconfigured constant should not take the
    confirmation screen down with it. Zero minutes is visibly wrong to a reader in a way
    an exception is not.
    """
    if PLACEHOLDER_AVERAGE_SPEED_MPH <= 0:
        return 0.0
    return (miles / PLACEHOLDER_AVERAGE_SPEED_MPH) * 60.0
