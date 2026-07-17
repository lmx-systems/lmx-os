"""Geographic clustering helpers for the Batch-Hold Queue."""
from __future__ import annotations

from geopy.distance import geodesic


def miles_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return geodesic((lat1, lng1), (lat2, lng2)).miles


def cluster_members(
    target_lat: float,
    target_lng: float,
    candidates: list[tuple[str, float, float]],
    radius_miles: float,
) -> list[str]:
    """
    Returns the ids of candidates within `radius_miles` of the target point.
    `candidates` is a list of (id, lat, lng) tuples. O(n) - fine for the
    single-hub, low-hundreds-of-open-orders scale this queue runs at; if a
    hub's open-order count grows well beyond that, this should move to a
    spatial index (e.g. a geohash bucket or PostGIS query) instead of a
    linear scan.
    """
    return [
        candidate_id
        for candidate_id, lat, lng in candidates
        if miles_between(target_lat, target_lng, lat, lng) <= radius_miles
    ]
