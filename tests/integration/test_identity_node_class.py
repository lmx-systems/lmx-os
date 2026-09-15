"""IDN-3: what kind of place each dock is, and how much of the book is labelled.

`docs/ROADMAP_1.5.md` Phase 1. Done when "all ~230 accounts classified;
unlabelled below 2%" - so coverage is measured here, not assumed.

`PRD-1` is why the labels matter and why a wrong one is expensive: batch value
is +3-7% at high-frequency shops against +195-271% at warehouse and transfer
nodes. Mislabel a warehouse as a shop and the model averages across two orders
of magnitude.
"""
import uuid

import pytest
from sqlalchemy import select

from app.identity import (
    classification_coverage,
    classify_unlabelled_locations,
    infer_node_class,
    resolve_location,
    set_node_class,
)
from app.identity.node_class import (
    NODE_CLASS_BODY_SHOP,
    NODE_CLASS_DEALER,
    NODE_CLASS_MUNICIPAL,
    NODE_CLASS_PARTS_STORE,
    NODE_CLASS_SHOP,
    NODE_CLASS_TRANSFER,
    NODE_CLASS_WAREHOUSE,
    SOURCE_HUMAN,
    SOURCE_INFERRED,
)
from app.models.client import Client
from app.models.hub import Hub
from app.models.location import Location
from app.models.shop import Shop

pytestmark = pytest.mark.integration


async def _seed_client(db_session) -> uuid.UUID:
    hub = Hub(id=uuid.uuid4(), name="Node Class Hub", lat=34.05, lng=-118.25)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Node Class Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return client.id


async def _seed_shop(db_session, client_id, name: str, address: str) -> Location:
    location = await resolve_location(db_session, address=address)
    db_session.add(
        Shop(client_id=client_id, name=name, address=address, lat=30.0, lng=-97.0,
             location_id=location.id)
    )
    await db_session.flush()
    return location


# Names in the shape a distributor's account list actually holds them.
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Smith Auto Body Shop", NODE_CLASS_BODY_SHOP),
        ("Westside Collision Center", NODE_CLASS_BODY_SHOP),
        ("NAPA Auto Parts #4471", NODE_CLASS_PARTS_STORE),
        ("O'Reilly Auto Parts", NODE_CLASS_PARTS_STORE),
        ("Capital Ford", NODE_CLASS_DEALER),
        ("Riverside Toyota Dealership", NODE_CLASS_DEALER),
        ("Central Distribution Warehouse", NODE_CLASS_WAREHOUSE),
        ("Regional DC 3", NODE_CLASS_WAREHOUSE),
        ("North Transfer Station", NODE_CLASS_TRANSFER),
        ("Airport Cross-Dock", NODE_CLASS_TRANSFER),
        ("City of Austin Fleet Services", NODE_CLASS_MUNICIPAL),
        ("Travis County Public Works", NODE_CLASS_MUNICIPAL),
        ("Joe's Garage", NODE_CLASS_SHOP),
        ("Discount Tire", NODE_CLASS_SHOP),
    ],
)
def test_the_classifier_reads_the_obvious_cases(name, expected):
    guess = infer_node_class(name)
    assert guess is not None, f"{name!r} was not classified at all"
    assert guess[0] == expected, f"{name!r} -> {guess[0]}, expected {expected}"


def test_the_more_specific_class_wins():
    """Order is load-bearing.

    "Auto Body Shop" contains "shop" and is not a generic shop. "Parts
    Warehouse" contains "parts" and is not a parts store. If the rule order is
    ever rearranged, these are what fail.
    """
    assert infer_node_class("Smith Auto Body Shop")[0] == NODE_CLASS_BODY_SHOP
    assert infer_node_class("NAPA Parts Warehouse")[0] == NODE_CLASS_WAREHOUSE
    assert infer_node_class("Ford Collision Center")[0] == NODE_CLASS_BODY_SHOP


def test_a_word_inside_another_word_is_not_a_match():
    """Substring matching would classify half the book wrongly and silently."""
    assert infer_node_class("Shopworth Holdings") is None
    assert infer_node_class("Bodycote Industries") is None
    assert infer_node_class("Naples Trading") is None


def test_an_unrecognisable_name_is_left_alone():
    """Under 2% unlabelled is the target, not zero. Guessing is worse than null."""
    assert infer_node_class("Acme LLC") is None
    assert infer_node_class("") is None
    assert infer_node_class(None) is None


def test_the_account_name_outranks_the_street_name():
    """A business on Warehouse Road is not a warehouse."""
    guess = infer_node_class("Smith Collision", "1200 Warehouse Road, Austin, TX")
    assert guess[0] == NODE_CLASS_BODY_SHOP


