"""A record's tier, from how complete it is (`docs/ROADMAP_1.5.md` DE-1, D-TIER).

A record is one delivery or visit. It is graded on five elements - pickup,
drop-off, item, delivery profile, autonomy qualifier - and the tier is the count
of what is missing: none is gold, one silver, two bronze, more reference. The
`record_tiers` view (migration 0069) computes it; these build one fully captured
delivery and take one element away per tier, so each test shows the rule doing
exactly one thing.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.identity import resolve_location
from app.models.client import Client
from app.models.driver import Driver
from app.models.handoff_point import HandoffPoint
from app.models.hub import Hub
from app.models.machine_label import MACHINES, MachineLabel
from app.models.order import Order, OrderStatus
from app.models.receiver_profile import ReceiverProfile
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder

pytestmark = pytest.mark.integration


async def _site(db_session, street: str):
    """A site with every Site-layer fact captured: kind, setting, whether it
    shares its address, receiving hours and who receives."""
    location = await resolve_location(
        db_session, address=f"{uuid.uuid4().int % 9000 + 100} {street}, Austin, TX"
    )
    location.kind_of_site = "repair_shop"
    location.setting = "suburb"
    location.region = "TX"
    location.shares_address = False
    location.site_source = "technician"
    db_session.add(
        ReceiverProfile(
            location_id=location.id,
            receiving_hours={"mon": [["08:00", "17:00"]]},
            who_receives="counter_staff",
        )
    )
    await db_session.flush()
    return location


def _handoff_point(location, **overrides) -> HandoffPoint:
    """A handoff point with every field the definitions need for gold."""
    fields = dict(
        location_id=location.id,
        handoff_type="counter",
        door_lat=30.2672,
        door_lng=-97.7431,
        stop_lat=30.2671,
        stop_lng=-97.7433,
        stop_type="lot",
        stop_legal=True,
        walk_distance_m=18.0,
        steps_or_ramps="none",
        continuous_sidewalk=True,
        obstructions=[],
        drone_open_ground=False,
        overhead=["wires"],
        photo_approach="handoff/approach.jpg",
        photo_stop_point="handoff/stop.jpg",
        photo_path="handoff/path.jpg",
        photo_handoff="handoff/door.jpg",
        source="technician",
        collected_by="tech-1",
        collected_on=date(2026, 10, 6),
    )
    fields.update(overrides)
    return HandoffPoint(**fields)


async def _a_delivery(
    db_session,
    *,
    labelled: bool = True,
    item_weight_kg: float | None = 1.4,
    drop_photo_handoff: str | None = "handoff/door.jpg",
    promised: bool = True,
    drop_site=None,
    scorers: tuple[str, str, str] = ("scorer-1", "scorer-1", "scorer-1"),
):
    """One delivery, every element captured unless a keyword takes one away.
    Returns the drop-off stop's id: the record."""
    now = datetime.now(timezone.utc)
    hub = Hub(id=uuid.uuid4(), name="Tier Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Design Partner", pos_system="flat_file")
    driver = Driver(
        hub_id=hub.id, name="Sam O.", phone=f"+1555{uuid.uuid4().int % 10**7:07d}",
        vehicle_capacity_units=5,
    )
    db_session.add_all([client, driver])
    await db_session.flush()

    pickup_site = await _site(db_session, "Parts Way")
    drop_site = drop_site or await _site(db_session, "Garage Rd")
    pickup_point = _handoff_point(pickup_site)
    drop_point = _handoff_point(drop_site, photo_handoff=drop_photo_handoff)
    db_session.add_all([pickup_point, drop_point])
    await db_session.flush()

    shop = Shop(
        client_id=client.id, name="Counter", address=pickup_site.address,
        lat=30.26, lng=-97.75, location_id=pickup_site.id,
    )
    db_session.add(shop)
    await db_session.flush()
    order = Order(
        hub_id=hub.id, client_id=client.id, shop_id=shop.id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}", source_system="flat_file", raw_payload={},
        sla_tier="T2", hold_deadline=now, weight_units=1, status=OrderStatus.delivered,
        requested_at=now - timedelta(hours=2),
        promised_at=now + timedelta(hours=1) if promised else None,
        delivery_address=drop_site.address, delivery_lat=30.2672, delivery_lng=-97.7431,
        delivery_location_id=drop_site.id,
        item_description="brake caliper", item_weight_kg=item_weight_kg,
        item_size_class="small", item_hazmat_or_liquid=False,
    )
    db_session.add(order)
    await db_session.flush()

    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    pickup = Stop(
        route_id=route.id, shop_id=shop.id, stop_type="pickup", sequence=1, status="completed",
        completed_at=now - timedelta(minutes=40), handoff_point_id=pickup_point.id,
    )
    drop = Stop(
        route_id=route.id, stop_type="dropoff", sequence=2, status="completed",
        completed_at=now - timedelta(minutes=10), handoff_point_id=drop_point.id,
        collected_by=str(driver.id),
    )
    db_session.add_all([pickup, drop])
    await db_session.flush()
    db_session.add_all([StopOrder(stop_id=pickup.id, order_id=order.id), StopOrder(stop_id=drop.id, order_id=order.id)])

    if labelled:
        for machine, scorer in zip(MACHINES, scorers):
            db_session.add(
                MachineLabel(
                    stop_id=drop.id, handoff_point_id=drop_point.id, machine=machine,
                    verdict="no" if machine == "drone" else "yes",
                    reason_codes=["overhead"] if machine == "drone" else [],
                    scorer_id=scorer,
                )
            )
    await db_session.commit()
    return drop.id


