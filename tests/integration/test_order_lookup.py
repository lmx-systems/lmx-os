"""CON-1: a dispatcher can find an order.

*"A working dispatcher can run a day on it."*

They could not. The hold queue's search box filters the held list, so an order
already released, assigned or delivered was unfindable — while
`GET /client/orders?q=` has let the **customer** search their own orders all
along. The person phoning could find it; the person answering the phone could
not.

The tests worth reading are the ones about what a search must not do: cross a
hub boundary, or report an order as on time when it is late and still out.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.client import Client
from app.models.hub import Hub
from app.models.ops_user import VIEWER_ROLE
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.ops_auth.dependencies import AuthedOpsUser

pytestmark = pytest.mark.integration

OPS = AuthedOpsUser(
    ops_user_id="u1", email="dispatcher@lmxit.com", name="Dispatcher", role=VIEWER_ROLE
)


async def _hub(db_session) -> Hub:
    hub = Hub(id=uuid.uuid4(), name="Lookup Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(
        id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file"
    )
    db_session.add(client)
    await db_session.flush()
    shop = Shop(
        id=uuid.uuid4(), client_id=client.id, name="Riverside Motors",
        address="1200 E 6th St", lat=30.26, lng=-97.73, external_ref=f"S-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(shop)
    await db_session.flush()
    hub._client_id, hub._shop_id = client.id, shop.id
    return hub


async def _order(
    db_session, hub, *, ref="PO-4471", status=OrderStatus.assigned,
    promised_minutes_ago=None, delivered=False, contact=None, address=None,
) -> Order:
    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub.id, client_id=hub._client_id, shop_id=hub._shop_id,
        external_order_ref=ref, source_order_ref=f"SRC-{ref}",
        source_system="flat_file", raw_payload={}, sla_tier="T2", status=status,
        requested_at=now - timedelta(hours=2),
        promised_at=(
            now - timedelta(minutes=promised_minutes_ago)
            if promised_minutes_ago is not None else None
        ),
        delivered_at=now if delivered else None,
        delivery_contact_name=contact,
        delivery_address=address,
    )
    db_session.add(order)
    await db_session.flush()
    return order


class TestADispatcherCanFindIt:
    async def test_an_assigned_order_is_findable(self, db_session):
        """The gap, in one assertion. This order is not in the hold queue, so
        before this it could not be found at all."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(db_session, hub, ref="PO-4471", status=OrderStatus.assigned)

        page = await lookup_orders(
            hub_id=hub.id, q="4471", limit=25, session=db_session, _ops=OPS
        )

        assert page.total == 1
        assert page.items[0].external_ref == "PO-4471"
        assert page.items[0].status == "assigned"

    async def test_a_delivered_order_is_findable(self, db_session):
        """"Where was my delivery yesterday" is a question a dispatcher gets."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(
            db_session, hub, ref="PO-9", status=OrderStatus.delivered, delivered=True
        )

        page = await lookup_orders(
            hub_id=hub.id, q="PO-9", limit=25, session=db_session, _ops=OPS
        )

        assert page.total == 1
        assert page.items[0].delivered_at is not None

    @pytest.mark.parametrize(
        "term,kwargs",
        [
            ("4471", {}),
            ("SRC-PO-4471", {}),
            ("Riverside", {}),
            ("Alex Rivera", {"contact": "Alex Rivera"}),
            ("Congress", {"address": "900 Congress Ave"}),
        ],
    )
    async def test_it_matches_whichever_reference_the_caller_quotes(
        self, db_session, term, kwargs
    ):
        """A caller quotes what they have to hand, and which one that is is not
        something we get to choose. Same fields the client-facing search uses,
        for the same reason."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(db_session, hub, ref="PO-4471", **kwargs)

        page = await lookup_orders(
            hub_id=hub.id, q=term, limit=25, session=db_session, _ops=OPS
        )

        assert page.total == 1, f"{term!r} should find it"

    async def test_the_newest_match_comes_first(self, db_session):
        """A reference quoted on the phone is nearly always recent, and a
        dispatcher scanning for it should not start in March."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        old = await _order(db_session, hub, ref="PO-100")
        old.requested_at = datetime.now(timezone.utc) - timedelta(days=30)
        recent = await _order(db_session, hub, ref="PO-101")
        await db_session.flush()

        page = await lookup_orders(
            hub_id=hub.id, q="PO-10", limit=25, session=db_session, _ops=OPS
        )

        assert [i.order_id for i in page.items] == [recent.id, old.id]


class TestWhatItMustNotDo:
    async def test_it_never_crosses_a_hub(self, db_session):
        """A dispatcher works a hub. Returning another hub's orders would show
        them work they cannot act on and customers that are not theirs."""
        from app.api.routes import lookup_orders

        mine = await _hub(db_session)
        elsewhere = await _hub(db_session)
        await _order(db_session, elsewhere, ref="PO-4471")

        page = await lookup_orders(
            hub_id=mine.id, q="4471", limit=25, session=db_session, _ops=OPS
        )

        assert page.total == 0

    async def test_an_order_still_out_and_past_its_promise_reads_as_late(
        self, db_session
    ):
        """Measured against now, not against a delivery that has not happened.
        A null here would read as on time - which is the answer a dispatcher is
        phoning about."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(
            db_session, hub, ref="PO-LATE", status=OrderStatus.assigned,
            promised_minutes_ago=40,
        )

        page = await lookup_orders(
            hub_id=hub.id, q="PO-LATE", limit=25, session=db_session, _ops=OPS
        )

        assert page.items[0].minutes_late == 40

    async def test_an_order_delivered_inside_its_promise_is_not_late(self, db_session):
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(
            db_session, hub, ref="PO-OK", status=OrderStatus.delivered,
            delivered=True, promised_minutes_ago=-30,
        )

        page = await lookup_orders(
            hub_id=hub.id, q="PO-OK", limit=25, session=db_session, _ops=OPS
        )

        assert page.items[0].minutes_late is None

    async def test_an_order_with_no_promise_is_not_late(self, db_session):
        """Nothing was promised, so nothing was missed. Reporting a lateness
        against a commitment nobody made is the flattering direction's
        opposite - it invents a breach."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        await _order(db_session, hub, ref="PO-NOPROMISE", promised_minutes_ago=None)

        page = await lookup_orders(
            hub_id=hub.id, q="NOPROMISE", limit=25, session=db_session, _ops=OPS
        )

        assert page.items[0].minutes_late is None

    async def test_the_total_says_when_there_is_more_than_a_page(self, db_session):
        """So a dispatcher knows to narrow the term rather than believing the
        first twenty-five are all of them."""
        from app.api.routes import lookup_orders

        hub = await _hub(db_session)
        for index in range(6):
            await _order(db_session, hub, ref=f"PO-8{index:02d}")

        page = await lookup_orders(
            hub_id=hub.id, q="PO-8", limit=2, session=db_session, _ops=OPS
        )

        assert len(page.items) == 2
        assert page.total == 6

    async def test_it_needs_an_ops_session(self):
        """It names customers, contacts and addresses across a whole hub."""
        import inspect

        from app.api.routes import lookup_orders
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(lookup_orders).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user
