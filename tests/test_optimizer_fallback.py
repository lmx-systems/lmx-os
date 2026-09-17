"""DEC-5: a solver outage produces a usable plan, not an outage.

The client is built once and cached for the process, so before this existed a
Route Optimization outage stopped dispatch until somebody redeployed with the
project id removed. These are pure-function tests - no database, no network -
because the behaviour under test is entirely about what happens when a call
raises.
"""
import asyncio

import pytest

from app.optimizer.fallback import (
    FALLBACK_SUFFIX,
    FallbackRouteOptimizationClient,
    _failure_class,
)
from app.optimizer.google_routes_client import RouteOptimizationClient


class _Recorder(RouteOptimizationClient):
    """A planner that answers, or raises whatever it was told to."""

    def __init__(self, name: str, *, raises: Exception | None = None):
        self.engine_name = name
        self._raises = raises
        self.calls = 0

    async def optimize(self, drivers, stops):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return ([f"plan-from-{self.engine_name}"], [])


class _Timeout(Exception):
    pass


class _InvalidArgumentError(Exception):
    pass


def _client(primary_raises=None, **kwargs) -> FallbackRouteOptimizationClient:
    return FallbackRouteOptimizationClient(
        primary=_Recorder("google_route_optimization", raises=primary_raises),
        fallback=_Recorder("stub_nearest_neighbor"),
        **kwargs,
    )


def _plan(client):
    return asyncio.run(client.optimize([], []))


class TestTheHappyPath:
    def test_the_primary_serves_and_is_recorded(self):
        client = _client()
        assignments, _ = _plan(client)
        assert assignments == ["plan-from-google_route_optimization"]
        assert client.engine_name == "google_route_optimization"
        assert client._fallback.calls == 0

    def test_the_engine_before_any_call_is_the_primary(self):
        """A cycle can record the engine without planning - a closed hub, an
        empty queue - and should describe the client it would have used."""
        assert _client().engine_name == "google_route_optimization"


class TestWhenThePrimaryFails:
    def test_a_plan_still_comes_back(self):
        assignments, _ = _plan(_client(_Timeout("gateway timeout")))
        assert assignments == ["plan-from-stub_nearest_neighbor"]

    def test_the_degraded_plan_is_recorded_as_degraded(self):
        """A fallback recorded as plain `stub_nearest_neighbor` would be
        indistinguishable from a deployment configured without Google. One is a
        choice and the other is an incident, and REC-1 keeps the row for months.

        Read inside the task that planned, which is how `service.py` reads it -
        it awaits `optimize` and reads `engine_name` a few lines later in the
        same coroutine. A task copies its context rather than sharing it, so
        reading from outside would see the default.
        """
        client = _client(_Timeout("gateway timeout"))

        async def plan_and_read() -> str:
            await client.optimize([], [])
            return client.engine_name

        assert asyncio.run(plan_and_read()) == "stub_nearest_neighbor" + FALLBACK_SUFFIX
        assert client._fallback.engine_name == "stub_nearest_neighbor"

    def test_a_sustained_outage_reads_as_degraded_from_anywhere(self):
        """The case that matters, and the one the ContextVar alone would get
        wrong. While the breaker is open every plan is degraded - a fact about
        the process, not about one call - so it is reported to whoever asks."""
        client = _client(_Timeout("down"), failure_threshold=1)
        _plan(client)
        assert client.breaker_is_open is True
        assert client.engine_name == "stub_nearest_neighbor" + FALLBACK_SUFFIX

    def test_a_hub_that_never_planned_does_not_inherit_the_last_one(self):
        """The scheduler's poll loop plans several hubs in one task. Without a
        reset per call, a hub whose cycle fell back would leave its engine
        behind for the next hub to record."""
        primary = _Recorder("google_route_optimization", raises=_Timeout("blip"))
        client = FallbackRouteOptimizationClient(
            primary=primary, fallback=_Recorder("stub_nearest_neighbor"),
            failure_threshold=99,
        )

        async def two_hubs() -> tuple[str, str]:
            await client.optimize([], [])
            first = client.engine_name
            primary._raises = None
            await client.optimize([], [])
            return first, client.engine_name

        degraded, healthy = asyncio.run(two_hubs())
        assert degraded == "stub_nearest_neighbor" + FALLBACK_SUFFIX
        assert healthy == "google_route_optimization"

    def test_a_malformed_request_also_falls_back(self):
        """Our bug, not Google's - and refusing to dispatch because our own
        request builder is broken is worse for the customer and no faster to
        diagnose. It is made loud by the failure class instead."""
        client = _client(_InvalidArgumentError("bad stop"))
        assignments, _ = _plan(client)
        assert assignments == ["plan-from-stub_nearest_neighbor"]

    def test_a_failing_fallback_raises_rather_than_going_quiet(self):
        """Nothing left to try. An outage that is genuinely an outage should
        look like one."""
        client = FallbackRouteOptimizationClient(
            primary=_Recorder("google_route_optimization", raises=_Timeout("down")),
            fallback=_Recorder("stub_nearest_neighbor", raises=RuntimeError("also down")),
        )
        with pytest.raises(RuntimeError, match="also down"):
            _plan(client)


