"""
The alerting check (app/health/checks.py, docs/ROADMAP.md S4).

**Most of these tests are about the check NOT firing.** That is deliberate. A
condition that pages when nothing is wrong gets muted, and a muted check is worse
than no check at all - it also removes the nagging worry that would otherwise have
made someone go and look. So the false-positive cases below (a quiet evening, a
closed hub, normal batching, a late order a driver already holds) carry more
weight here than the true-positive ones.

The two that would have bitten in production, both proven here:

  - a hub CLOSED for the day writes no cycle snapshot at all, because
    `run_cycle` returns early. Without the calendar check this check pages every
    Sunday morning.
  - dispatch cycles can run perfectly and assign nothing - no driver on shift,
    every driver full, an unroutable stop. The heartbeat stays fresh while the
    promise goes past, so cycle liveness alone reports healthy through the
    failure that actually costs a client.
"""
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.api.internal_routes import dispatch_health
from app.batch_queue.queue import HeldOrder
from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.health import checks
from app.health.checks import evaluate
from app.hub_calendar import hub_local_date
from app.models.client import Client
from app.models.hub import Hub
from app.models.hub_closure import HubClosure
from app.models.order import Order, OrderStatus
from app.models.shop import Shop
from app.optimizer.last_cycle_store import LastCycleStore
from app.schemas.optimizer import LastCycleSnapshot

pytestmark = pytest.mark.integration

TOKEN = "an-internal-token"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "internal_api_token", TOKEN)


async def _seed_hub(db_session, *, active: bool = True) -> uuid.UUID:
    hub_id = uuid.uuid4()
    db_session.add(
        Hub(id=hub_id, name=f"Hub {hub_id.hex[:6]}", lat=30.267, lng=-97.743, active=active)
    )
    await db_session.commit()
    return hub_id


async def _hold_one_order(hub_id: uuid.UUID) -> None:
    """Put an order in the hold queue. Only the queue matters for liveness - the
    check reads a depth, not the rows - so no Postgres row is needed here."""
    now = datetime.now(timezone.utc)
    await HoldQueueStore().add(
        str(hub_id),
        HeldOrder(
            order_id=str(uuid.uuid4()),
            shop_lat=30.26,
            shop_lng=-97.74,
            sla_tier="T2",
            hold_deadline=now + timedelta(minutes=30),
            held_since=now,
            shop_name="Midtown Auto Parts",
        ),
    )


async def _record_cycle(hub_id: uuid.UUID, *, seconds_ago: int) -> None:
    await LastCycleStore().set(
        LastCycleSnapshot(
            hub_id=str(hub_id),
            at=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
            engine="stub",
            duration_seconds=0.2,
            assigned_count=0,
            unassigned_count=0,
            over_budget=False,
        )
    )


async def _seed_order(
    db_session,
    hub_id: uuid.UUID,
    *,
    status: OrderStatus,
    promised_at: datetime | None,
) -> Order:
    client_id, shop_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Client(id=client_id, hub_id=hub_id, name="Design Partner", pos_system="flat_file"))
    await db_session.commit()
    db_session.add(
        Shop(
            id=shop_id,
            client_id=client_id,
            name="Midtown Auto Parts",
            address="220 Harbor St",
            lat=30.26,
            lng=-97.74,
            external_ref=f"SHOP-{uuid.uuid4().hex[:8]}",
        )
    )
    await db_session.commit()

    now = datetime.now(timezone.utc)
    order = Order(
        hub_id=hub_id,
        client_id=client_id,
        shop_id=shop_id,
        external_order_ref=f"ORD-{uuid.uuid4().hex[:8]}",
        source_system="flat_file",
        raw_payload={},
        sla_tier="T2",
        hold_deadline=now + timedelta(minutes=30),
        weight_units=1,
        status=status,
        requested_at=now,
        promised_at=promised_at,
        delivery_address="14 Oak Ave",
        delivery_lat=30.27,
        delivery_lng=-97.75,
    )
    db_session.add(order)
    await db_session.commit()
    return order


def _by_name(report, name):
    return next(check for check in report.checks if check.name == name)


# ---------------------------------------------------------------------------
# The healthy baseline
# ---------------------------------------------------------------------------


async def test_a_quiet_healthy_system_reports_ok(db_session, real_redis_client):
    await _seed_hub(db_session)

    report = await evaluate()

    assert report.ok, report.failing
    assert [c.name for c in report.checks] == [
        "redis",
        "database",
        "dispatch_liveness",
        "stuck_orders",
    ]


async def test_a_fresh_deployment_with_no_hubs_is_not_degraded(db_session, real_redis_client):
    """The scheduler and the uptime check will both be running before the first
    hub exists, and a brand new deployment must not page anyone."""
    report = await evaluate()
    assert report.ok, report.failing


