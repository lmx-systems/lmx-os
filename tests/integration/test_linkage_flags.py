"""REC-4: noticing that two pieces of work are about the same thing.

The done-when names one case specifically - *"the open-return-and-reorder case
fires"* - and that is the first test here. The field logs are the reason: an
$82 core part missed on two visits and never collected. Nothing was broken. The
delivery record was right and the return record was right, and no query put
them side by side before the van left.

The rule running through all of it: a flag is a question, never an action.
Every one of these has a legitimate explanation available, so the tests check
that a flag is *raised* and that nothing is done about it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.hub import Hub
from app.models.linkage_flag import (
    KIND_DUPLICATE_BRANCH,
    KIND_OPEN_RETURN,
    KIND_REPEAT_VISIT,
    LinkageFlag,
)
from app.models.order import Order, OrderStatus
from app.models.return_item import ReturnItem
from app.models.shop import Shop
from app.record import (
    flag_duplicates_across_branches,
    flag_open_returns_on_visits,
    flag_repeat_visits,
    open_flags,
    resolve_flag,
    run_linkage_detectors,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)


async def _hub_and_client(db_session):
    hub = Hub(id=uuid.uuid4(), name="Linkage Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(hub_id=hub.id, name="Linkage Client", pos_system="flat_file")
    db_session.add(client)
    await db_session.flush()
    return hub, client


async def _shop(db_session, client, *, account_ref: str, name="Shop") -> Shop:
    shop = Shop(
        client_id=client.id, name=name, address=f"{name}, Austin, TX 78702",
        lat=30.26, lng=-97.74, external_ref=account_ref,
    )
    db_session.add(shop)
    await db_session.flush()
    return shop


async def _order(db_session, hub, client, shop, *, ref: str, created=NOW, delivered=None):
    order = Order(
        hub_id=hub.id, client_id=client.id, shop_id=shop.id,
        external_order_ref=ref, source_system="flat_file", raw_payload={},
        sla_tier="T2", status=OrderStatus.held, requested_at=created,
        created_at=created, delivered_at=delivered,
    )
    db_session.add(order)
    await db_session.flush()
    return order


class TestOpenReturnOnAVisit:
    """The done-when, and the $82 core part in general form."""

    async def test_a_waiting_return_and_an_undelivered_order_to_the_same_shop(self, db_session):
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        order = await _order(db_session, hub, client, shop, ref="PO-1")
        pending = ReturnItem(
            hub_id=hub.id, shop_id=shop.id, manifest="Core unit, $82", status="expected",
            created_at=NOW - timedelta(days=3),
        )
        db_session.add(pending)
        await db_session.flush()

        raised = await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW)

        assert raised == 1
        flag = (await db_session.scalars(select(LinkageFlag))).one()
        assert flag.kind == KIND_OPEN_RETURN
        assert flag.subjects["order_id"] == str(order.id)
        assert flag.subjects["return_item_id"] == str(pending.id)
        assert "3 day(s)" in flag.detail
        assert "Core unit, $82" in flag.detail

    async def test_a_collected_return_is_not_flagged(self, db_session):
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1")
        db_session.add(
            ReturnItem(
                hub_id=hub.id, shop_id=shop.id, manifest="Already picked up",
                status="collected", collected_at=NOW - timedelta(days=1),
                created_at=NOW - timedelta(days=2),
            )
        )
        await db_session.flush()

        assert await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW) == 0

    async def test_a_delivered_order_is_not_flagged(self, db_session):
        """A trip that already happened is not something a dispatcher can act
        on, and a queue full of those is a queue nobody reads."""
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1", delivered=NOW - timedelta(hours=2))
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=shop.id, manifest="Core unit",
                       status="expected", created_at=NOW - timedelta(days=1))
        )
        await db_session.flush()

        assert await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW) == 0

    async def test_a_return_at_a_different_shop_is_not_flagged(self, db_session):
        hub, client = await _hub_and_client(db_session)
        visiting = await _shop(db_session, client, account_ref="1234/5", name="Visiting")
        elsewhere = await _shop(db_session, client, account_ref="9999/1", name="Elsewhere")
        await _order(db_session, hub, client, visiting, ref="PO-1")
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=elsewhere.id, manifest="Core unit",
                       status="expected", created_at=NOW - timedelta(days=1))
        )
        await db_session.flush()

        assert await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW) == 0

    async def test_a_very_old_return_is_a_report_not_a_flag(self, db_session):
        """Past a month, the question stops being "we are going there anyway"
        and becomes "why has nobody collected this at all" - which is a
        different conversation with a different owner."""
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1")
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=shop.id, manifest="Long forgotten",
                       status="expected", created_at=NOW - timedelta(days=120))
        )
        await db_session.flush()

        assert await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW) == 0


class TestDuplicateAcrossBranches:
    async def test_one_reference_ordered_by_two_branches_of_an_account(self, db_session):
        hub, client = await _hub_and_client(db_session)
        north = await _shop(db_session, client, account_ref="1234/5", name="North")
        south = await _shop(db_session, client, account_ref="1234/6", name="South")
        await _order(db_session, hub, client, north, ref="PO-77")
        await _order(db_session, hub, client, south, ref="PO-77")

        raised = await flag_duplicates_across_branches(db_session, hub_id=hub.id, now=NOW)

        assert raised == 1
        flag = (await db_session.scalars(select(LinkageFlag))).one()
        assert flag.kind == KIND_DUPLICATE_BRANCH
        assert flag.subjects["account_root"] == "1234"
        assert sorted(flag.subjects["branches"]) == ["1234/5", "1234/6"]
        assert "billed twice" in flag.detail

    async def test_two_orders_at_one_branch_are_not_this_flag(self, db_session):
        """A re-send to the same branch is what idempotent intake handles. This
        flag is about two branches, and conflating them would bury the case it
        exists for under retries."""
        hub, client = await _hub_and_client(db_session)
        north = await _shop(db_session, client, account_ref="1234/5", name="North")
        await _order(db_session, hub, client, north, ref="PO-77")
        await _order(db_session, hub, client, north, ref="PO-77")

        assert await flag_duplicates_across_branches(db_session, hub_id=hub.id, now=NOW) == 0

    async def test_different_accounts_sharing_a_reference_are_not_flagged(self, db_session):
        """Two unrelated customers can both call something PO-77."""
        hub, client = await _hub_and_client(db_session)
        one = await _shop(db_session, client, account_ref="1111/1", name="One")
        two = await _shop(db_session, client, account_ref="2222/1", name="Two")
        await _order(db_session, hub, client, one, ref="PO-77")
        await _order(db_session, hub, client, two, ref="PO-77")

        assert await flag_duplicates_across_branches(db_session, hub_id=hub.id, now=NOW) == 0

    async def test_an_unparseable_account_id_is_skipped_not_guessed(self, db_session):
        hub, client = await _hub_and_client(db_session)
        one = await _shop(db_session, client, account_ref="not-an-id", name="One")
        two = await _shop(db_session, client, account_ref="also-not", name="Two")
        await _order(db_session, hub, client, one, ref="PO-77")
        await _order(db_session, hub, client, two, ref="PO-77")

        assert await flag_duplicates_across_branches(db_session, hub_id=hub.id, now=NOW) == 0


class TestRepeatVisits:
    async def test_two_orders_to_one_shop_on_one_day(self, db_session):
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1", created=NOW)
        await _order(db_session, hub, client, shop, ref="PO-2", created=NOW + timedelta(hours=3))

        raised = await flag_repeat_visits(db_session, hub_id=hub.id, now=NOW + timedelta(hours=4))

        assert raised == 1
        flag = (await db_session.scalars(select(LinkageFlag))).one()
        assert flag.kind == KIND_REPEAT_VISIT
        assert len(flag.subjects["order_ids"]) == 2

    async def test_orders_on_different_days_are_not_a_repeat_visit(self, db_session):
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1", created=NOW)
        await _order(db_session, hub, client, shop, ref="PO-2", created=NOW - timedelta(days=1))

        assert await flag_repeat_visits(db_session, hub_id=hub.id, now=NOW) == 0


class TestTheQueue:
    async def test_running_twice_does_not_raise_the_same_question_twice(self, db_session):
        """A detector that re-raises every cycle produces a list people stop
        reading - the failure IDN-2's merge queue was tuned to avoid."""
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1")
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=shop.id, manifest="Core unit",
                       status="expected", created_at=NOW - timedelta(days=1))
        )
        await db_session.flush()

        first = await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW)
        second = await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW)

        assert (first, second) == (1, 0)
        assert len((await db_session.scalars(select(LinkageFlag))).all()) == 1

    async def test_nothing_is_acted_on_automatically(self, db_session):
        """A flag is a question. Every one of these has a legitimate
        explanation, so raising one must not cancel, merge or reschedule
        anything."""
        hub, client = await _hub_and_client(db_session)
        north = await _shop(db_session, client, account_ref="1234/5", name="North")
        south = await _shop(db_session, client, account_ref="1234/6", name="South")
        one = await _order(db_session, hub, client, north, ref="PO-77")
        two = await _order(db_session, hub, client, south, ref="PO-77")

        await run_linkage_detectors(db_session, hub_id=hub.id, now=NOW)

        await db_session.refresh(one)
        await db_session.refresh(two)
        assert one.status == OrderStatus.held
        assert two.status == OrderStatus.held

    async def test_resolving_records_the_judgement_rather_than_deleting_it(self, db_session):
        hub, client = await _hub_and_client(db_session)
        shop = await _shop(db_session, client, account_ref="1234/5")
        await _order(db_session, hub, client, shop, ref="PO-1")
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=shop.id, manifest="Core unit",
                       status="expected", created_at=NOW - timedelta(days=1))
        )
        await db_session.flush()
        await flag_open_returns_on_visits(db_session, hub_id=hub.id, now=NOW)

        flag = (await open_flags(db_session, hub_id=hub.id))[0]
        await resolve_flag(db_session, flag, note="Customer is keeping it, credit issued")

        assert await open_flags(db_session, hub_id=hub.id) == []
        # Still there, with the reasoning attached.
        stored = (await db_session.scalars(select(LinkageFlag))).one()
        assert stored.is_open is False
        assert "credit issued" in stored.resolution_note

    async def test_all_three_run_together(self, db_session):
        hub, client = await _hub_and_client(db_session)
        north = await _shop(db_session, client, account_ref="1234/5", name="North")
        south = await _shop(db_session, client, account_ref="1234/6", name="South")
        await _order(db_session, hub, client, north, ref="PO-77")
        await _order(db_session, hub, client, south, ref="PO-77")
        await _order(db_session, hub, client, north, ref="PO-88")
        db_session.add(
            ReturnItem(hub_id=hub.id, shop_id=north.id, manifest="Core unit",
                       status="expected", created_at=NOW - timedelta(days=1))
        )
        await db_session.flush()

        counts = await run_linkage_detectors(db_session, hub_id=hub.id, now=NOW)

        assert counts[KIND_OPEN_RETURN] == 2, "two undelivered orders at the shop with a return"
        assert counts[KIND_DUPLICATE_BRANCH] == 1
        assert counts[KIND_REPEAT_VISIT] == 1, "two orders to North on one day"
