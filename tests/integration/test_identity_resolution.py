"""IDN-1: an address resolves to exactly one physical dock.

`docs/ROADMAP_1.5.md` Phase 1, §2.2(b). The done-when is "every historical stop
resolves to exactly one dock", so that is the last test here; the ones before it
cover the resolution rule that makes it true.

The address shapes are taken from the design partner's real export rather than
invented: the same body shop written five ways, a city spelled with zeros for
the letter O, and a row whose address is literally `N/A`. Those are the cases
that decide whether 230 customer IDs collapse to the right number of docks.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.identity import resolve_location
from app.models.client import Client
from app.models.hub import Hub
from app.models.location import Location
from app.models.shop import Shop

pytestmark = pytest.mark.integration


async def _seed_hub_and_client(db_session) -> uuid.UUID:
    hub = Hub(id=uuid.uuid4(), name="Identity Test Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Identity Test Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return client.id


async def test_one_dock_written_five_ways_resolves_to_one_location(db_session):
    """The body-shop case, which is why this table exists."""
    spellings = [
        "1200 E 6th St, Austin, TX",
        "  1200   E 6th St,  Austin, TX  ",
        "1200 E 6TH ST, AUSTIN, TX",
        "1200 E 6th St, Austin, TX.",
        "1200 e 6th st, austin, tx;",
    ]

    resolved = [
        await resolve_location(db_session, address=address) for address in spellings
    ]

    assert len({location.id for location in resolved} ) == 1, (
        "five spellings of one dock produced more than one location"
    )
    count = await db_session.scalar(select(func.count()).select_from(Location))
    assert count == 1


async def test_a_different_house_number_is_a_different_dock(db_session):
    """The failure that must never happen, stated as a test.

    A duplicate dock is visible and IDN-2's merge queue collapses it. A wrongly
    merged dock is invisible and sends a driver next door, and nothing
    downstream can detect it. The normalizer is conservative for this reason.
    """
    a = await resolve_location(db_session, address="1200 E 6th St, Austin, TX")
    b = await resolve_location(db_session, address="1202 E 6th St, Austin, TX")
    c = await resolve_location(db_session, address="1200 E 6th Street, Austin, TX")

    assert len({a.id, b.id, c.id}) == 3


async def test_a_zero_for_the_letter_o_is_not_the_same_dock(db_session):
    """`AUSTIN` vs `AUSTIN` with zeros - a real row in the export.

    Recorded as the behaviour we actually have, not the behaviour we want:
    character substitution is not something a conservative normalizer catches,
    so this stays two docks until IDN-2's review queue puts them in front of a
    person. If a later change makes this pass as one dock automatically, that is
    a decision to take deliberately, not a bug fix.
    """
    real = await resolve_location(db_session, address="1200 Main St, Houston, TX")
    typo = await resolve_location(db_session, address="1200 Main St, H0ust0n, TX")

    assert "0" in "H0ust0n", "the typo has to actually differ, or this passes vacuously"
    assert real.id != typo.id


async def test_an_existing_dock_is_not_moved_by_a_later_caller(db_session):
    """Coordinates are set once, by whoever created the dock.

    Two callers disagreeing about a dock's position means either the geocoder
    changed its mind or two places normalized together. The first is harmless
    and the second is serious, and silently taking the newest answer would hide
    both.
    """
    first = await resolve_location(
        db_session, address="1200 E 6th St, Austin, TX", lat=30.2669, lng=-97.7325
    )
    again = await resolve_location(
        db_session, address="1200 E 6TH ST, AUSTIN, TX", lat=0.0, lng=0.0
    )

    assert again.id == first.id
    assert again.lat == pytest.approx(30.2669)
    assert again.lng == pytest.approx(-97.7325)


async def test_a_dock_exists_even_when_it_did_not_geocode(db_session):
    """Identity is not conditional on a third-party service answering."""
    location = await resolve_location(db_session, address="1200 E 6th St, Austin, TX")

    assert location.id is not None
    assert location.geocoded is False


@pytest.mark.parametrize("blank", ["", "   ", ",,,", " , . ; "])
async def test_an_address_that_normalizes_to_nothing_is_refused(db_session, blank):
    """Rather than collecting every blank address into one shared fictional dock."""
    with pytest.raises(ValueError):
        await resolve_location(db_session, address=blank)


async def test_every_shop_resolves_to_exactly_one_dock(db_session):
    """IDN-1's done-when, expressed over shops rather than stops.

    A stop reaches its dock through `Stop.shop_id -> Shop.location_id`, so the
    invariant that matters is this one: every shop whose address names a place
    has exactly one dock, and the shops that name no place have none rather than
    sharing an invented one.
    """
    client_id = await _seed_hub_and_client(db_session)

    addresses = [
        "1200 E 6th St, Austin, TX",
        "  1200   E 6th St,  Austin, TX  ",
        "1200 E 6TH ST, AUSTIN, TX",
        "500 Congress Ave, Austin, TX",
        "N/A",
    ]
    shops = [
        Shop(
            client_id=client_id,
            name=f"Shop {index}",
            address=address,
            lat=30.26,
            lng=-97.73,
        )
        for index, address in enumerate(addresses)
    ]
    db_session.add_all(shops)
    await db_session.flush()

    for shop in shops:
        try:
            location = await resolve_location(db_session, address=shop.address)
        except ValueError:
            continue
        shop.location_id = location.id
    await db_session.flush()

    # Four addresses naming a place, three of which are one dock spelled
    # differently -> two docks.
    dock_ids = {shop.location_id for shop in shops if shop.location_id is not None}
    assert len(dock_ids) == 2

    # And exactly one shop has no dock: the `N/A` row. It is not folded in with
    # anything, and it did not invent a dock of its own.
    unresolved = [shop for shop in shops if shop.location_id is None]
    assert [shop.address for shop in unresolved] == ["N/A"]

    count = await db_session.scalar(select(func.count()).select_from(Location))
    assert count == 2
