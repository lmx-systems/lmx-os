"""
The keyed geocoder and the cache-poisoning fix (docs/ROADMAP.md L12).

Nominatim was always a pilot decision: its usage policy forbids commercial bulk
use, and its one-request-per-second ceiling is what makes a 25-row bulk paste of
new addresses take 25 seconds. This adds a keyed provider behind the same
interface, with no throttle.

**The important half of this file is not the provider, it's the distinction it
forced.** The address cache remembers a failed lookup and never retries it - which
is right for a typo, and catastrophic for a quota exhaustion. Before this,
`resolve_address` cached ANY `None`, so one expired API key would have permanently
marked every address attempted during that window as unresolvable, and every
future order to them would have been refused with nothing to explain why. Google
returns HTTP 200 for quota and auth failures, which makes that far more likely
than it was with Nominatim.

So: `None` means "asked, and this address is not real" and is cached.
`GeocoderUnavailableError` means "could not ask" and is never cached.
"""
import httpx
import pytest
from sqlalchemy import func, select

from app.geocoding.base import BaseGeocoder, GeocodeResult, GeocoderUnavailableError
from app.geocoding.cache import resolve_address
from app.geocoding.google import GoogleGeocoder
from app.models.geocoded_address import GeocodedAddress

pytestmark = pytest.mark.integration

ADDRESS = "1200 E 6th St, Austin TX"


def _google(handler) -> GoogleGeocoder:
    """A GoogleGeocoder whose HTTP calls are served by `handler`, so the status
    mapping can be tested without a key or a network."""
    geocoder = GoogleGeocoder(api_key="test-key")
    geocoder._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return geocoder


def _json(payload: dict, status_code: int = 200):
    return lambda request: httpx.Response(status_code, json=payload)


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


async def test_a_resolved_address_returns_coordinates():
    geocoder = _google(
        _json(
            {
                "status": "OK",
                "results": [
                    {
                        "formatted_address": "1200 E 6th St, Austin, TX 78702, USA",
                        "geometry": {"location": {"lat": 30.264642, "lng": -97.730218}},
                    }
                ],
            }
        )
    )

    result = await geocoder.geocode(ADDRESS)

    assert result.lat == pytest.approx(30.264642)
    assert result.lng == pytest.approx(-97.730218)
    assert result.display_name == "1200 E 6th St, Austin, TX 78702, USA"
    assert result.provider == "google"


async def test_the_api_key_is_sent():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})

    await _google(handler).geocode(ADDRESS)

    assert seen["key"] == "test-key"
    assert seen["address"] == ADDRESS


# ---------------------------------------------------------------------------
# ZERO_RESULTS is the ONLY cacheable no-match
# ---------------------------------------------------------------------------


async def test_zero_results_is_a_definitive_no_match():
    """The address is not real. Safe to remember, because asking again would get
    the same answer."""
    geocoder = _google(_json({"status": "ZERO_RESULTS", "results": []}))
    assert await geocoder.geocode("not a real place") is None


# ---------------------------------------------------------------------------
# Everything else must RAISE, not return None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["OVER_QUERY_LIMIT", "REQUEST_DENIED", "INVALID_REQUEST", "UNKNOWN_ERROR"],
)
async def test_provider_side_failures_raise(status):
    """Google returns HTTP 200 for all of these, so treating a 200 as success
    would turn an exhausted quota into 'that address doesn't exist'."""
    geocoder = _google(_json({"status": status, "error_message": "something"}))
    with pytest.raises(GeocoderUnavailableError):
        await geocoder.geocode(ADDRESS)


async def test_an_unrecognised_status_raises_rather_than_guessing():
    """Assuming an unknown code means no-match is exactly the mistake that
    poisons the cache."""
    geocoder = _google(_json({"status": "SOME_NEW_STATUS"}))
    with pytest.raises(GeocoderUnavailableError):
        await geocoder.geocode(ADDRESS)


async def test_a_non_200_raises():
    geocoder = _google(_json({}, status_code=503))
    with pytest.raises(GeocoderUnavailableError):
        await geocoder.geocode(ADDRESS)


async def test_a_transport_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route")

    with pytest.raises(GeocoderUnavailableError):
        await _google(handler).geocode(ADDRESS)


async def test_ok_with_unreadable_coordinates_raises():
    geocoder = _google(_json({"status": "OK", "results": [{"geometry": {}}]}))
    with pytest.raises(GeocoderUnavailableError):
        await geocoder.geocode(ADDRESS)


