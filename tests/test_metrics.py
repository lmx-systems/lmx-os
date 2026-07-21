"""
Prometheus metrics (roadmap item S4): the render endpoint payload and
that instrumented call sites actually move the counters.
"""
from prometheus_client import REGISTRY

from app.metrics import (
    OPTIMIZER_CYCLE_SECONDS,
    ORDERS_INGESTED,
    RATE_LIMIT_REJECTIONS,
    render_latest,
)


def _sample(name: str, labels: dict | None = None) -> float | None:
    return REGISTRY.get_sample_value(name, labels or {})


def test_render_latest_produces_prometheus_text_format():
    ORDERS_INGESTED.labels(hub_id="hub-m1", source_system="flat_file").inc()
    payload, content_type = render_latest()
    assert b"lmx_orders_ingested_total" in payload
    assert b"lmx_optimizer_cycle_seconds" in payload
    assert "text/plain" in content_type


def test_counters_and_histogram_move():
    before = _sample("lmx_orders_ingested_total", {"hub_id": "hub-m2", "source_system": "epicor"}) or 0.0
    ORDERS_INGESTED.labels(hub_id="hub-m2", source_system="epicor").inc()
    after = _sample("lmx_orders_ingested_total", {"hub_id": "hub-m2", "source_system": "epicor"})
    assert after == before + 1

    OPTIMIZER_CYCLE_SECONDS.labels(hub_id="hub-m2", engine="stub").observe(0.42)
    count = _sample("lmx_optimizer_cycle_seconds_count", {"hub_id": "hub-m2", "engine": "stub"})
    assert count == 1

    rl_before = _sample("lmx_rate_limit_rejections_total") or 0.0
    RATE_LIMIT_REJECTIONS.inc()
    assert _sample("lmx_rate_limit_rejections_total") == rl_before + 1