async def test_classifying_records_what_it_matched_on(db_session):
    """A reviewer correcting a label has to see what the machine keyed on."""
    client_id = await _seed_client(db_session)
    location = await _seed_shop(
        db_session, client_id, "Capital Ford", "900 Dealer Dr, Austin, TX"
    )

    result = await classify_unlabelled_locations(db_session)
    assert result["labelled"] == 1

    await db_session.refresh(location)
    assert location.node_class == NODE_CLASS_DEALER
    assert location.node_class_source == SOURCE_INFERRED
    assert location.node_class_evidence.lower() == "ford"


async def test_a_human_label_is_never_overwritten_by_the_classifier(db_session):
    """The same rule as IDN-2's merges: a person's judgement outranks a guess."""
    client_id = await _seed_client(db_session)
    location = await _seed_shop(
        db_session, client_id, "Capital Ford", "900 Dealer Dr, Austin, TX"
    )

    # A reviewer knows this account is actually the dealer's body shop.
    set_node_class(location, NODE_CLASS_BODY_SHOP)
    await db_session.flush()

    await classify_unlabelled_locations(db_session)

    await db_session.refresh(location)
    assert location.node_class == NODE_CLASS_BODY_SHOP
    assert location.node_class_source == SOURCE_HUMAN
    assert location.node_class_evidence is None


async def test_running_the_classifier_twice_changes_nothing(db_session):
    """Idempotent, so a reviewer's correction survives the next batch run."""
    client_id = await _seed_client(db_session)
    await _seed_shop(db_session, client_id, "Capital Ford", "900 Dealer Dr, Austin, TX")

    first = await classify_unlabelled_locations(db_session)
    second = await classify_unlabelled_locations(db_session)

    assert first["labelled"] == 1
    assert second["labelled"] == 0
    assert second["considered"] == 0


def test_an_eighth_class_is_refused():
    """PRD-1 groups by these seven. An unnoticed eighth splits a group."""
    location = Location(normalized_address="x", address="x")
    with pytest.raises(ValueError, match="not one of the seven"):
        set_node_class(location, "fuel_depot")


async def test_coverage_counts_accounts_and_does_not_flatter_itself(db_session):
    """IDN-3's done-when, measured.

    The denominator is shops, not docks - the roadmap says accounts. A shop with
    no dock cannot be classified and is reported separately rather than dropped
    from the denominator, which would make the percentage look better than the
    book is.
    """
    client_id = await _seed_client(db_session)
    for name, address in [
        ("Smith Auto Body Shop", "1 A St, Austin, TX"),
        ("NAPA Auto Parts", "2 B St, Austin, TX"),
        ("Capital Ford", "3 C St, Austin, TX"),
        ("Acme LLC", "4 D St, Austin, TX"),
    ]:
        await _seed_shop(db_session, client_id, name, address)

    # One account with no resolvable dock at all - the export's `N/A` row.
    db_session.add(
        Shop(client_id=client_id, name="Unknown Account", address="N/A",
             lat=30.0, lng=-97.0, location_id=None)
    )
    await db_session.flush()

    await classify_unlabelled_locations(db_session)
    coverage = await classification_coverage(db_session)

    assert coverage["shops"] == 5
    assert coverage["classified"] == 3, "Acme LLC and the N/A row are not classifiable"
    assert coverage["unlabelled"] == 2
    assert coverage["without_dock"] == 1
    assert coverage["unlabelled_pct"] == pytest.approx(40.0)
    assert coverage["meets_target"] is False, "40% unlabelled is nowhere near under 2%"


async def test_coverage_reports_meeting_the_target(db_session):
    """The other side of the same measure, so `meets_target` is not always False."""
    client_id = await _seed_client(db_session)
    for index in range(50):
        await _seed_shop(
            db_session, client_id, f"Body Shop {index}", f"{index} Main St, Austin, TX"
        )

    await classify_unlabelled_locations(db_session)
    coverage = await classification_coverage(db_session)

    assert coverage["unlabelled"] == 0
    assert coverage["meets_target"] is True


async def test_a_merged_dock_is_not_classified_twice(db_session):
    """Absorbed docks are aliases, not places. Classifying them would double-count."""
    client_id = await _seed_client(db_session)
    await _seed_shop(db_session, client_id, "Capital Ford", "900 Dealer Dr, Austin, TX")
    alias = await resolve_location(db_session, address="900 Dealer Drive, Austin, TX")

    canonical = await db_session.scalar(
        select(Location).where(Location.normalized_address == "900 dealer dr, austin, tx")
    )
    alias.merged_into_id = canonical.id
    await db_session.flush()

    result = await classify_unlabelled_locations(db_session)

    assert result["considered"] == 1, "the alias should not have been considered"
    await db_session.refresh(alias)
    assert alias.node_class is None