async def test_the_error_message_is_carried_through():
    """REQUEST_DENIED's message is usually the actual fix - 'billing not enabled',
    'API not activated' - so swallowing it costs an afternoon of debugging."""
    geocoder = _google(
        _json({"status": "REQUEST_DENIED", "error_message": "Billing not enabled"})
    )
    with pytest.raises(GeocoderUnavailableError, match="Billing not enabled"):
        await geocoder.geocode(ADDRESS)


# ---------------------------------------------------------------------------
# The cache-poisoning fix
# ---------------------------------------------------------------------------


class _Unavailable(BaseGeocoder):
    provider_name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls += 1
        raise GeocoderUnavailableError("quota exhausted")


class _Resolving(BaseGeocoder):
    provider_name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls += 1
        return GeocodeResult(lat=30.26, lng=-97.73, display_name=ADDRESS, provider="fake")


async def test_a_provider_failure_is_never_cached(db_session):
    """**The bug this fixes.** One quota exhaustion must not permanently mark a
    real address as unresolvable."""
    flaky = _Unavailable()

    assert await resolve_address(db_session, ADDRESS, geocoder=flaky) is None

    rows = (await db_session.execute(select(func.count()).select_from(GeocodedAddress))).scalar_one()
    assert rows == 0, "nothing may be remembered about an address we never asked about"


async def test_the_address_still_resolves_once_the_provider_recovers(db_session):
    """The whole point: an outage costs the orders placed during it, not the
    address forever."""
    assert await resolve_address(db_session, ADDRESS, geocoder=_Unavailable()) is None

    recovered = _Resolving()
    result = await resolve_address(db_session, ADDRESS, geocoder=recovered)

    assert result is not None
    assert recovered.calls == 1, "the provider is asked again, not short-circuited"


async def test_a_provider_failure_is_retried_every_time(db_session):
    """Contrast with a remembered no-match, which is deliberately never retried."""
    flaky = _Unavailable()
    for _ in range(3):
        await resolve_address(db_session, ADDRESS, geocoder=flaky)
    assert flaky.calls == 3


async def test_a_genuine_no_match_is_still_cached_and_not_retried(db_session):
    """The behaviour that had to survive this change - a resubmitted typo must not
    burn the request budget three times."""

    class _NoMatch(BaseGeocoder):
        provider_name = "fake"

        def __init__(self) -> None:
            self.calls = 0

        async def geocode(self, address: str) -> GeocodeResult | None:
            self.calls += 1
            return None

    no_match = _NoMatch()
    for _ in range(3):
        assert await resolve_address(db_session, "asdfghjkl", geocoder=no_match) is None

    assert no_match.calls == 1
    rows = (await db_session.execute(select(func.count()).select_from(GeocodedAddress))).scalar_one()
    assert rows == 1


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_google_without_a_key_refuses_rather_than_falling_back(monkeypatch):
    """Falling back to Nominatim would silently reintroduce both the licensing
    problem and the rate ceiling that setting this was meant to escape."""
    import app.geocoding as geocoding
    from app.config import settings

    monkeypatch.setattr(geocoding, "_geocoder", None)
    monkeypatch.setattr(settings, "geocoder_provider", "google")
    monkeypatch.setattr(settings, "google_maps_api_key", None)

    with pytest.raises(ValueError, match="GOOGLE_MAPS_API_KEY"):
        geocoding.get_geocoder()


def test_google_with_a_key_is_selected(monkeypatch):
    import app.geocoding as geocoding
    from app.config import settings

    monkeypatch.setattr(geocoding, "_geocoder", None)
    monkeypatch.setattr(settings, "geocoder_provider", "google")
    monkeypatch.setattr(settings, "google_maps_api_key", "a-key")

    chosen = geocoding.get_geocoder()
    assert chosen.provider_name == "google"
    monkeypatch.setattr(geocoding, "_geocoder", None)


def test_an_unknown_provider_still_refuses(monkeypatch):
    import app.geocoding as geocoding
    from app.config import settings

    monkeypatch.setattr(geocoding, "_geocoder", None)
    monkeypatch.setattr(settings, "geocoder_provider", "mapbox")

    with pytest.raises(ValueError, match="unknown GEOCODER_PROVIDER"):
        geocoding.get_geocoder()
    monkeypatch.setattr(geocoding, "_geocoder", None)
