"""
Coverage for address resolution (docs/LMX_LINK_PLAN.md §1.2).

Everything here runs against a counting fake geocoder rather than the network.
That is not just test hygiene: the real provider is rate-limited to one request
per second and its usage policy forbids bulk automated use, so a suite that hit
it would be both flaky and impolite.

The guarantee under test is "geocode once on first order per address, cache and
reuse" - which is what makes a no-account, 1-req/sec provider viable at all. The
request-count assertions are the real subject; if they ever relax, steady-state
volume silently becomes orders-per-day instead of new-addresses-per-day.
"""
import pytest
from sqlalchemy import func, select

from app.geocoding.base import BaseGeocoder, GeocodeResult, normalize_address
from app.geocoding.cache import resolve_address
from app.models.geocoded_address import GeocodedAddress

pytestmark = pytest.mark.integration

AUSTIN = GeocodeResult(
    lat=30.2669, lng=-97.7325, display_name="1200 E 6th St, Austin, TX", provider="fake"
)


class CountingGeocoder(BaseGeocoder):
    """Records how many times it was actually asked."""

    provider_name = "fake"

    def __init__(self, result: GeocodeResult | None = AUSTIN) -> None:
        self.result = result
        self.calls: list[str] = []

    async def geocode(self, address: str) -> GeocodeResult | None:
        self.calls.append(address)
        return self.result


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("1200 E 6th St, Austin TX", "1200 e 6th st, austin tx"),
        ("1200 E 6th St, Austin TX", "  1200   E 6th St,  Austin TX  "),
        ("1200 E 6th St, Austin TX", "1200 E 6th St, Austin TX."),
    ],
)
def test_trivial_differences_share_a_cache_key(a, b):
    """Case, whitespace and trailing punctuation are noise."""
    assert normalize_address(a) == normalize_address(b)


@pytest.mark.parametrize(
    "a,b",
    [
        # The one that matters: a different house number is a different building.
        ("1200 E 6th St, Austin TX", "1202 E 6th St, Austin TX"),
        # Abbreviations are deliberately NOT canonicalized - see
        # normalize_address's docstring on the asymmetry. This costs a duplicate
        # geocode, which is much cheaper than the alternative error.
        ("1200 E 6th Street, Austin TX", "1200 E 6th St, Austin TX"),
    ],
)
def test_meaningful_differences_do_not_share_a_cache_key(a, b):
    assert normalize_address(a) != normalize_address(b)


def test_an_empty_address_resolves_to_nothing():
    assert normalize_address("   ,. ") == ""


# ---------------------------------------------------------------------------
# The once-ever guarantee
# ---------------------------------------------------------------------------


async def test_an_address_is_geocoded_once_and_reused_forever(db_session):
    """The guarantee the whole design rests on."""
    geocoder = CountingGeocoder()

    first = await resolve_address(db_session, "1200 E 6th St, Austin TX", geocoder=geocoder)
    second = await resolve_address(db_session, "1200 E 6th St, Austin TX", geocoder=geocoder)

    assert first.lat == pytest.approx(30.2669)
    assert second.lat == pytest.approx(30.2669)
    assert len(geocoder.calls) == 1, "second lookup must come from cache"


async def test_a_differently_typed_version_of_the_same_address_still_hits_cache(db_session):
    """Case and spacing are the common real variation - a counter person typing
    the same shop twice will not match themselves byte for byte."""
    geocoder = CountingGeocoder()

    await resolve_address(db_session, "1200 E 6th St, Austin TX", geocoder=geocoder)
    await resolve_address(db_session, "  1200 e 6th st, AUSTIN tx ", geocoder=geocoder)

    assert len(geocoder.calls) == 1


async def test_two_different_addresses_are_geocoded_separately(db_session):
    geocoder = CountingGeocoder()

    await resolve_address(db_session, "1200 E 6th St, Austin TX", geocoder=geocoder)
    await resolve_address(db_session, "900 Congress Ave, Austin TX", geocoder=geocoder)

    assert len(geocoder.calls) == 2


async def test_only_one_cache_row_per_address(db_session):
    geocoder = CountingGeocoder()
    for _ in range(3):
        await resolve_address(db_session, "1200 E 6th St, Austin TX", geocoder=geocoder)

    count = (
        await db_session.execute(select(func.count()).select_from(GeocodedAddress))
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


async def test_an_unresolvable_address_returns_none_rather_than_a_guess(db_session):
    """A wrong coordinate is worse than no coordinate: no coordinate stops
    dispatch, a wrong one sends a real van somewhere real and wrong."""
    geocoder = CountingGeocoder(result=None)

    result = await resolve_address(db_session, "not a real place at all", geocoder=geocoder)

    assert result is None


async def test_a_failed_lookup_is_remembered_and_not_retried(db_session):
    """The realistic failure is a typo the customer resubmits. Three attempts at
    the same bad address must not burn three of a very small request budget."""
    geocoder = CountingGeocoder(result=None)

    for _ in range(3):
        assert await resolve_address(db_session, "asdfghjkl", geocoder=geocoder) is None

    assert len(geocoder.calls) == 1, "a remembered failure must not re-ask the provider"


async def test_a_failed_lookup_is_stored_as_a_row_not_left_absent(db_session):
    """Absence and 'we asked and it didn't resolve' are different facts."""
    geocoder = CountingGeocoder(result=None)
    await resolve_address(db_session, "asdfghjkl", geocoder=geocoder)

    row = (
        await db_session.execute(
            select(GeocodedAddress).where(
                GeocodedAddress.normalized_address == normalize_address("asdfghjkl")
            )
        )
    ).scalar_one()
    assert row.resolved is False
    assert row.lat is None
    assert row.attempted_at is not None


async def test_a_blank_address_never_reaches_the_provider(db_session):
    geocoder = CountingGeocoder()
    assert await resolve_address(db_session, "   ", geocoder=geocoder) is None
    assert geocoder.calls == []


# ---------------------------------------------------------------------------
# What gets stored
# ---------------------------------------------------------------------------


async def test_the_raw_input_is_kept_alongside_the_key(db_session):
    """When a resolution looks wrong, the question is whether the input or the
    provider was at fault - which needs both."""
    geocoder = CountingGeocoder()
    typed = "  1200 E 6th St, Austin TX  "
    await resolve_address(db_session, typed, geocoder=geocoder)

    row = (await db_session.execute(select(GeocodedAddress))).scalar_one()
    assert row.raw_address == typed
    assert row.normalized_address == normalize_address(typed)
    assert row.provider == "fake"
    assert row.display_name == "1200 E 6th St, Austin, TX"
