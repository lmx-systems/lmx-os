"""
Working out what a drop costs (docs/ROADMAP.md F5).

`client_rates` was flat per-drop, per-tier. Real courier rate cards are not: they are
written as *"$8 plus $1.50 a mile, minimum $12"*, sometimes with a per-piece or per-weight
component on top. So the components here are **additive rather than a mutually-exclusive
basis**, because an enum would force every hybrid contract to be approximated and the
approximation shows up later as an argument about an invoice.

    fee = base + (miles x per_mile) + (pieces x per_piece) + (weight x per_weight)
    fee = max(fee, minimum_charge)

**Priced once, at ingestion, and frozen on the order.** That is pre-existing behaviour and
it matters more now than it did: a rate card that changes mid-month must not retroactively
reprice orders already taken, and with a per-mile component a rate change would otherwise
move numbers a client has already been quoted.

**`fee_breakdown` is the other half of that.** With one flat rate, "why is this $18" needed
no explanation. With a rate table it does, and reconstructing it later from a card that may
since have changed is not an answer - so the arithmetic is recorded alongside the result.

Distance is straight-line between shop and drop, at the same placeholder used everywhere
else in this codebase, and the breakdown says so. Billing a client for road miles we have
not measured would be worse than billing for a distance we can both compute the same way -
E1's live routing is what turns this into real miles, and until then the label is honest.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.batch_queue.clustering import miles_between
from app.models.client_rate import ClientRate

logger = structlog.get_logger(__name__)

# Named in the breakdown so nobody reading a stored fee has to guess which distance model
# produced it - and so the day E1 lands, old and new lines are distinguishable.
DISTANCE_MODEL = "straight_line"


@dataclass(frozen=True)
class PricedDrop:
    fee_cents: int
    breakdown: dict


def price_drop(
    rate: ClientRate,
    *,
    miles: float | None,
    pieces: int,
    weight_units: float,
) -> PricedDrop:
    """The fee for one drop, and the arithmetic that produced it.

    `miles` is None when either end isn't geocoded. The mileage component is then simply
    absent rather than estimated: charging for a distance we could not compute is the one
    outcome a client would be right to dispute, and it would be invisible in a total.
    """
    components: list[dict] = []
    total = rate.rate_per_drop_cents
    if total:
        components.append(
            {"kind": "base", "amount_cents": total, "detail": "per drop"}
        )

    if rate.rate_per_mile_cents:
        if miles is None:
            logger.warning(
                "rate_mileage_component_skipped",
                client_id=str(rate.client_id),
                detail="no distance available - billing without the per-mile component",
            )
            components.append(
                {
                    "kind": "mileage",
                    "amount_cents": 0,
                    "detail": "not charged - the distance could not be computed",
                }
            )
        else:
            # Rounded once, at the end of this component, rather than rounding the
            # distance first: rounding miles to whole numbers before multiplying turns a
            # 4.4-mile drop into a 4-mile one and quietly under-bills every short run.
            amount = round(miles * rate.rate_per_mile_cents)
            total += amount
            components.append(
                {
                    "kind": "mileage",
                    "amount_cents": amount,
                    "detail": f"{miles:.2f} mi ({DISTANCE_MODEL}) at {rate.rate_per_mile_cents}c/mi",
                }
            )

    if rate.rate_per_piece_cents and pieces:
        amount = pieces * rate.rate_per_piece_cents
        total += amount
        components.append(
            {
                "kind": "pieces",
                "amount_cents": amount,
                "detail": f"{pieces} x {rate.rate_per_piece_cents}c",
            }
        )

    if rate.rate_per_weight_unit_cents and weight_units:
        amount = round(weight_units * rate.rate_per_weight_unit_cents)
        total += amount
        components.append(
            {
                "kind": "weight",
                "amount_cents": amount,
                "detail": f"{weight_units:g} units at {rate.rate_per_weight_unit_cents}c",
            }
        )

    minimum = rate.minimum_charge_cents
    if minimum is not None and total < minimum:
        # Recorded as its own line rather than silently replacing the total, so a client
        # looking at a short cheap drop can see the minimum is what they are paying for
        # instead of concluding the mileage was computed wrong.
        components.append(
            {
                "kind": "minimum",
                "amount_cents": minimum - total,
                "detail": f"topped up to the {minimum}c minimum",
            }
        )
        total = minimum

    return PricedDrop(
        fee_cents=total,
        breakdown={
            "sla_tier": rate.sla_tier,
            "total_cents": total,
            "distance_model": DISTANCE_MODEL,
            "components": components,
        },
    )


def distance_between(
    origin_lat: float | None,
    origin_lng: float | None,
    drop_lat: float | None,
    drop_lng: float | None,
) -> float | None:
    """Straight-line miles, or None if either end is unknown."""
    if None in (origin_lat, origin_lng, drop_lat, drop_lng):
        return None
    return miles_between(
        float(origin_lat), float(origin_lng), float(drop_lat), float(drop_lng)
    )
