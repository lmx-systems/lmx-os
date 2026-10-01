"""`_result_from`: a cached geocode row as a result, or None for a remembered failure.

Both of `resolve_address`'s cache paths go through it - the plain cache hit, and
the concurrent-write race where another request's row wins - and the race path
has no integration test of its own. `GeocodeResult` is a frozen dataclass that
would carry a None coordinate into distance arithmetic without complaint, so the
None case is the one that matters.
"""
from app.geocoding.cache import _result_from
from app.models.geocoded_address import GeocodedAddress


def test_a_resolved_row_becomes_a_result():
    result = _result_from(
        GeocodedAddress(lat=30.2672, lng=-97.7431, display_name="1 Main St", provider="nominatim")
    )
    assert result is not None
    assert (result.lat, result.lng, result.display_name, result.provider) == (
        30.2672, -97.7431, "1 Main St", "nominatim",
    )


def test_missing_text_fields_take_the_cache_defaults():
    result = _result_from(GeocodedAddress(lat=1.0, lng=2.0, display_name=None, provider=None))
    assert result is not None and (result.display_name, result.provider) == ("", "cache")


def test_a_remembered_failure_is_none_not_a_result_at_zero():
    assert _result_from(GeocodedAddress(lat=None, lng=None)) is None
    assert _result_from(GeocodedAddress(lat=30.0, lng=None)) is None
