"""
Real per-delivery pay estimate for gig-classified drivers (docs/ROADMAP.md
A11). Rates below are explicitly-labeled placeholders, same convention as
app/payroll/hours.py's PLACEHOLDER_HOURLY_RATE_CENTS - a real base rate,
per-mile rate, and SLA-tier bonus schedule are a business/pricing decision
no one has made yet, not something to derive from first principles here.

What this closes is the *mechanism* gap the roadmap item actually named:
before this, no fare field or pricing formula existed anywhere in this
codebase, so a gig-classified driver couldn't be shown or paid a
per-delivery amount at all, regardless of what the real numbers should
be. Every call site threading these numbers through (JobOfferView,
complete_stop's payout trigger) is real; only the constants themselves
are a reasoned guess pending real unit-economics data.
"""
from __future__ import annotations

import math

GIG_BASE_PAY_CENTS = 400  # $4.00 base, every delivery
GIG_PER_MILE_CENTS = 70  # $0.70/mile, pickup -> dropoff straight-line distance
GIG_SLA_TIER_BONUS_CENTS = {"HOT_SHOT": 500, "T1": 200, "T2": 0, "T3": 0}

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line, not road, distance - a real routing-API distance
    would need the same live Google Route Optimization account
    docs/ROADMAP.md's E1 gap is already gated on. Straight-line is a
    reasonable estimate for a pay amount shown before a driver ever
    accepts the offer, same spirit as the optimizer's own stub
    nearest-neighbor engine (app/optimizer/google_routes_client.py)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def estimate_delivery_pay_cents(
    *, pickup_lat: float, pickup_lng: float, dropoff_lat: float, dropoff_lng: float, sla_tier: str | None
) -> int:
    distance_miles = haversine_miles(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    tier_bonus = GIG_SLA_TIER_BONUS_CENTS.get(sla_tier or "T2", 0)
    return round(GIG_BASE_PAY_CENTS + distance_miles * GIG_PER_MILE_CENTS + tier_bonus)
