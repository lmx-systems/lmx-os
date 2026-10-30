"""An illustrative second supplier, built from open data (`DE-19`; the Proof Map's B6).

The Proof Map's density and commingling readings (O4, O7 across suppliers) need a
second supplier's orders beside the design partner's, and none exists. This makes
one, and says on every output that it is made: **Supplier B is invented** (D-SIM).
What is real is what it is made from, and each input names its source in the spec
(`ml/analytics/supplier_b_spec.json`):

    who receives        real businesses from Overture Places (CDLA-Permissive 2.0,
                        Apache 2.0, CC0), by category and confidence; no names
    in what mix         Census County Business Patterns 2022 establishment counts
    how heavy           the Commodity Flow Survey 2017 public file: shipments by
                        plumbing and HVAC wholesalers' own trucks within 50 miles
    how many a day      vehicles per branch from public 10-Ks, times stops a vehicle
                        makes in a day, which is assumed
    at what hour        the design partner's own measured hour of day, which is an
                        assumption that a trade counter's day looks alike

**Seeded and repeatable.** The same spec, receivers, hour profile and seed give the
same orders, so a reading taken from them can be reproduced.

Standard library; the receivers file is written by `scripts/illustrative_pack.py
supplier-receivers` and read here as plain rows.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ml.analytics.illustrative import STAMP

LB_TO_KG = 0.45359237
MILE_KM = 1.609344


@dataclass(frozen=True)
class Receiver:
    receiver_id: str
    category: str
    lat: float
    lng: float


@dataclass(frozen=True)
class Order:
    order_id: str
    day: int
    hour: int
    minute: int
    receiver_id: str
    category: str
    lat: float
    lng: float
    weight_kg: float


def check_spec(spec: Mapping) -> list[str]:
    """Every problem with a spec, so it can be refused whole."""
    problems = []
    for key in ("name", "categories", "orders_per_business_day", "business_days", "weight_lb_quantiles", "activity_sigma"):
        if key not in spec:
            problems.append(f"the spec has no {key}")
    for name, category in spec.get("categories", {}).items():
        if not category.get("share") or category["share"] <= 0:
            problems.append(f"category {name}: share must be above zero")
        if not category.get("source"):
            problems.append(f"category {name}: no source")
    quantiles = spec.get("weight_lb_quantiles", {}).get("points", [])
    if len(quantiles) < 2 or any(b[0] <= a[0] or b[1] < a[1] for a, b in zip(quantiles, quantiles[1:])):
        problems.append("weight_lb_quantiles.points must rise in both share and weight")
    if quantiles and (quantiles[0][0] != 0 or quantiles[-1][0] != 1):
        problems.append("weight_lb_quantiles.points must run from share 0 to share 1")
    for key in ("orders_per_business_day", "weight_lb_quantiles"):
        if key in spec and not spec[key].get("source"):
            problems.append(f"{key}: no source")
    return problems


def _weight_kg(rng: random.Random, points: Sequence[Sequence[float]]) -> float:
    """A shipment weight from the quantile curve, linear between its points."""
    u = rng.random()
    for (q0, w0), (q1, w1) in zip(points, points[1:]):
        if u <= q1:
            pounds = w0 + (w1 - w0) * (u - q0) / (q1 - q0)
            return round(pounds * LB_TO_KG, 2)
    return round(points[-1][1] * LB_TO_KG, 2)


def generate(receivers: Sequence[Receiver], spec: Mapping, hour_profile: Sequence[int], seed: int) -> list[Order]:
    """Supplier B's orders over the spec's business days.

    Each order picks a category by the spec's shares, then a receiver within it,
    weighted by a seeded activity level per receiver: a contractor who orders
    often is the usual shape of a trade account, and a uniform draw would spread
    every receiver's orders evenly. The hour comes from `hour_profile` (24 counts).
    """
    problems = check_spec(spec)
    if problems:
        raise ValueError("; ".join(problems))
    if len(hour_profile) != 24 or not sum(hour_profile):
        raise ValueError("hour_profile must be 24 counts with at least one")
    rng = random.Random(seed)
    by_category: dict[str, list[Receiver]] = defaultdict(list)
    for receiver in sorted(receivers, key=lambda r: r.receiver_id):
        if receiver.category in spec["categories"]:
            by_category[receiver.category].append(receiver)
    categories = [c for c in spec["categories"] if by_category[c]]
    if not categories:
        raise ValueError("no receivers in any of the spec's categories")
    activity = {
        r.receiver_id: rng.lognormvariate(0, spec["activity_sigma"]) for c in categories for r in by_category[c]
    }
    shares = [spec["categories"][c]["share"] for c in categories]
    points = spec["weight_lb_quantiles"]["points"]

    orders = []
    for day in range(spec["business_days"]):
        for n in range(spec["orders_per_business_day"]["value"]):
            category = rng.choices(categories, weights=shares)[0]
            pool = by_category[category]
            receiver = rng.choices(pool, weights=[activity[r.receiver_id] for r in pool])[0]
            orders.append(
                Order(
                    order_id=f"B-{day + 1:02d}-{n + 1:03d}",
                    day=day,
                    hour=rng.choices(range(24), weights=hour_profile)[0],
                    minute=rng.randrange(60),
                    receiver_id=receiver.receiver_id,
                    category=category,
                    lat=receiver.lat,
                    lng=receiver.lng,
                    weight_kg=_weight_kg(rng, points),
                )
            )
    return orders


def _cell(point: tuple[float, float], cell_miles: float) -> tuple[int, int]:
    """A square grid cell of about `cell_miles` on a side (flat-earth, fine at metro scale)."""
    lat, lng = point
    km = cell_miles * MILE_KM
    return (math.floor(lat * 111.32 / km), math.floor(lng * 111.32 * math.cos(math.radians(lat)) / km))


def density(
    a: Sequence[tuple[tuple[float, float], int]],
    b: Sequence[tuple[tuple[float, float], int]],
    days_a: int,
    days_b: int,
    cell_miles: float = 1.0,
) -> dict:
    """Drops per square mile by hour, each supplier alone and the two together (the Proof Map's O4).

    `a` and `b` are (point, hour) per drop-off. Counts are averaged per business
    day, so suppliers observed over different spans compare. A cell "shared" in
    an hour has drops from both suppliers in it - the commingling a route could
    use. Only cells with any drop count, so the figures describe where work is.
    """
    per_cell: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for points, days, index in ((a, days_a, 0), (b, days_b, 1)):
        for point, hour in points:
            per_cell[(hour, _cell(point, cell_miles))][index] += 1 / days
    hours = []
    for hour in range(24):
        cells = {k[1]: v for k, v in per_cell.items() if k[0] == hour}
        if not cells:
            continue
        a_cells = [v[0] for v in cells.values() if v[0]]
        b_cells = [v[1] for v in cells.values() if v[1]]
        both = [v for v in cells.values() if v[0] and v[1]]
        hours.append({
            "hour": hour,
            "drops_a": round(sum(v[0] for v in cells.values()), 1),
            "drops_b": round(sum(v[1] for v in cells.values()), 1),
            "cells_a": len(a_cells),
            "cells_b": len(b_cells),
            "cells_with_any": len(cells),
            "cells_shared": len(both),
            "drops_per_cell_a": round(sum(a_cells) / len(a_cells), 2) if a_cells else 0.0,
            "drops_per_cell_together": round(sum(v[0] + v[1] for v in cells.values()) / len(cells), 2),
            "b_drops_in_shared_cells": round(sum(v[1] for v in both), 1),
        })
    return {"stamp": STAMP, "cell_square_miles": round(cell_miles**2, 2), "hours": hours}


def summary(orders: Sequence[Order]) -> dict:
    weights = sorted(o.weight_kg for o in orders)
    n = len(weights)
    return {
        "stamp": STAMP,
        "orders": n,
        "receivers": len({o.receiver_id for o in orders}),
        "by_category": dict(Counter(o.category for o in orders).most_common()),
        "weight_kg_p25_p50_p75_p90": [weights[int(q * (n - 1))] for q in (0.25, 0.5, 0.75, 0.9)] if n else [],
        "share_at_most_2_3_kg": round(sum(w <= 2.3 for w in weights) / n, 3) if n else None,
        "share_at_most_9_07_kg": round(sum(w <= 9.07 for w in weights) / n, 3) if n else None,
        "share_at_most_25_kg": round(sum(w <= 25 for w in weights) / n, 3) if n else None,
    }