# ---------------------------------------------------------------------------
# Dispatch liveness - the condition this was built for
# ---------------------------------------------------------------------------


async def test_orders_waiting_with_a_stale_cycle_is_degraded(db_session, real_redis_client):
    """**The failure that is otherwise invisible.** With one driver in a pilot,
    "no offers arrived" and "no orders today" look identical from outside, so a
    dead poll loop can persist for a day and surface as an angry client."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=settings.dispatch_stale_after_seconds + 60)

    report = await evaluate()

    assert report.failing == ["dispatch_liveness"]
    detail = _by_name(report, "dispatch_liveness").detail
    # The body is what whoever the alert wakes actually reads.
    assert str(hub_id) in detail
    assert "1 order(s) waiting" in detail
    assert "threshold" in detail


async def test_orders_waiting_with_a_fresh_cycle_is_fine(db_session, real_redis_client):
    """Holding orders to combine them IS the product. Alerting on a non-empty
    queue alone would fire during normal batching, every day."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=30)

    report = await evaluate()

    assert report.ok, report.failing


async def test_a_stale_cycle_with_an_empty_queue_is_fine(db_session, real_redis_client):
    """A quiet evening, not an outage. Alerting on cycle age alone would page
    every night and be muted within a week."""
    hub_id = await _seed_hub(db_session)
    await _record_cycle(hub_id, seconds_ago=settings.dispatch_stale_after_seconds * 10)

    report = await evaluate()

    assert report.ok, report.failing


async def test_orders_waiting_with_no_cycle_ever_recorded_is_degraded(
    db_session, real_redis_client
):
    """Distinct from a stale snapshot: this is what a deployment where dispatch
    never started at all looks like, and it must not read as healthy just because
    there is no timestamp to compare against."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)

    report = await evaluate()

    assert report.failing == ["dispatch_liveness"]
    assert "no dispatch cycle has ever been recorded" in _by_name(
        report, "dispatch_liveness"
    ).detail


async def test_a_closed_hub_with_a_full_queue_is_not_degraded(db_session, real_redis_client):
    """**The Sunday-morning regression.** `run_cycle` returns early for a hub that
    isn't operating today and writes NO cycle snapshot, so a correctly-behaving
    system looks stale all weekend with orders waiting for Monday. Without the
    calendar check this pages every closed day."""
    hub_id = await _seed_hub(db_session)
    # The hub's OWN local date, not UTC's. A closure is a local calendar day, so
    # a UTC date here would silently stop marking the hub closed whenever the
    # test runs near midnight - the same time-of-day trap that makes this class
    # of test pass all afternoon and fail at 7pm.
    hub = await db_session.get(Hub, hub_id)
    local_today = hub_local_date(hub, datetime.now(timezone.utc))
    db_session.add(HubClosure(hub_id=hub_id, closure_date=local_today, reason="Sunday"))
    await db_session.commit()

    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=settings.dispatch_stale_after_seconds * 5)

    report = await evaluate()

    assert report.ok, report.failing


async def test_an_inactive_hub_is_not_checked(db_session, real_redis_client):
    """A decommissioned hub's leftover queue must not page anyone forever."""
    hub_id = await _seed_hub(db_session, active=False)
    await _hold_one_order(hub_id)

    report = await evaluate()

    assert report.ok, report.failing


async def test_one_healthy_hub_does_not_mask_a_broken_one(db_session, real_redis_client):
    """Any hub in trouble degrades the check - the body names which."""
    healthy = await _seed_hub(db_session)
    broken = await _seed_hub(db_session)
    await _hold_one_order(healthy)
    await _record_cycle(healthy, seconds_ago=10)
    await _hold_one_order(broken)
    await _record_cycle(broken, seconds_ago=settings.dispatch_stale_after_seconds + 60)

    report = await evaluate()

    detail = _by_name(report, "dispatch_liveness").detail
    assert report.failing == ["dispatch_liveness"]
    assert str(broken) in detail
    assert str(healthy) not in detail


