"""A synthetic delivery day in Austin, with the answers known in advance.

The real export cannot exercise the sensor. It has no street addresses and no
coordinates (see `app/ingestion/adapters/dispatch_export.py`), so nothing in it
can place a geofence, and its minute-resolution timestamps are the very defect
`DRV-1` exists to replace. This generates the thing it cannot: routes with real
geography, stops with a true arrival and departure, and both the crossings a
phone would have recorded and the taps a driver would have made.

**Why it is worth having.** Because the truth is planted, the measurement can be
checked rather than merely run. `refresh_dwell_statistics` should recover the
dwell we generated; `measure_geofence_calibration` should recover the lead we
generated from the radius we chose. A pipeline that runs without error over real
data proves only that it runs.

**What it is not.** It is not evidence about the business. No synthetic dataset
closes gap 2 - a measured cost-per-drop delta needs real orders and a control
arm, and a number produced from data we invented would be worth less than no
number. This is for engineering confidence in the chain, nothing more.

**Austin on purpose.** The design partner is nowhere near Texas. Synthetic
geography in a public repository should be somewhere the customer is not, so a
fixture can never be mistaken for their account book (CLAUDE.md's naming rule).

Deterministic: same seed, same world, so a failing test can be re-run.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Real Austin postcodes with approximate centroids. Public geography; the
# jitter below spreads stops across each one rather than stacking them on a
# point, which matters because a geofence radius is metres and a centroid is
# not a building.
AUSTIN_ZIPS: tuple[tuple[str, float, float], ...] = (
    ("78701", 30.2711, -97.7437),  # downtown
    ("78702", 30.2620, -97.7143),
    ("78703", 30.2915, -97.7663),
    ("78704", 30.2450, -97.7650),
    ("78717", 30.4938, -97.7530),
    ("78745", 30.2072, -97.7996),
    ("78751", 30.3080, -97.7240),
    ("78753", 30.3810, -97.6760),
    ("78756", 30.3200, -97.7390),
    ("78758", 30.3830, -97.7090),
)

# Roughly 1 km of jitter. One degree of latitude is ~111 km, so this spreads a
# zip's stops over a believable few streets rather than a single address.
_JITTER_DEG = 0.009

# Dwell, drawn to match the finding the roadmap is built on: median 2.1 minutes
# at the design partner, with a third of stops under 60 seconds. A lognormal
# with these parameters reproduces both - median exp(mu)=126s, and
# P(X<60)~=0.33. `test_austin_world.py` asserts it, so the distribution cannot
# drift away from the finding without a test noticing.
_DWELL_LOG_MEAN = math.log(126)
_DWELL_LOG_SIGMA = 1.72

# How fast a van is moving when it crosses the fence. The lead time a geofence
# buys is radius / speed, so this is what turns a radius in metres into an
# arrival time that is early by some seconds.
_APPROACH_SPEED_MPS = 8.0

# Not every stop produces a crossing: permission declined, app killed, GPS
# indoors. The real coverage figure is unknown - that is what the calibration
# report is for - so this is a plausible default to exercise the fallback path,
# not a claim.
_DEFAULT_MISS_RATE = 0.08


@dataclass(frozen=True)
class SyntheticStop:
    """One stop, with what really happened and what each source recorded."""

    shop_index: int
    sequence: int

    #: The truth. Nothing in the system sees these; they are what the
    #: measurement is graded against.
    true_arrived_at: datetime
    true_departed_at: datetime

    #: What the phone would have recorded, or None when the fence did not fire.
    crossing_enter_at: datetime | None
    crossing_exit_at: datetime | None

    #: What the driver tapped. Always later than the truth - a tap happens when
    #: someone remembers, and nobody remembers early.
    tapped_arrived_at: datetime
    tapped_completed_at: datetime

    @property
    def true_dwell_seconds(self) -> float:
        return (self.true_departed_at - self.true_arrived_at).total_seconds()

    @property
    def sensed(self) -> bool:
        return self.crossing_enter_at is not None


@dataclass(frozen=True)
class SyntheticShop:
    """A delivery address in one of Austin's postcodes."""

    index: int
    name: str
    street: str
    postcode: str
    lat: float
    lng: float
    account_ref: str

    @property
    def address(self) -> str:
        return f"{self.street}, Austin, TX {self.postcode}"


@dataclass(frozen=True)
class SyntheticRoute:
    driver_index: int
    stops: list[SyntheticStop]


@dataclass(frozen=True)
class AustinWorld:
    """A generated day, plus the ground truth to grade against."""

    shops: list[SyntheticShop]
    routes: list[SyntheticRoute] = field(default_factory=list)
    geofence_radius_m: float = 75.0

    @property
    def all_stops(self) -> list[SyntheticStop]:
        return [stop for route in self.routes for stop in route.stops]

    @property
    def true_dwells(self) -> list[float]:
        return [stop.true_dwell_seconds for stop in self.all_stops]

    @property
    def expected_lead_seconds(self) -> float:
        """What a correctly-working calibration report should find.

        The fence fires `radius / speed` before the van stops, and the driver
        taps some seconds after. The gap between crossing and tap is the sum,
        and it is the number `measure_geofence_calibration` has to recover.
        """
        return self.geofence_radius_m / _APPROACH_SPEED_MPS

    @property
    def expected_dwell_bias_seconds(self) -> float:
        """How much a geofence OVERSTATES dwell, by construction.

        Found by building this fixture, and worth stating plainly because it is
        easy to assume the opposite: the fence fires early on the way in and
        late on the way out, so the two errors **add**. They do not cancel.
        Dwell measured from crossings is `true + 2 * radius / speed`.

        At 75m and a 8 m/s approach that is ~19 seconds. Against the design
        partner's median dwell of 126s that is a 15% overstatement, and against
        the third of their stops that finish inside 60 seconds it is far worse
        proportionally - the shorter the real stop, the larger the error as a
        share of it.

        Two consequences. The radius is not a free choice: it sets a systematic
        bias on every dwell figure, which is a stronger argument for measuring
        it than the arrival-time accuracy alone. And the bias is predictable,
        so it can be corrected for once the radius is known - which is another
        reason `app/reporting/geofence_calibration.py` is worth having.
        """
        return 2 * self.geofence_radius_m / _APPROACH_SPEED_MPS


