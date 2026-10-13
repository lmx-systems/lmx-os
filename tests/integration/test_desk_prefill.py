"""DE-15's desk facts in the database: written only where empty, and never grading a record above bronze.

`app/record/desk_prefill.py` and migration 0080. The rules that produce a desk
fact are tested offline (`tests/test_desk_prefill.py`); these test what writing
one does - to the site, and to the grade of every record that stands on it.
"""
import uuid

import pytest
from sqlalchemy import select, text

from app.identity import resolve_location
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.location import Location
from app.models.route import Route
from app.models.stop import Stop
from app.record.desk_prefill import DESK, SiteEvidence, apply_desk_facts
from tests.integration.test_record_tiers import _a_delivery, _handoff_point, _site, _tier

pytestmark = pytest.mark.integration


async def _bare_site(db_session, region="NJ") -> Location:
    location = await resolve_location(db_session, address=f"{uuid.uuid4().int % 9000 + 100} Desk Ave, Sometown, NJ 07000")
    location.region = region
    await db_session.flush()
    return location


def _desk(location_id, *, kind="body_shop", setting="suburb", shares=True, matched=True) -> SiteEvidence:
    return SiteEvidence(location_id=location_id, matched=matched, kind_of_site=kind, setting=setting, shares_address=shares)


async def _from_the_desk(db_session, location: Location, *facts: str) -> None:
    location.site_fact_sources = {**(location.site_fact_sources or {}), **{f: DESK for f in facts}}
    await db_session.commit()


async def test_desk_facts_fill_an_empty_site_and_say_where_they_came_from(db_session):
    site = await _bare_site(db_session)

    report = await apply_desk_facts(db_session, [_desk(site.id)])
    await db_session.commit()

    row = (await db_session.execute(select(Location).where(Location.id == site.id))).scalar_one()
    assert (row.kind_of_site, row.setting, row.shares_address) == ("body_shop", "suburb", True)
    assert row.site_fact_sources == {"kind_of_site": DESK, "setting": DESK, "shares_address": DESK}
    # Each fact says desk; the site-wide source is not the desk's to set.
    assert row.site_source is None
    assert report.filled["setting"] == 1
    assert report.place_known_after == 1


async def test_a_fact_already_known_is_never_overwritten_only_compared(db_session):
    """The import's guess stays; where the desk would have said otherwise, that
    is counted - the first measure of how good the desk is."""
    site = await _bare_site(db_session)
    site.kind_of_site = "dealership_counter"
    site.shares_address = True
    site.site_source = "import"
    await db_session.flush()

    report = await apply_desk_facts(db_session, [_desk(site.id, kind="body_shop", shares=True)])
    await db_session.commit()

    row = (await db_session.execute(select(Location).where(Location.id == site.id))).scalar_one()
    assert (row.kind_of_site, row.shares_address, row.setting) == ("dealership_counter", True, "suburb")
    assert row.site_source == "import"
    assert row.site_fact_sources == {"setting": DESK}
    assert (report.disagrees["kind_of_site"], report.agrees["shares_address"]) == (1, 1)


async def test_what_public_data_does_not_know_stays_unknown(db_session):
    site = await _bare_site(db_session)

    report = await apply_desk_facts(db_session, [_desk(site.id, kind=None, setting=None, shares=None, matched=False)])
    await db_session.commit()

    row = (await db_session.execute(select(Location).where(Location.id == site.id))).scalar_one()
    assert (row.kind_of_site, row.setting, row.shares_address, row.site_fact_sources) == (None, None, None, None)
    assert report.unknown["kind_of_site"] == 1
    assert report.matched == 0


async def test_running_it_twice_changes_nothing_the_second_time(db_session):
    site = await _bare_site(db_session)
    await apply_desk_facts(db_session, [_desk(site.id)])
    await db_session.commit()

    report = await apply_desk_facts(db_session, [_desk(site.id, kind="parts_store_counter")])

    assert sum(report.filled.values()) == 0
    assert report.already_known["kind_of_site"] == 1


