"""
Prometheus metrics (docs/ROADMAP.md S4, app/metrics.py). Pure unit tests -
the metric registry and the /metrics endpoint need no DB or Redis.
"""
from app import metrics
from app.api.routes import prometheus_metrics
from app.ops_auth.middleware import EXEMPT_PATHS


def test_render_latest_emits_defined_metrics_in_prometheus_text():
    # Touch each metric so it appears in the exposition output.
    metrics.ORDERS_INGESTED.labels(hub_id="h1", source_system="flat_file").inc()
    metrics.HOLD_QUEUE_DEPTH.labels(hub_id="h1").set(3)
    metrics.OPTIMIZER_CYCLE_SECONDS.labels(hub_id="h1", engine="stub").observe(0.4)
    metrics.OPTIMIZER_CYCLES_OVER_BUDGET.labels(hub_id="h1").inc()
    metrics.RATE_LIMIT_REJECTIONS.inc()

    payload, content_type = metrics.render_latest()
    text = payload.decode()
    assert "text/plain" in content_type
    for name in (
        "lmx_orders_ingested_total",
        "lmx_hold_queue_depth",
        "lmx_optimizer_cycle_seconds",
        "lmx_optimizer_cycles_over_budget_total",
        "lmx_rate_limit_rejections_total",
    ):
        assert name in text, f"{name} missing from /metrics output"


async def test_metrics_endpoint_returns_a_prometheus_response():
    resp = await prometheus_metrics()
    assert resp.status_code == 200
    assert "text/plain" in resp.media_type
    assert resp.body.startswith(b"# ")  # prometheus exposition begins with HELP/TYPE comments


def test_metrics_path_is_exempt_from_ops_auth():
    # A scraper can't present a per-user JWT, so /metrics must be exempt
    # from the ops-auth gate (app/ops_auth/middleware.py).
    assert "/metrics" in EXEMPT_PATHS