_BUSINESS_PREFIXES = (
    "Barton", "Zilker", "Mueller", "Travis", "Congress", "Lamar", "Manor",
    "Pflugerville", "Bastrop", "Onion Creek", "Shoal", "Bee Cave",
)
# Suffixes chosen so IDN-3 can classify most of them - the point is to exercise
# the classifier, not to defeat it. A few are deliberately unclassifiable,
# because the real book is a third unclassifiable and a fixture that is 100%
# labelled would hide that.
_BUSINESS_SUFFIXES = (
    "Auto Body", "Collision Center", "Auto Parts", "Ford", "Tire & Service",
    "Garage", "Distribution Warehouse", "Transfer Station", "County DPW",
    "Holdings", "Group", "LLC",
)
_STREETS = (
    "E 6th St", "S Congress Ave", "W 35th St", "Airport Blvd", "Burnet Rd",
    "S Lamar Blvd", "Manor Rd", "Cesar Chavez St", "Guadalupe St", "Slaughter Ln",
)


def generate_world(
    *,
    seed: int = 1,
    shops: int = 40,
    routes: int = 4,
    stops_per_route: int = 12,
    geofence_radius_m: float = 75.0,
    miss_rate: float = _DEFAULT_MISS_RATE,
    day: datetime | None = None,
) -> AustinWorld:
    """Build a day. Same seed, same world.

    `stops_per_route` above 18 is worth using at least once: that is the point
    where DRV-1's rolling window stops being able to hold the whole route, and
    a route that fits the window never exercises the thing the window exists
    for.
    """
    rng = random.Random(seed)
    start = day or datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    generated_shops = [
        _make_shop(rng, index) for index in range(shops)
    ]

    generated_routes = []
    for driver_index in range(routes):
        generated_routes.append(
            SyntheticRoute(
                driver_index=driver_index,
                stops=_make_route_stops(
                    rng,
                    start=start + timedelta(minutes=17 * driver_index),
                    shop_count=len(generated_shops),
                    stops_per_route=stops_per_route,
                    geofence_radius_m=geofence_radius_m,
                    miss_rate=miss_rate,
                ),
            )
        )

    return AustinWorld(
        shops=generated_shops,
        routes=generated_routes,
        geofence_radius_m=geofence_radius_m,
    )


def _make_shop(rng: random.Random, index: int) -> SyntheticShop:
    postcode, lat, lng = AUSTIN_ZIPS[index % len(AUSTIN_ZIPS)]
    return SyntheticShop(
        index=index,
        name=f"{rng.choice(_BUSINESS_PREFIXES)} {rng.choice(_BUSINESS_SUFFIXES)}",
        street=f"{rng.randint(100, 9999)} {rng.choice(_STREETS)}",
        postcode=postcode,
        lat=lat + rng.uniform(-_JITTER_DEG, _JITTER_DEG),
        lng=lng + rng.uniform(-_JITTER_DEG, _JITTER_DEG),
        # Same grammar as the real book, so account-signal code meets the shape
        # it will meet in production.
        account_ref=f"{1000 + index}/{rng.randint(1, 3)}",
    )


def _make_route_stops(
    rng: random.Random,
    *,
    start: datetime,
    shop_count: int,
    stops_per_route: int,
    geofence_radius_m: float,
    miss_rate: float,
) -> list[SyntheticStop]:
    stops: list[SyntheticStop] = []
    clock = start
    lead = timedelta(seconds=geofence_radius_m / _APPROACH_SPEED_MPS)

    for sequence in range(stops_per_route):
        dwell = timedelta(seconds=rng.lognormvariate(_DWELL_LOG_MEAN, _DWELL_LOG_SIGMA))
        arrived = clock
        departed = arrived + dwell

        sensed = rng.random() >= miss_rate
        stops.append(
            SyntheticStop(
                shop_index=rng.randrange(shop_count),
                sequence=sequence,
                true_arrived_at=arrived,
                true_departed_at=departed,
                # Early in, late out - by the same walk, so the two errors
                # ADD rather than cancel. See expected_dwell_bias_seconds: this
                # is why a geofence overstates dwell rather than merely
                # blurring it.
                crossing_enter_at=(arrived - lead) if sensed else None,
                crossing_exit_at=(departed + lead) if sensed else None,
                # A tap is late and variable. This is the error the sensor
                # exists to remove, so it has to be present to be removed.
                tapped_arrived_at=arrived + timedelta(seconds=rng.uniform(5, 75)),
                tapped_completed_at=departed + timedelta(seconds=rng.uniform(5, 90)),
            )
        )
        # Drive to the next stop.
        clock = departed + timedelta(minutes=rng.uniform(4, 14))

    return stops


def new_uuid(rng: random.Random) -> uuid.UUID:
    """A UUID that is stable for a given seed.

    `uuid.uuid4()` would make every run produce different ids, which defeats
    the point of a deterministic world the moment anything stores one.
    """
    return uuid.UUID(int=rng.getrandbits(128), version=4)
