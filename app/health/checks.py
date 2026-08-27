"""
The conditions worth waking someone up for, evaluated server-side
(docs/ROADMAP.md S4, the alerting half of observability).

**Why this is not "scrape the Prometheus metrics".** `app/metrics.py` exists and
is correct, but nothing can usefully alert on it as deployed:

  1. Prometheus counters and gauges live in PROCESS MEMORY. The app service autoscales
     and recycles instances, so `lmx_orders_ingested_total` resets on every cold
     start and differs per instance - and a scrape of the service URL reaches
     whichever instance the load balancer picked. `rate()` over a counter that
     resets and jumps between instances is noise, not a signal.
  2. Nothing is scraping. Amazon Managed Prometheus would need a collector
     collector, and it would inherit problem 1.
  3. **The thing we most need to know is an absence, not a value.** "Dispatch
     stopped" is not a number anywhere; it is the non-arrival of something.
     Expressing that in Prometheus needs a server with history.

So instead of exporting numbers and hoping something downstream reasons about
them, the app answers the question itself. Every input below lives in Redis or
Postgres - state SHARED by all instances - so the answer is the same whichever
instance responds, which is exactly what per-process metrics cannot promise.
`app/api/internal_routes.py` turns the result into a 200 or a 503, and a Cloud
Monitoring uptime check alerts on the status code. No scraper, no sidecar, no
time-series database: the alert rule is a URL returning a bad status.

**Why these four conditions and not more.** A check that fires when nothing is
wrong gets muted, and a muted check is worse than no check because it also
removes the worry that would have made someone look. Each of these had to survive
"would I get out of bed for this, every time it fires?":

  redis / database     Everything else depends on them, and they are cheap.
  dispatch_liveness    Orders waiting with nothing releasing them. THE reason
                       this module exists - see below.
  stuck_orders         Dispatch running but achieving nothing, which liveness
                       alone reports as healthy.

**What this deliberately does NOT detect: an ingestion outage.** If orders stop
arriving entirely, the hold queue stays empty and `dispatch_liveness` says
healthy. Catching that means alerting on a traffic flatline, which needs a volume
baseline - and at pilot volume "no orders for three hours" is a normal Tuesday
afternoon, so such a check would false-alarm until someone muted it. It needs
real volume history first. Stated here rather than left implicit, because the
gap is invisible from the outside and someone will otherwise assume this covers
it.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from app.batch_queue.store import HoldQueueStore
from app.config import settings
from app.db import session_scope
from app.hub_calendar import is_hub_closed_at
from app.models.hub import Hub
from app.models.order import Order, OrderStatus
from app.optimizer.last_cycle_store import LastCycleStore
from app.redis_client import get_client

logger = structlog.get_logger(__name__)

# Per-check ceiling. A health endpoint that hangs is indistinguishable from a
# dead deployment to an uptime check, and "the alert fired because the alert
# was slow" is the fastest way to lose trust in it. A timeout counts as that
# check failing, which is honest: we could not establish that it was healthy.
CHECK_TIMEOUT_SECONDS = 4.0

# Statuses where the order is OURS to move and no driver has taken it yet. An
# order sitting here past its promise means dispatch produced nothing for it.
# Deliberately excludes `assigned` and everything after: once a driver holds it,
# lateness is a fulfillment problem for ops to chase, not a systems failure, and
# paging an engineer for traffic is how a check gets muted.
PRE_ASSIGNMENT_STATUSES = (
    OrderStatus.received,
    OrderStatus.classified,
    OrderStatus.held,
    OrderStatus.queued,
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    # Written for whoever opens this from an alert notification at 2am: it should
    # say what is wrong and against which threshold, so the next step is obvious
    # without reading this file.
    detail: str


async def _guarded(name: str, coro) -> CheckResult:
    """Run one check so that neither a hang nor a raise can take down the others.

    An exception here is reported as that check failing rather than propagating,
    because a 500 from the health endpoint alerts without explaining - and the
    explanation is the entire value of the response body.
    """
    try:
        return await asyncio.wait_for(coro, timeout=CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return CheckResult(
            name=name,
            ok=False,
            detail=f"check did not complete within {CHECK_TIMEOUT_SECONDS}s",
        )
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        logger.exception("health_check_failed", check=name)
        return CheckResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")


async def check_redis() -> CheckResult:
    await get_client().ping()
    return CheckResult(name="redis", ok=True, detail="reachable")


async def check_database() -> CheckResult:
    async with session_scope() as session:
        await session.execute(select(1))
    return CheckResult(name="database", ok=True, detail="reachable")


async def check_dispatch_liveness() -> CheckResult:
    """Orders are waiting and nothing has run a dispatch cycle recently.

    **This is the condition the module was built for.** Dispatch runs off an
    in-process poll loop (`app/events/bus.py`); if that loop stops - a suspended
    instance, a wedged task, a CPU setting changed by someone who didn't know
    what it was holding up - orders accumulate in the hold queue and absolutely
    nothing says so. With a one-driver pilot, "no offers arrived" and "no orders
    today" look identical from the outside, so this failure can persist for a
    whole day and only surface as an angry client.

    **Both halves of the conjunction are load-bearing.** A stale cycle with an
    empty queue is a quiet afternoon, not an outage, and alerting on it would
    fire most evenings. Waiting orders with a fresh cycle is normal batching -
    holding orders to combine them is the product, not a fault. Only waiting
    orders AND a stale cycle means work is queued that nothing is acting on.

    **Closed hubs are excluded, and that is not a detail.** `run_cycle` returns
    early for a hub that isn't operating today (R6) WITHOUT writing a cycle
    snapshot, so a correctly-behaving system looks stale all weekend. Checking
    the calendar is what keeps this from paging every Sunday morning - which
    would have gotten it muted inside a month.
    """
    threshold = timedelta(seconds=settings.dispatch_stale_after_seconds)
    now = datetime.now(timezone.utc)
    hold_queue = HoldQueueStore()
    cycles = LastCycleStore()

    problems: list[str] = []
    async with session_scope() as session:
        hub_ids = [
            str(hub_id)
            for hub_id in (
                await session.execute(select(Hub.id).where(Hub.active.is_(True)))
            )
            .scalars()
            .all()
        ]
        for hub_id in hub_ids:
            depth = await hold_queue.depth(hub_id)
            if depth == 0:
                continue
            if await is_hub_closed_at(session, hub_id, now):
                continue

            snapshot = await cycles.get(hub_id)
            if snapshot is None:
                problems.append(
                    f"hub {hub_id}: {depth} order(s) waiting and no dispatch cycle "
                    f"has ever been recorded"
                )
                continue
            age = now - snapshot.at
            if age > threshold:
                problems.append(
                    f"hub {hub_id}: {depth} order(s) waiting, last dispatch cycle "
                    f"{int(age.total_seconds())}s ago "
                    f"(threshold {int(threshold.total_seconds())}s)"
                )

    if problems:
        return CheckResult(name="dispatch_liveness", ok=False, detail="; ".join(problems))
    return CheckResult(
        name="dispatch_liveness",
        ok=True,
        detail=f"{len(hub_ids)} active hub(s), none with unworked orders",
    )


async def check_stuck_orders() -> CheckResult:
    """Orders past what we promised the client, still with no driver on them.

    **Why this is separate from liveness rather than folded into it.** Cycles can
    run happily and assign nothing - no driver on shift, every driver at
    capacity, a geocoding failure leaving a stop unroutable, an optimizer that
    returns everything unassigned. The heartbeat stays fresh the whole time, so
    `dispatch_liveness` reports healthy while the promise quietly goes past. That
    is the failure that costs a client rather than an afternoon, and it needs its
    own condition.

    Measured against `promised_at` - what the client was actually told - falling
    back to the internal `hold_deadline`. The grace period is deliberate: an
    order a minute past its window is ops's problem to chase, not an engineer's.
    """
    grace = timedelta(seconds=settings.stuck_order_after_seconds)
    cutoff = datetime.now(timezone.utc) - grace

    async with session_scope() as session:
        deadline = func.coalesce(Order.promised_at, Order.hold_deadline)
        result = await session.execute(
            select(func.count())
            .select_from(Order)
            .where(
                Order.status.in_(PRE_ASSIGNMENT_STATUSES),
                deadline.is_not(None),
                deadline < cutoff,
            )
        )
        stuck = result.scalar_one()

    if stuck:
        return CheckResult(
            name="stuck_orders",
            ok=False,
            detail=(
                f"{stuck} order(s) more than {int(grace.total_seconds())}s past their "
                f"promised time with no driver assigned"
            ),
        )
    return CheckResult(name="stuck_orders", ok=True, detail="none past promise")


@dataclass(frozen=True)
class HealthReport:
    checks: list[CheckResult]

    @property
    def failing(self) -> list[str]:
        return [check.name for check in self.checks if not check.ok]

    @property
    def ok(self) -> bool:
        return not self.failing


async def evaluate() -> HealthReport:
    """Run every check concurrently and report all of them.

    Concurrently because the endpoint's latency is an uptime check's timeout, and
    ALL of them regardless of earlier failures because the difference between
    "Redis is down" and "Redis is down AND orders are already late" is the
    difference between a restart and a round of client phone calls. Short-
    circuiting on the first failure would hide the second.
    """
    results = await asyncio.gather(
        _guarded("redis", check_redis()),
        _guarded("database", check_database()),
        _guarded("dispatch_liveness", check_dispatch_liveness()),
        _guarded("stuck_orders", check_stuck_orders()),
    )
    report = HealthReport(checks=list(results))
    if not report.ok:
        # Logged as a warning so it also reaches Sentry via the structlog
        # processor in app/logging_config.py - the alert says "something", this
        # says what, and it is captured even if nobody opens the URL in time.
        logger.warning(
            "health_check_degraded",
            failing=report.failing,
            detail="; ".join(c.detail for c in report.checks if not c.ok),
        )
    return report
