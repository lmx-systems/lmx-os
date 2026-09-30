"""The fallback signals in `_why_these_addresses_might_match`, pinned.

The export we hold has no coordinates, so the coordinate branch never fires on it
and no integration test asserted it. It still runs on any geocoded book - and it
was rewritten to spell out `geocoded` for the type checker, which is exactly the
kind of edit that should not be trusted untested.
"""
from app.identity.merge import TIER_HIGH, TIER_WEAK, _why_these_addresses_might_match
from app.models.location import Location


def _dock(normalized: str, lat: float | None, lng: float | None) -> Location:
    return Location(address=normalized, normalized_address=normalized, lat=lat, lng=lng)


def test_two_docks_at_the_same_coordinates_are_a_confident_match():
    """Addresses that barely resemble each other, at the same point on the ground."""
    verdict = _why_these_addresses_might_match(
        _dock("1 main st", 30.26720, -97.74310),
        _dock("unit 4 rear lot off main street", 30.26721, -97.74311),
    )
    assert verdict is not None and verdict[0] == TIER_HIGH


def test_a_dock_without_coordinates_falls_back_to_the_address():
    """One side ungeocoded: no arithmetic on a missing coordinate, and the address
    similarity still gets its say."""
    verdict = _why_these_addresses_might_match(
        _dock("1 main st", None, None),
        _dock("1 main st", 30.26720, -97.74310),
    )
    assert verdict is not None and verdict[0] == TIER_WEAK


def test_far_apart_and_unalike_is_no_match():
    assert _why_these_addresses_might_match(
        _dock("1 main st", 30.2672, -97.7431),
        _dock("900 harbor industrial way", 30.4000, -97.9000),
    ) is None