async def _tier(db_session, stop_id):
    return (
        await db_session.execute(
            text("SELECT * FROM record_tiers WHERE stop_id = :stop_id"), {"stop_id": stop_id}
        )
    ).mappings().one()


async def test_a_record_with_all_five_elements_is_gold(db_session):
    """Delivered with both ends' handoff points captured, the item weighed, the
    order's urgency and window known, and all three machines scored."""
    row = await _tier(db_session, await _a_delivery(db_session))

    assert row["tier"] == "gold"
    assert row["elements_missing"] == 0
    assert all(
        row[element]
        for element in ("has_pickup", "has_dropoff", "has_item", "has_delivery_profile", "has_autonomy_qualifier")
    )
    # Every record carries its source.
    assert row["record_source"] == "lmx_app"


async def test_a_record_missing_one_element_is_silver(db_session):
    """Nobody has scored it yet: no autonomy qualifier."""
    row = await _tier(db_session, await _a_delivery(db_session, labelled=False))

    assert row["tier"] == "silver"
    assert row["elements_missing"] == 1
    assert row["has_autonomy_qualifier"] is False


async def test_a_record_missing_two_elements_is_bronze(db_session):
    """Unscored, and the item was never weighed. A missing weight is null, never
    the dispatch default of 1.0 - and null is what makes the element missing."""
    row = await _tier(db_session, await _a_delivery(db_session, labelled=False, item_weight_kg=None))

    assert row["tier"] == "bronze"
    assert row["elements_missing"] == 2
    assert (row["has_autonomy_qualifier"], row["has_item"]) == (False, False)


async def test_a_record_missing_three_elements_is_reference(db_session):
    """Unscored, unweighed, and the drop-off's handoff photo is missing - three of
    four photos is not the four-photo set, so the drop-off end is not captured."""
    row = await _tier(
        db_session,
        await _a_delivery(db_session, labelled=False, item_weight_kg=None, drop_photo_handoff=None),
    )

    assert row["tier"] == "reference"
    assert row["elements_missing"] == 3
    assert (row["has_autonomy_qualifier"], row["has_item"], row["has_dropoff"]) == (False, False, False)
    assert row["has_pickup"] is True


async def test_a_merged_location_reads_its_survivors_site_facts(db_session):
    """A handoff point captured at a location later merged into another keeps
    its record gold: the site facts live on the survivor, as identity
    resolution reads them, and the merged row itself holds none."""
    survivor = await _site(db_session, "Survivor Ln")
    merged = await resolve_location(
        db_session, address=f"{uuid.uuid4().int % 9000 + 100} Alias St, Austin, TX"
    )
    merged.merged_into_id = survivor.id
    await db_session.flush()

    row = await _tier(db_session, await _a_delivery(db_session, drop_site=merged))

    assert row["has_dropoff"] is True
    assert row["tier"] == "gold"


async def test_verdicts_from_different_scorers_are_not_one_qualifier(db_session):
    """Three machines scored, but by two people: nobody gave all three verdicts,
    so the record has no autonomy qualifier and is silver."""
    row = await _tier(
        db_session,
        await _a_delivery(db_session, scorers=("scorer-1", "scorer-1", "scorer-2")),
    )

    assert row["has_autonomy_qualifier"] is False
    assert row["tier"] == "silver"