class TestTheBreaker:
    def test_one_failure_does_not_open_it(self):
        """A single timeout is weather. Opening on it would hand a whole
        cooldown of cycles to the worse planner for no reason."""
        client = _client(_Timeout("blip"), failure_threshold=3)
        _plan(client)
        assert client.breaker_is_open is False

    def test_it_opens_after_the_threshold(self):
        client = _client(_Timeout("down"), failure_threshold=3)
        for _ in range(3):
            _plan(client)
        assert client.breaker_is_open is True

    def test_an_open_breaker_stops_paying_the_retry_budget(self):
        """The reason it exists. The primary retries with backoff, so while
        Google is down every cycle burns the full budget before failing over -
        and DEC-3 is measured on p95 solve under 5s."""
        client = _client(_Timeout("down"), failure_threshold=2)
        for _ in range(2):
            _plan(client)
        calls_before = client._primary.calls
        _plan(client)
        _plan(client)
        assert client._primary.calls == calls_before, "primary was called while open"
        assert client._fallback.calls == 4

    def test_it_closes_after_the_cooldown(self):
        client = _client(_Timeout("down"), failure_threshold=1, cooldown_seconds=0.0)
        _plan(client)
        assert client.breaker_is_open is False

    def test_a_recovery_resets_the_count(self):
        primary = _Recorder("google_route_optimization", raises=_Timeout("blip"))
        client = FallbackRouteOptimizationClient(
            primary=primary, fallback=_Recorder("stub_nearest_neighbor"),
            failure_threshold=3,
        )
        _plan(client)
        _plan(client)
        primary._raises = None
        _plan(client)
        assert client._consecutive_failures == 0
        assert client.engine_name == "google_route_optimization"
        # Two more failures must not tip it over, because the count reset.
        primary._raises = _Timeout("again")
        _plan(client)
        _plan(client)
        assert client.breaker_is_open is False


class TestTheEngineIsPerCycleNotPerClient:
    def test_two_concurrent_cycles_do_not_record_each_others_engine(self):
        """Several hubs can plan at once in one process, and the service reads
        `engine_name` after `optimize` returns. A plain attribute would have one
        cycle recording the other's engine - which is why this is a ContextVar."""
        healthy = _Recorder("google_route_optimization")
        broken = _Recorder("google_route_optimization", raises=_Timeout("down"))

        async def run() -> tuple[str, str]:
            good = FallbackRouteOptimizationClient(
                primary=healthy, fallback=_Recorder("stub_nearest_neighbor")
            )
            bad = FallbackRouteOptimizationClient(
                primary=broken, fallback=_Recorder("stub_nearest_neighbor")
            )

            async def plan(client):
                await client.optimize([], [])
                return client.engine_name

            return await asyncio.gather(plan(good), plan(bad))

        good_engine, bad_engine = asyncio.run(run())
        assert good_engine == "google_route_optimization"
        assert bad_engine == "stub_nearest_neighbor" + FALLBACK_SUFFIX


class TestTheFailureLabel:
    @pytest.mark.parametrize(
        "failure,expected",
        [
            (_Timeout("x"), "timeout"),
            (_InvalidArgumentError("x"), "invalid_request"),
            (RuntimeError("x"), "other"),
        ],
    )
    def test_it_is_coarse_because_it_goes_on_a_metric(self, failure, expected):
        """A label with unbounded values - an error string, a request id - turns
        one time series into thousands and takes the dashboard with it."""
        assert _failure_class(failure) == expected
