"""
Finding one order among thousands (docs/ROADMAP.md W5, story CP-3).

`GET /client/orders` returned **every order the client had ever placed** - no search, no
filter, no limit. Two problems in one endpoint: a full scan that grows forever, and no
way for a counter person with a customer on the phone to find the order they are being
asked about. CP-3's target is ten seconds.

Three of these tests are the ones worth having:

  - **`test_a_percent_sign_does_not_match_everything`.** The obvious implementation
    interpolates the search term into a LIKE pattern, and then `%` matches every order
    the client has while `_` matches any single character. A search box that silently
    stops being a search box.
  - **`test_search_is_scoped_to_the_caller`.** The whole surface is one company's data.
    A matching order belonging to another client must not be reachable by guessing a
    reference.
  - **`test_the_total_counts_the_filtered_set`.** A total that ignored the filter would
    read "312 orders" above the three that matched, which is how someone concludes the
    search is broken.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.client_routes import list_my_orders
from app.client_auth.dependencies import AuthedClient
from app.models.client import Client
from app.models.client_user import CLIENT_MEMBER_ROLE
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.shop import Shop
from app.models.stop import Stop, StopOrder

pytestmark = pytest.mark.integration


async def _seed(db_session):
    hub_id, client_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Hub(id=hub_id, name="Austin Hub", lat=30.267, lng=-97.743))
    await db_session.commit()
    db_session.add(
        Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="client_portal")
    )
    await db_session.commit()
    return hub_id, client_id


async def _shop(db_session, client_id, name):
    shop_id = uuid.uuid4()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name=name,
            address=f"{name} Harbor St",
            lat=30.264,
            lng=-97.730,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()
    return shop_id


async def _order(
    db_session,
    hub_id,
    client_id,
    shop_id,
    *,
    ours="ORD-1",
    theirs="THEIRS-1",
    contact="Jim Nguyen",
    address="900 Congress Ave",
    status=OrderStatus.received,
    age_minutes=0,
    with_drop=True,
):
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=ours,
        source_order_ref=theirs,
        source_system="client_portal",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=status,
        requested_at=now - timedelta(minutes=age_minutes),
        delivered_at=now if status == OrderStatus.delivered else None,
        delivery_address=address,
        delivery_contact_name=contact,
        delivery_lat=30.30 if with_drop else None,
        delivery_lng=-97.80 if with_drop else None,
    )
    db_session.add(order)
    await db_session.commit()
    return order


def _authed(client_id) -> AuthedClient:
    """A counter person, not the owner.

    `member` is C4's read-only role, and W5 was written up as needing a decision about
    whether counter staff get their own logins. They already can - so these tests use the
    role the persona would actually have rather than an admin.
    """
    return AuthedClient(
        client_id=str(client_id),
        client_user_id=str(uuid.uuid4()),
        email="counter@example.com",
        name="Alex at the counter",
        role=CLIENT_MEMBER_ROLE,
    )


async def _search(db_session, client_id, **kwargs):
    params = dict(q=None, status="all", limit=50, offset=0)
    params.update(kwargs)
    return await list_my_orders(client=_authed(client_id), session=db_session, **params)


# ---------------------------------------------------------------------------
# Finding it
# ---------------------------------------------------------------------------


async def test_search_finds_an_order_by_our_reference(db_session):
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-7C2A9F")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-000000", theirs="OTHER")

    page = await _search(db_session, client_id, q="7c2a9f")
    assert [o.external_order_ref for o in page.items] == ["LMX-7C2A9F"]


async def test_search_finds_an_order_by_their_own_reference(db_session):
    """The one that matters most of the five fields.

    A counter person knows the number on their own paperwork, not ours. This is the search
    they will actually type.
    """
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-1", theirs="WO-44718")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-2", theirs="WO-99999")

    page = await _search(db_session, client_id, q="44718")
    assert [o.external_order_ref for o in page.items] == ["LMX-1"]


async def test_search_finds_orders_by_shop_name(db_session):
    hub_id, client_id = await _seed(db_session)
    riverside = await _shop(db_session, client_id, "Riverside Depot")
    braker = await _shop(db_session, client_id, "Braker Lane")
    await _order(db_session, hub_id, client_id, riverside, ours="LMX-R1", theirs="A")
    await _order(db_session, hub_id, client_id, riverside, ours="LMX-R2", theirs="B")
    await _order(db_session, hub_id, client_id, braker, ours="LMX-B1", theirs="C")

    page = await _search(db_session, client_id, q="riverside")
    assert page.total == 2
    assert {o.external_order_ref for o in page.items} == {"LMX-R1", "LMX-R2"}


async def test_search_finds_orders_by_customer_name_and_address(db_session):
    """What a counter person often has instead of a number: who it is for, or where."""
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(
        db_session, hub_id, client_id, shop_id, ours="LMX-C", theirs="A", contact="Dana Whitfield"
    )
    await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        ours="LMX-A",
        theirs="B",
        contact="Someone Else",
        address="4200 Speedway Blvd",
    )

    by_name = await _search(db_session, client_id, q="whitfield")
    assert [o.external_order_ref for o in by_name.items] == ["LMX-C"]

    by_address = await _search(db_session, client_id, q="speedway")
    assert [o.external_order_ref for o in by_address.items] == ["LMX-A"]


# ---------------------------------------------------------------------------
# Not finding what it must not find
# ---------------------------------------------------------------------------


async def test_a_percent_sign_does_not_match_everything(db_session):
    """LIKE metacharacters are escaped.

    Interpolating the term straight into a pattern makes `%` match every order and `_`
    match any single character - so the search box silently stops filtering, at exactly
    the moment someone is trying to narrow down a long list.
    """
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-1", theirs="A")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-2", theirs="B")

    assert (await _search(db_session, client_id, q="%")).total == 0
    assert (await _search(db_session, client_id, q="_")).total == 0
    assert (await _search(db_session, client_id, q="%%%")).total == 0


async def test_a_literal_percent_is_still_searchable(db_session):
    """Escaping must not make the character unfindable - an address or a reference can
    legitimately contain one."""
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-1", theirs="DISCOUNT-50%")
    await _order(db_session, hub_id, client_id, shop_id, ours="LMX-2", theirs="PLAIN")

    page = await _search(db_session, client_id, q="50%")
    assert [o.external_order_ref for o in page.items] == ["LMX-1"]


async def test_search_is_scoped_to_the_caller(db_session):
    """Another company's order is not reachable by guessing its reference."""
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="MINE-1", theirs="A")

    other_id = uuid.uuid4()
    db_session.add(
        Client(id=other_id, hub_id=hub_id, name="Someone Else", pos_system="client_portal")
    )
    await db_session.commit()
    other_shop = await _shop(db_session, other_id, "Their Shop")
    await _order(db_session, hub_id, other_id, other_shop, ours="THEIRS-1", theirs="Z")

    page = await _search(db_session, client_id, q="1")
    assert [o.external_order_ref for o in page.items] == ["MINE-1"]
    assert page.total == 1


