"""
Marginal economics for a gig job (docs/ROADMAP.md G7).

The problem this exists to solve, stated plainly: **the pilot's headline
rates are gross, not net.** $25.17/job, $1.75/mile and $70.74/hour engaged
all measure the job from pickup to dropoff. None of them count driving to
the pickup, and none count where the driver is stranded afterwards. An
accept advisor built on those numbers will confidently recommend
money-losing work - a $22 job eleven miles away is a loss dressed as a
median fare.

Three legs, and only the middle one is what the platform pays for:

    deadhead in    where the driver is now  ->  pickup     unpaid
    engaged        pickup                   ->  dropoff    paid
    reposition out dropoff -> back toward useful territory unpaid

The third leg is the one people forget. A job that ends in a dead zone
costs whatever it takes to get back to where offers exist, and that cost
belongs to this job, not to the next one.

EVERY RATE BELOW IS AN EXPLICIT PLACEHOLDER, same convention as
app/payroll/gig_pricing.py's PLACEHOLDER_ rates. They are reasoned defaults
for a van in a US metro, not measured LMX figures, and they should be
replaced with real per-vehicle cost data once there is any. The structure is
the durable part; the constants are not.
"""
from __future__ import annotations

from dataclasses import dataclass

# Pure geometry, reused rather than reimplemented. Its home in batch_queue is
# incidental - importing it here is not a dependency on the hold-queue logic,
# which is explicitly not involved in the gig path at all.
from app.batch_queue.clustering import miles_between
from app.travel import PLACEHOLDER_STOP_SERVICE_MINUTES, minutes_for_miles

# PLACEHOLDER. Fuel, maintenance, tyres and depreciation for a delivery van,
# per mile driven, paid or unpaid. Roughly the ballpark of the IRS business
# mileage rate, which is a reasonable stand-in for all-in vehicle cost until
# real numbers exist.
PLACEHOLDER_VEHICLE_COST_PER_MILE_CENTS = 67

# PLACEHOLDER. What an hour of the driver's time costs LMX, used to price
# time spent rather than distance covered - a job that pays well per mile but
# strands the driver in traffic is still a bad job.
PLACEHOLDER_DRIVER_COST_PER_HOUR_CENTS = 2500

# How much of the return trip is charged to this job. Not 100%: a driver
# finishing a job somewhere useful has not really incurred a full trip back,
# and charging the whole thing would reject almost everything. Not 0% either,
# which is the error the headline rates make. PLACEHOLDER.
REPOSITION_CHARGE_FRACTION = 0.5


@dataclass(frozen=True)
class MarginalEconomics:
    """What one job is really worth, given where the driver already is."""

    pay_cents: int

    deadhead_miles: float
    engaged_miles: float
    reposition_miles: float

    vehicle_cost_cents: int
    time_cost_cents: int
    total_cost_cents: int

    margin_cents: int
    total_minutes: float

    @property
    def is_profitable(self) -> bool:
        return self.margin_cents > 0

    @property
    def effective_hourly_cents(self) -> int | None:
        """Pay per hour across the WHOLE job, deadhead included.

        This is the number to compare against the pilot's $70.74/hour
        engaged - and it will be lower, often much lower, because the pilot
        figure excludes exactly the unpaid legs counted here. A large gap
        between the two is the finding, not an error.
        """
        if self.total_minutes <= 0:
            return None
        return int(round(self.pay_cents / (self.total_minutes / 60.0)))


def evaluate_marginal_economics(
    *,
    pay_cents: int,
    driver_lat: float,
    driver_lng: float,
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    reposition_lat: float | None = None,
    reposition_lng: float | None = None,
) -> MarginalEconomics:
    """Cost this job from where the driver actually is.

    `reposition_*` is where the driver would want to end up - typically the
    pickup of their next committed job, or their operating anchor if this is
    the last one. When omitted, the return leg is measured back to the
    driver's current position, which is the honest default: absent other
    information, the assumption is they came from somewhere useful and will
    need to get back to it.
    """
    deadhead_miles = miles_between(driver_lat, driver_lng, pickup_lat, pickup_lng)
    engaged_miles = miles_between(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)

    return_lat = reposition_lat if reposition_lat is not None else driver_lat
    return_lng = reposition_lng if reposition_lng is not None else driver_lng
    raw_reposition = miles_between(dropoff_lat, dropoff_lng, return_lat, return_lng)
    reposition_miles = raw_reposition * REPOSITION_CHARGE_FRACTION

    total_miles = deadhead_miles + engaged_miles + reposition_miles
    total_minutes = minutes_for_miles(total_miles) + (2 * PLACEHOLDER_STOP_SERVICE_MINUTES)

    vehicle_cost = int(round(total_miles * PLACEHOLDER_VEHICLE_COST_PER_MILE_CENTS))
    time_cost = int(round((total_minutes / 60.0) * PLACEHOLDER_DRIVER_COST_PER_HOUR_CENTS))
    total_cost = vehicle_cost + time_cost

    return MarginalEconomics(
        pay_cents=pay_cents,
        deadhead_miles=round(deadhead_miles, 2),
        engaged_miles=round(engaged_miles, 2),
        reposition_miles=round(reposition_miles, 2),
        vehicle_cost_cents=vehicle_cost,
        time_cost_cents=time_cost,
        total_cost_cents=total_cost,
        margin_cents=pay_cents - total_cost,
        total_minutes=round(total_minutes, 1),
    )