async def test_the_threshold_is_configurable(db_session, real_redis_client, monkeypatch):
    """It has to be: the safety-net scheduler's interval bounds how stale a
    healthy system can look, so this must be tunable above it."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=600)

    monkeypatch.setattr(settings, "dispatch_stale_after_seconds", 300)
    assert (await evaluate()).failing == ["dispatch_liveness"]

    monkeypatch.setattr(settings, "dispatch_stale_after_seconds", 1800)
    assert (await evaluate()).ok


# ---------------------------------------------------------------------------
# Stuck orders - the failure a fresh heartbeat hides
# ---------------------------------------------------------------------------


async def test_an_order_past_its_promise_with_no_driver_is_degraded(
    db_session, real_redis_client
):
    """**Why this is a separate condition.** Cycles here are perfectly fresh -
    dispatch is running, it just isn't achieving anything. Liveness reports
    healthy right through it."""
    hub_id = await _seed_hub(db_session)
    await _record_cycle(hub_id, seconds_ago=5)
    late = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stuck_order_after_seconds + 600
    )
    await _seed_order(db_session, hub_id, status=OrderStatus.queued, promised_at=late)

    report = await evaluate()

    assert report.failing == ["stuck_orders"]
    assert "past their promised time" in _by_name(report, "stuck_orders").detail


async def test_an_order_a_driver_already_holds_is_not_an_alert(db_session, real_redis_client):
    """Once a driver has it, lateness is a fulfillment problem for ops to chase.
    Paging an engineer for traffic is how a check gets muted."""
    hub_id = await _seed_hub(db_session)
    late = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stuck_order_after_seconds + 600
    )
    for status in (
        OrderStatus.assigned,
        OrderStatus.accepted,
        OrderStatus.en_route_pickup,
        OrderStatus.picked_up,
        OrderStatus.delivered,
        OrderStatus.cancelled,
    ):
        await _seed_order(db_session, hub_id, status=status, promised_at=late)

    report = await evaluate()

    assert report.ok, report.failing


async def test_the_grace_period_is_respected(db_session, real_redis_client):
    """An order a minute late is not an engineering problem."""
    hub_id = await _seed_hub(db_session)
    barely_late = datetime.now(timezone.utc) - timedelta(seconds=60)
    await _seed_order(db_session, hub_id, status=OrderStatus.queued, promised_at=barely_late)

    report = await evaluate()

    assert report.ok, report.failing


async def test_an_order_with_no_promise_at_all_is_not_counted(db_session, real_redis_client):
    """An EXTERNAL-SLA order may carry no promise of ours. Nothing to be late
    against, so it must not read as stuck forever."""
    hub_id = await _seed_hub(db_session)
    order = await _seed_order(
        db_session, hub_id, status=OrderStatus.queued, promised_at=None
    )
    order.hold_deadline = None
    await db_session.commit()

    report = await evaluate()

    assert report.ok, report.failing


# ---------------------------------------------------------------------------
# Robustness: the check must not become the outage
# ---------------------------------------------------------------------------


async def test_a_raising_check_is_reported_not_propagated(
    db_session, real_redis_client, monkeypatch
):
    """A 500 from this endpoint alerts without explaining, and the explanation is
    the whole value of the response body."""

    async def exploding() -> checks.CheckResult:
        raise RuntimeError("redis client is misconfigured")

    monkeypatch.setattr(checks, "check_redis", exploding)

    report = await evaluate()

    assert report.failing == ["redis"]
    assert "RuntimeError" in _by_name(report, "redis").detail


async def test_a_hanging_check_times_out_rather_than_hanging(
    db_session, real_redis_client, monkeypatch
):
    """A health endpoint that hangs is indistinguishable from a dead deployment,
    and "the alert fired because the alert was slow" destroys trust in it."""
    import asyncio

    async def never_returns() -> checks.CheckResult:
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    # Shortened, not tiny: the ceiling is global, so a value below the real
    # queries' latency would time THEM out too and prove nothing about the
    # hanging one. Membership rather than equality for the same reason - a slow
    # CI database shouldn't make this flaky.
    monkeypatch.setattr(checks, "CHECK_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(checks, "check_database", never_returns)

    report = await evaluate()

    assert "database" in report.failing
    assert "did not complete" in _by_name(report, "database").detail


async def test_every_check_is_reported_even_when_several_fail(
    db_session, real_redis_client, monkeypatch
):
    """"Redis is down" and "Redis is down AND orders are already late" are a
    restart versus a round of client phone calls. Short-circuiting would hide the
    second."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    late = datetime.now(timezone.utc) - timedelta(
        seconds=settings.stuck_order_after_seconds + 600
    )
    await _seed_order(db_session, hub_id, status=OrderStatus.queued, promised_at=late)

    report = await evaluate()

    assert set(report.failing) == {"dispatch_liveness", "stuck_orders"}
    assert len(report.checks) == 4


# ---------------------------------------------------------------------------
# The endpoint - what the uptime check actually sees
# ---------------------------------------------------------------------------


class _Response:
    """Enough of Response for the handler to set a status code on."""

    status_code = 200


async def test_a_healthy_system_answers_200(db_session, real_redis_client, configured):
    await _seed_hub(db_session)
    response = _Response()

    body = await dispatch_health(response)

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["failing"] == []