# ---------------------------------------------------------------------------
# Paging, and admitting that it is paging
# ---------------------------------------------------------------------------


async def test_the_endpoint_is_bounded_by_default(db_session):
    """The defect this replaces. It used to return everything, forever."""
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    for index in range(12):
        await _order(
            db_session, hub_id, client_id, shop_id, ours=f"LMX-{index}", theirs=f"T-{index}"
        )

    page = await _search(db_session, client_id, limit=5)
    assert len(page.items) == 5
    assert page.total == 12
    assert page.limit == 5
    assert page.offset == 0


async def test_paging_walks_the_whole_set_without_repeating(db_session):
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    for index in range(7):
        await _order(
            db_session,
            hub_id,
            client_id,
            shop_id,
            ours=f"LMX-{index}",
            theirs=f"T-{index}",
            # Distinct request times, so ordering is deterministic rather than
            # incidental - two orders sharing a timestamp could page inconsistently.
            age_minutes=index,
        )

    seen: list[str] = []
    for offset in (0, 3, 6):
        page = await _search(db_session, client_id, limit=3, offset=offset)
        seen.extend(o.external_order_ref for o in page.items)
    assert len(seen) == 7
    assert len(set(seen)) == 7


async def test_the_total_counts_the_filtered_set(db_session):
    """Not the whole table.

    A total that ignored the filter would print "12 orders" above the two that matched,
    which reads as the search being broken.
    """
    hub_id, client_id = await _seed(db_session)
    riverside = await _shop(db_session, client_id, "Riverside Depot")
    braker = await _shop(db_session, client_id, "Braker Lane")
    for index in range(10):
        await _order(
            db_session, hub_id, client_id, braker, ours=f"B-{index}", theirs=f"T-{index}"
        )
    for index in range(2):
        await _order(
            db_session, hub_id, client_id, riverside, ours=f"R-{index}", theirs=f"S-{index}"
        )

    page = await _search(db_session, client_id, q="riverside", limit=50)
    assert page.total == 2


