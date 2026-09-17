"""A solver outage produces a usable plan, not an outage (`DEC-5`).

`get_route_optimization_client` picks one client at startup from configuration
and caches it for the process. Nothing catches a failure from it, so the moment
`DEC-3` points the optimizer at a live Google project, a Route Optimization
outage stops dispatch: `plan_cycle` raises, `run_cycle` raises, and orders sit in
the hold queue until somebody notices.

The planner to fall back to already exists. `StubRouteOptimizationClient` is a
working nearest-neighbour router - worse plans, real plans - and it has been
serving every cycle in this repository to date, so its output is the best
understood in the system. What was missing was the wiring between them, and the
part that matters more than the wiring: **saying so.**

## A degraded plan must never look like a good one

`REC-1` records the engine that produced every decision, and a fallback recorded
as `stub_nearest_neighbor` would be indistinguishable from a deployment that was
simply configured without Google. Those are completely different facts - one is
a choice, the other is an incident - and a month later nobody could tell which
plans to trust. The fallback reports its own engine name, so a decision made
during an outage says so for as long as the row exists.

## Why the breaker, and why it is not an optimisation

`GoogleRouteOptimizationClient` retries with exponential backoff, which is right
for a blip and wrong for an outage: while Google is down, every cycle pays the
full retry budget before failing over. `DEC-3` is measured on "p95 solve under
5s", and the retries alone would blow it on every cycle for the duration. After
`FAILURE_THRESHOLD` consecutive failures the primary is skipped outright for
`COOLDOWN_SECONDS`, so a sustained outage costs the retry budget once rather
than once per cycle.

## It falls back on a malformed request too, and that is a real trade

A 400 from a bad request is our bug, not Google's, and falling back hides it
behind working deliveries. The alternative is refusing to dispatch because our
own request builder is broken, which is worse for the customer and no faster to
diagnose. So it falls back and makes the bug loud a different way: the failure
class is logged, the counter is labelled by it, and a persistent
`invalid_request` fallback reads as a defect rather than weather.

## If the fallback fails too

It raises. There is nothing left to try, and an outage that is genuinely an
outage should look like one rather than like a quiet no-op.
"""
from __future__ import annotations

import contextvars
import time

import structlog

from app.metrics import OPTIMIZER_FALLBACKS
from app.optimizer.google_routes_client import (
    DriverCandidate,
    RouteAssignment,
    RouteOptimizationClient,
    StopCandidate,
)

logger = structlog.get_logger(__name__)

# Consecutive failures before the primary is skipped outright.  Three rather
# than one: a single timeout is weather, and opening the breaker on it would
# hand a whole cooldown of cycles to the worse planner for no reason.
FAILURE_THRESHOLD = 3

# How long to stop trying. Long enough that a real outage is not re-probed every
# cycle, short enough that recovery is picked up within one delivery window.
COOLDOWN_SECONDS = 300.0

FALLBACK_SUFFIX = "_fallback"

# Which engine served the call *in this task*. A plain attribute would be wrong:
# several hubs can plan concurrently in one process, and `app/optimizer/
# service.py` reads `engine_name` after `optimize` returns - so two cycles
# racing would record each other's engine. A ContextVar is per-task, which is
# exactly the scope of one cycle.
_engine_used: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "optimizer_engine_used", default=None
)