async def test_a_gold_record_on_a_desk_fact_is_bronze(db_session):
    """Every element captured, but the drop-off site's setting is the desk's guess."""
    drop_site = await _site(db_session, "Desk Rd")
    await _from_the_desk(db_session, drop_site, "setting")

    row = await _tier(db_session, await _a_delivery(db_session, drop_site=drop_site))

    assert row["elements_missing"] == 0
    assert row["desk_site"] is True
    assert row["tier"] == "bronze"


async def test_a_silver_record_on_a_desk_fact_is_bronze_and_a_bronze_one_stays(db_session):
    drop_site = await _site(db_session, "Desk Rd")
    await _from_the_desk(db_session, drop_site, "kind_of_site")

    silver = await _tier(db_session, await _a_delivery(db_session, drop_site=drop_site, labelled=False))
    bronze = await _tier(
        db_session, await _a_delivery(db_session, drop_site=drop_site, labelled=False, item_weight_kg=None)
    )

    assert (silver["elements_missing"], silver["tier"]) == (1, "bronze")
    assert (bronze["elements_missing"], bronze["tier"]) == (2, "bronze")


async def test_a_desk_fact_on_an_end_that_does_not_count_caps_nothing(db_session):
    """The drop-off's door is not captured, so its site facts decide nothing,
    desk or not: the record is graded on what it has."""
    drop_site = await _site(db_session, "Desk Rd")
    await _from_the_desk(db_session, drop_site, "setting")

    row = await _tier(db_session, await _a_delivery(db_session, drop_site=drop_site, drop_photo_handoff=None))

    assert row["has_dropoff"] is False
    assert row["desk_site"] is False
    assert row["tier"] == "silver"


async def test_a_desk_fact_at_the_pickup_caps_it_too(db_session):
    stop_id = await _a_delivery(db_session)
    pickup_site_id = (
        await db_session.execute(
            text(
                "SELECT hp.location_id FROM stop_orders d "
                "JOIN stop_orders p ON p.order_id = d.order_id AND p.stop_id <> d.stop_id "
                "JOIN stops ps ON ps.id = p.stop_id AND ps.stop_type = 'pickup' "
                "JOIN handoff_points hp ON hp.id = ps.handoff_point_id WHERE d.stop_id = :s"
            ),
            {"s": stop_id},
        )
    ).scalar_one()
    pickup_site = (await db_session.execute(select(Location).where(Location.id == pickup_site_id))).scalar_one()
    await _from_the_desk(db_session, pickup_site, "shares_address")

    row = await _tier(db_session, stop_id)

    assert (row["desk_site"], row["tier"]) == (True, "bronze")


async def test_a_person_confirming_the_fact_lifts_the_cap(db_session):
    drop_site = await _site(db_session, "Desk Rd")
    await _from_the_desk(db_session, drop_site, "setting")
    stop_id = await _a_delivery(db_session, drop_site=drop_site)
    assert (await _tier(db_session, stop_id))["tier"] == "bronze"

    drop_site.site_fact_sources = {"setting": "technician"}
    await db_session.commit()

    assert (await _tier(db_session, stop_id))["tier"] == "gold"


async def test_a_visit_whose_door_counts_on_a_desk_fact_is_bronze_not_silver(db_session):
    """D-VISIT grades a visit with its door captured silver; a desk fact under
    that door makes it bronze."""
    site = await _bare_site(db_session)
    await apply_desk_facts(db_session, [_desk(site.id)])
    point = _handoff_point(site)
    hub = Hub(id=uuid.uuid4(), name="Re-drive visits (NJ)", lat=40.0, lng=-74.0, active=False)
    db_session.add_all([point, hub])
    await db_session.flush()
    driver = Driver(hub_id=hub.id, name="Tech", phone=f"+1555{uuid.uuid4().int % 10**7:07d}")
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="completed")
    db_session.add(route)
    await db_session.flush()
    visit = Stop(
        route_id=route.id, stop_type="visit", sequence=1, status="completed",
        handoff_point_id=point.id, record_source="redrive",
    )
    db_session.add(visit)
    await db_session.commit()

    row = await _tier(db_session, visit.id)

    assert row["has_dropoff"] is True
    assert (row["desk_site"], row["tier"]) == (True, "bronze")