async def test_status_open_excludes_finished_orders(db_session):
    """What a counter person wants by default: the ones still happening."""
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, ours="LIVE", theirs="A")
    await _order(
        db_session,
        hub_id,
        client_id,
        shop_id,
        ours="DONE",
        theirs="B",
        status=OrderStatus.delivered,
    )

    assert {o.external_order_ref for o in (await _search(db_session, client_id)).items} == {
        "LIVE",
        "DONE",
    }
    open_page = await _search(db_session, client_id, status="open")
    assert [o.external_order_ref for o in open_page.items] == ["LIVE"]


# ---------------------------------------------------------------------------
# The number a counter person is really being asked for
# ---------------------------------------------------------------------------


async def test_the_commitment_and_the_estimate_are_both_returned(db_session):
    """`collect_by` is the promise; `estimated_delivery_by` is not.

    Before this the summary carried neither, so "get live status and ETA in ten seconds"
    was missing the ETA entirely.
    """
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    order = await _order(db_session, hub_id, client_id, shop_id)

    view = (await _search(db_session, client_id)).items[0]
    assert view.collect_by == order.hold_deadline.isoformat()
    # Pre-route: a straight-line estimate from the commitment, so it is later than it.
    assert view.estimated_delivery_by is not None
    assert view.estimated_delivery_by > view.collect_by


async def test_the_route_aware_eta_wins_once_the_order_is_on_a_route(db_session):
    """The point of surfacing one number here.

    `Stop.eta` is walked along the sequence the driver will actually drive and refreshed as
    the route progresses. A counter person quoting a customer must not be reading a
    straight-line guess while the driver's own app shows something else.
    """
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    order = await _order(db_session, hub_id, client_id, shop_id, status=OrderStatus.picked_up)

    from app.models.driver import Driver

    driver_id = uuid.uuid4()
    db_session.add(
        Driver(
            id=driver_id,
            hub_id=hub_id,
            name="Sam O.",
            phone=f"+1512555{uuid.uuid4().int % 9000:04d}",
        )
    )
    await db_session.flush()
    route = Route(hub_id=hub_id, driver_id=driver_id, status="active")
    db_session.add(route)
    await db_session.flush()

    routed_eta = datetime.now(timezone.utc) + timedelta(hours=4)
    stop = Stop(
        route_id=route.id,
        shop_id=None,
        sequence=1,
        stop_type="dropoff",
        parcel_count=1,
        eta=routed_eta,
    )
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    await db_session.commit()

    view = (await _search(db_session, client_id)).items[0]
    assert view.estimated_delivery_by == routed_eta.isoformat()


async def test_an_order_with_no_geocoded_drop_has_no_estimate(db_session):
    """Silence rather than invention, the same rule the confirmation screen follows.

    With no destination there is nothing to estimate from, and guessing would be inventing
    twice over.
    """
    hub_id, client_id = await _seed(db_session)
    shop_id = await _shop(db_session, client_id, "Midtown Auto Parts")
    await _order(db_session, hub_id, client_id, shop_id, with_drop=False)

    view = (await _search(db_session, client_id)).items[0]
    assert view.estimated_delivery_by is None
    assert view.collect_by is not None