async def test_a_degraded_system_answers_503(db_session, real_redis_client, configured):
    """The status code IS the alert rule - a Cloud Monitoring uptime check plus an
    alert policy on check failure is the entire alerting stack."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=settings.dispatch_stale_after_seconds + 60)
    response = _Response()

    body = await dispatch_health(response)

    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["failing"] == ["dispatch_liveness"]
    assert body["checked_at"]


async def test_the_body_explains_every_check(db_session, real_redis_client, configured):
    """Written for whoever the alert wakes at 2am - the code fires it, the body
    says what broke and against which threshold."""
    await _seed_hub(db_session)

    body = await dispatch_health(_Response())

    assert {c["name"] for c in body["checks"]} == {
        "redis",
        "database",
        "dispatch_liveness",
        "stuck_orders",
    }
    assert all(c["detail"] for c in body["checks"])


async def test_the_endpoint_is_behind_the_internal_token(db_session, real_redis_client):
    """Hub ids, queue depths and late-order counts are operational intelligence.
    Cloud Monitoring uptime checks can send a custom header, so gating it is
    free."""
    from app.api.internal_routes import require_internal_secret
    from fastapi import HTTPException

    routes = [
        route
        for route in __import__("app.api.internal_routes", fromlist=["router"]).router.routes
        if getattr(route, "path", None) == "/internal/health/dispatch"
    ]
    assert routes, "route not registered"
    dependency_calls = [d.call for d in routes[0].dependant.dependencies]
    assert require_internal_secret in dependency_calls

    with pytest.raises(HTTPException):
        await require_internal_secret(x_lmx_internal_token="wrong")


# ---------------------------------------------------------------------------
# Over real HTTP, through the real middleware stack
# ---------------------------------------------------------------------------
#
# The handler tests above prove the handler sets an attribute. These prove the
# thing the uptime check will actually observe - which is a different claim, and
# the one the alert policy depends on.


async def _get(**headers) -> httpx.Response:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/internal/health/dispatch", headers=headers)


async def test_over_http_a_degraded_system_really_returns_503(
    db_session, real_redis_client, configured
):
    """Setting `response.status_code` in a handler that returns a dict has to
    actually reach the wire, because the alert policy is "non-200"."""
    hub_id = await _seed_hub(db_session)
    await _hold_one_order(hub_id)
    await _record_cycle(hub_id, seconds_ago=settings.dispatch_stale_after_seconds + 60)

    response = await _get(**{"x-lmx-internal-token": TOKEN})

    assert response.status_code == 503
    assert response.json()["failing"] == ["dispatch_liveness"]


async def test_over_http_a_healthy_system_really_returns_200(
    db_session, real_redis_client, configured
):
    await _seed_hub(db_session)

    response = await _get(**{"x-lmx-internal-token": TOKEN})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_over_http_an_unauthenticated_probe_gets_404(
    db_session, real_redis_client, configured
):
    assert (await _get()).status_code == 404
    assert (await _get(**{"x-lmx-internal-token": "wrong"})).status_code == 404


async def test_the_endpoint_still_explains_itself_when_redis_is_down(
    db_session, real_redis_client, configured, monkeypatch
):
    """**The scenario this endpoint most needs to survive.** The general rate
    limiter needs Redis to decide anything, so if this path were not exempt from
    it a Redis outage would raise in middleware and the response would be an
    opaque 500 - losing the one line that says which dependency died. Proven over
    real HTTP because that is the only place the middleware runs at all."""

    class _DeadRedis:
        async def ping(self):
            raise ConnectionError("Error 111 connecting to redis:6379")

        async def hlen(self, *_args, **_kwargs):
            raise ConnectionError("Error 111 connecting to redis:6379")

        async def get(self, *_args, **_kwargs):
            raise ConnectionError("Error 111 connecting to redis:6379")

    await _seed_hub(db_session)
    monkeypatch.setattr(checks, "get_client", lambda: _DeadRedis())
    monkeypatch.setattr("app.batch_queue.store.get_client", lambda: _DeadRedis())
    monkeypatch.setattr("app.optimizer.last_cycle_store.get_client", lambda: _DeadRedis())

    response = await _get(**{"x-lmx-internal-token": TOKEN})

    assert response.status_code == 503, "a Redis outage must not become an opaque 500"
    body = response.json()
    assert "redis" in body["failing"]
    redis_check = next(c for c in body["checks"] if c["name"] == "redis")
    assert "ConnectionError" in redis_check["detail"]
    # And the DB check still answers, so the body distinguishes "Redis is down"
    # from "everything is down".
    assert next(c for c in body["checks"] if c["name"] == "database")["ok"] is True


# ---------------------------------------------------------------------------
# The cheap depth read
# ---------------------------------------------------------------------------


async def test_depth_matches_the_queue_without_reading_it(db_session, real_redis_client):
    """The check runs on a timer against every hub, so a monitoring probe must not
    become the heaviest reader of this queue."""
    hub_id = await _seed_hub(db_session)
    store = HoldQueueStore()

    assert await store.depth(str(hub_id)) == 0
    for _ in range(3):
        await _hold_one_order(hub_id)
    assert await store.depth(str(hub_id)) == 3
    assert len(await store.get_all(str(hub_id))) == 3