class FallbackRouteOptimizationClient(RouteOptimizationClient):
    """Tries the primary, falls back to a simpler planner, records which ran."""

    def __init__(
        self,
        primary: RouteOptimizationClient,
        fallback: RouteOptimizationClient,
        *,
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._breaker_opened_at: float | None = None

    @property
    def fallback_engine_name(self) -> str:
        return f"{self._fallback.engine_name}{FALLBACK_SUFFIX}"

    @property
    def engine_name(self) -> str:
        """What served this cycle's plan.

        Two sources, because one is not enough and the failure directions
        differ.

        **While the breaker is open**, every plan for the next cooldown comes
        from the fallback, and that is a property of the process rather than of
        one call - so it is reported whoever asks and from whatever task. This
        is the case that matters: a sustained outage must never read as healthy.

        **Otherwise the ContextVar**, which is per-task and therefore per-cycle.
        `app/optimizer/service.py` awaits `optimize` and reads this a few lines
        later in the same coroutine, so it sees its own call's answer and not a
        concurrent hub's.

        The honest limitation: a caller that wrapped `optimize` in
        `create_task` and read this outside would get the primary's name for an
        isolated failure, because a task copies its context rather than sharing
        it. That direction is wrong - it reads as healthy - which is precisely
        why the breaker is checked first and covers every case that lasts longer
        than one cycle.
        """
        if self.breaker_is_open:
            return self.fallback_engine_name
        return _engine_used.get() or self._primary.engine_name

    @property
    def breaker_is_open(self) -> bool:
        if self._breaker_opened_at is None:
            return False
        if time.monotonic() - self._breaker_opened_at >= self._cooldown_seconds:
            # Cooldown elapsed: let the next call try the primary again. Reset
            # here rather than on a timer so recovery costs nothing while idle.
            self._breaker_opened_at = None
            self._consecutive_failures = 0
            logger.info("optimizer_breaker_closed", engine=self._primary.engine_name)
            return False
        return True

    async def optimize(
        self, drivers: list[DriverCandidate], stops: list[StopCandidate]
    ) -> tuple[list[RouteAssignment], list[str]]:
        # This call's answer, not the last one's. A single task can plan several
        # hubs in a row - the scheduler's poll loop does - and without the reset
        # a hub that never planned would inherit the previous hub's engine.
        _engine_used.set(None)

        if self.breaker_is_open:
            return await self._run_fallback(drivers, stops, reason="breaker_open")

        try:
            result = await self._primary.optimize(drivers, stops)
        except Exception as failure:
            self._record_failure(failure)
            return await self._run_fallback(
                drivers, stops, reason=_failure_class(failure)
            )

        if self._consecutive_failures:
            logger.info(
                "optimizer_primary_recovered",
                engine=self._primary.engine_name,
                after_failures=self._consecutive_failures,
            )
        self._consecutive_failures = 0
        _engine_used.set(self._primary.engine_name)
        return result

    def _record_failure(self, failure: Exception) -> None:
        self._consecutive_failures += 1
        logger.warning(
            "optimizer_primary_failed",
            engine=self._primary.engine_name,
            failure_class=_failure_class(failure),
            error=str(failure)[:200],
            consecutive_failures=self._consecutive_failures,
        )
        if (
            self._consecutive_failures >= self._failure_threshold
            and self._breaker_opened_at is None
        ):
            self._breaker_opened_at = time.monotonic()
            logger.error(
                "optimizer_breaker_opened",
                engine=self._primary.engine_name,
                cooldown_seconds=self._cooldown_seconds,
                detail=(
                    "every plan until the cooldown elapses comes from the "
                    "fallback planner and is recorded as such"
                ),
            )

    async def _run_fallback(
        self,
        drivers: list[DriverCandidate],
        stops: list[StopCandidate],
        *,
        reason: str,
    ) -> tuple[list[RouteAssignment], list[str]]:
        OPTIMIZER_FALLBACKS.labels(reason=reason).inc()
        _engine_used.set(self.fallback_engine_name)
        # No try/except. If the fallback fails there is nothing left to try, and
        # an outage that is genuinely an outage should look like one.
        return await self._fallback.optimize(drivers, stops)


def _failure_class(failure: Exception) -> str:
    """A coarse label, because it goes on a metric.

    Coarse on purpose: a label with unbounded values - an error string, a
    request id - turns one time series into thousands and takes the dashboard
    with it.
    """
    name = type(failure).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "invalid" in name or "argument" in name:
        return "invalid_request"
    if "auth" in name or "credential" in name:
        return "auth"
    return "other"
