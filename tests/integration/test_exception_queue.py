"""CON-4: what ops should look at before the phone rings.

The queue's job is to be read by a person under pressure, so most of these
assert the *ordering* and the *next action* rather than the counting. A list
that is merely complete is a list nobody works through.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from sqlalchemy import select

from app.models.client import Client
from app.models.driver import Driver
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.models.route import Route
from app.models.stop import Stop, StopFlag, StopOrder
from app.reporting.exceptions import (
    KIND_FAILED,
    KIND_FLAGGED,
    KIND_PAST_PROMISE,
    KIND_UNPLACED,
    build_exception_queue,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
GRACE = timedelta(seconds=settings.stuck_order_after_seconds)


async def _hub(db_session) -> Hub:
    """A hub with a client on it. Orders carry a real `client_id` because the
    queue names the customer - that is most of what makes it a worklist rather
    than a count."""
    hub = Hub(id=uuid.uuid4(), name="Exception Hub", lat=30.27, lng=-97.74)
    db_session.add(hub)
    await db_session.flush()
    client = Client(
        id=uuid.uuid4(), hub_id=hub.id, name="Design Partner", pos_system="flat_file"
    )
    db_session.add(client)
    await db_session.flush()
    hub._client_id = client.id
    return hub


async def _order(
    db_session, hub, *, status=OrderStatus.assigned, promised_minutes_ago=None,
    requested_minutes_ago=90, delivered=False, failure_reason=None,
) -> Order:
    order = Order(
        hub_id=hub.id, client_id=hub._client_id,
        external_order_ref=f"PO-{uuid.uuid4().hex[:6]}", source_system="flat_file",
        raw_payload={}, sla_tier="T2", status=status,
        requested_at=NOW - timedelta(minutes=requested_minutes_ago),
        promised_at=(
            NOW - timedelta(minutes=promised_minutes_ago)
            if promised_minutes_ago is not None else None
        ),
        delivered_at=NOW if delivered else None,
        failure_reason=failure_reason,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _flag(db_session, hub, order, *, flag_type="shop_closed", note="Shut early"):
    driver = Driver(hub_id=hub.id, name="D", phone=f"+1512555{uuid.uuid4().hex[:4]}")
    db_session.add(driver)
    await db_session.flush()
    route = Route(hub_id=hub.id, driver_id=driver.id, status="active")
    db_session.add(route)
    await db_session.flush()
    stop = Stop(route_id=route.id, sequence=1, stop_type="dropoff", parcel_count=1)
    db_session.add(stop)
    await db_session.flush()
    db_session.add(StopOrder(stop_id=stop.id, order_id=order.id))
    db_session.add(
        StopFlag(
            stop_id=stop.id, flag_type=flag_type, note=note,
            created_by_driver_id=driver.id,
        )
    )
    await db_session.flush()


async def _queue(db_session, hub):
    return await build_exception_queue(db_session, hub_id=hub.id, now=NOW)


class TestTheFourKinds:
    async def test_a_driver_flag_on_an_open_order_surfaces(self, db_session):
        """The earliest warning there is - somebody was physically there and the
        customer usually does not know yet."""
        hub = await _hub(db_session)
        order = await _order(db_session, hub)
        await _flag(db_session, hub, order)
        queue = await _queue(db_session, hub)
        assert [i.kind for i in queue.items] == [KIND_FLAGGED]
        assert "Shut early" in queue.items[0].detail

    async def test_a_flag_on_a_delivered_order_is_history_not_an_exception(
        self, db_session
    ):
        hub = await _hub(db_session)
        order = await _order(db_session, hub, delivered=True)
        await _flag(db_session, hub, order)
        assert (await _queue(db_session, hub)).items == []

    async def test_a_failed_delivery_surfaces(self, db_session):
        hub = await _hub(db_session)
        await _order(
            db_session, hub, status=OrderStatus.delivery_failed,
            failure_reason="access_blocked",
        )
        queue = await _queue(db_session, hub)
        assert [i.kind for i in queue.items] == [KIND_FAILED]
        assert "access_blocked" in queue.items[0].detail

    async def test_past_the_promise_and_still_open_surfaces(self, db_session):
        hub = await _hub(db_session)
        await _order(db_session, hub, promised_minutes_ago=120)
        queue = await _queue(db_session, hub)
        assert [i.kind for i in queue.items] == [KIND_PAST_PROMISE]

    async def test_released_but_never_placed_surfaces(self, db_session):
        """The quietest failure: no driver has it, nothing went wrong, and it
        looks like an order in transit from every other view."""
        hub = await _hub(db_session)
        await _order(db_session, hub, status=OrderStatus.queued, requested_minutes_ago=90)
        queue = await _queue(db_session, hub)
        assert [i.kind for i in queue.items] == [KIND_UNPLACED]


class TestWhatItLeavesAlone:
    async def test_a_delivered_order_is_not_an_exception(self, db_session):
        hub = await _hub(db_session)
        await _order(db_session, hub, delivered=True, promised_minutes_ago=300)
        assert (await _queue(db_session, hub)).items == []

    async def test_a_cancelled_order_is_not_chased(self, db_session):
        hub = await _hub(db_session)
        await _order(
            db_session, hub, status=OrderStatus.cancelled, promised_minutes_ago=300
        )
        assert (await _queue(db_session, hub)).items == []

    async def test_inside_the_grace_it_stays_quiet(self, db_session):
        """The same grace the health check uses. An order that is an exception
        on one screen and fine on another is how a team learns to trust
        neither."""
        hub = await _hub(db_session)
        await _order(db_session, hub, promised_minutes_ago=1)
        assert (await _queue(db_session, hub)).items == []

    async def test_another_hub_is_not_included(self, db_session):
        hub = await _hub(db_session)
        other = await _hub(db_session)
        await _order(db_session, other, promised_minutes_ago=200)
        assert (await _queue(db_session, hub)).items == []

    async def test_an_empty_queue_is_a_real_answer(self, db_session):
        hub = await _hub(db_session)
        queue = await _queue(db_session, hub)
        assert queue.items == []
        assert queue.by_kind() == {k: 0 for k in queue.by_kind()}
        assert queue.worst_wait_minutes == 0.0


class TestTheOrdering:
    async def test_the_longest_wait_comes_first(self, db_session):
        hub = await _hub(db_session)
        await _order(db_session, hub, promised_minutes_ago=60)
        await _order(db_session, hub, promised_minutes_ago=400)
        await _order(db_session, hub, promised_minutes_ago=200)
        waits = [i.minutes_waiting for i in (await _queue(db_session, hub)).items]
        assert waits == sorted(waits, reverse=True)

    async def test_an_order_appears_once_under_its_loudest_kind(self, db_session):
        """A failed delivery that was also flagged is one thing to do, not two.
        A queue that double-counts is a queue somebody stops trusting."""
        hub = await _hub(db_session)
        order = await _order(
            db_session, hub, status=OrderStatus.delivery_failed,
            promised_minutes_ago=300,
        )
        await _flag(db_session, hub, order)
        queue = await _queue(db_session, hub)
        assert len(queue.items) == 1
        assert queue.items[0].kind == KIND_FLAGGED

    async def test_minutes_is_named_for_what_it_is(self, db_session):
        """Not a score. The thing that would rank these properly is M2, which
        needs hundreds of observed consequences that do not exist - and a score
        would look like the model it stands in for."""
        hub = await _hub(db_session)
        await _order(db_session, hub, promised_minutes_ago=120)
        item = (await _queue(db_session, hub)).items[0]
        assert item.minutes_waiting == pytest.approx(120, abs=1)
        assert not hasattr(item, "urgency")
        assert not hasattr(item, "score")


class TestItTellsYouWhatToDo:
    async def test_every_kind_carries_a_next_action(self, db_session):
        hub = await _hub(db_session)
        flagged = await _order(db_session, hub)
        await _flag(db_session, hub, flagged)
        await _order(db_session, hub, status=OrderStatus.delivery_failed)
        await _order(db_session, hub, promised_minutes_ago=200)
        await _order(db_session, hub, status=OrderStatus.queued)

        queue = await _queue(db_session, hub)
        assert len(queue.items) == 4
        for item in queue.items:
            assert item.next_action
            assert item.external_ref

    async def test_the_quiet_one_says_nobody_has_it(self, db_session):
        hub = await _hub(db_session)
        await _order(db_session, hub, status=OrderStatus.queued)
        item = (await _queue(db_session, hub)).items[0]
        assert "nobody has this order" in item.next_action

    async def test_the_counts_make_an_empty_queue_legible(self, db_session):
        hub = await _hub(db_session)
        await _order(db_session, hub, promised_minutes_ago=200)
        counts = (await _queue(db_session, hub)).by_kind()
        assert counts[KIND_PAST_PROMISE] == 1
        assert counts[KIND_FLAGGED] == 0


class TestWhoCanRead:
    """The endpoint was admin-gated when CON-4 was written, copied from the
    scorecard beside it. Building the dashboard panel showed that to be wrong.
    """

    async def test_a_viewer_can_read_the_queue(self, db_session):
        """`require_admin`'s own docstring says it is "for the specific mutating
        endpoints a viewer shouldn't reach". This is a read, and a dispatcher on
        a viewer account who cannot see their own exceptions cannot run a day -
        which is CON-1's whole bar."""
        from app.api.routes import operations_exceptions
        from app.models.ops_user import VIEWER_ROLE
        from app.ops_auth.dependencies import AuthedOpsUser

        hub = await _hub(db_session)
        # Relative to the real clock, not this file's fixed NOW. The endpoint
        # takes no `now` - correctly, it is a live view - so a fixture pinned to
        # a calendar time is a test that passes at some hours and fails at
        # others. The same hazard broke TestTheWholeChain overnight.
        real_now = datetime.now(timezone.utc)
        await _order(
            db_session, hub,
            promised_minutes_ago=0, requested_minutes_ago=0,
        )
        order = (await db_session.scalars(select(Order))).all()[-1]
        order.promised_at = real_now - timedelta(minutes=200)
        order.requested_at = real_now - timedelta(minutes=260)
        await db_session.flush()

        viewer = AuthedOpsUser(
            ops_user_id="u1", email="v@example.com", name="Viewer", role=VIEWER_ROLE
        )
        view = await operations_exceptions(
            hub_id=hub.id, session=db_session, _ops=viewer
        )
        assert len(view.items) == 1
        assert view.items[0].next_action

    async def test_it_still_needs_an_ops_session(self):
        """Open to any ops user is not open to anyone. It names customers."""
        import inspect

        from app.api.routes import operations_exceptions
        from app.ops_auth.dependencies import get_current_ops_user

        dependency = inspect.signature(operations_exceptions).parameters["_ops"].default
        assert dependency.dependency is get_current_ops_user
