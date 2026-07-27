"""
Prometheus metrics (docs/ROADMAP.md S4, the metrics half of observability).

One module owns every metric definition so instrumentation call sites
stay one-liners and the full inventory is readable in one place. Exposed
at GET /metrics (app/api/routes.py) in Prometheus text format.

Auth note: unlike the rest of the ops surface (a per-account Bearer JWT
via app/ops_auth/), /metrics is exempt from that gate - a Prometheus
scraper can't present a per-user login. Cycle durations and queue depths
ARE operational intelligence, so in production /metrics must be reached
only over a private network / scrape target, not exposed publicly. That's
a deployment-network control (same class as infra/'s security-group
rules), not an app-auth one.

What's instrumented (chosen for "would page-worthy problems show up
here?", not completeness):
- optimizer cycle duration + over-budget count - THE performance story
  (design target <5s; see docs/ROADMAP.md T1's load test)
- hold-queue depth per hub - a growing queue is the earliest visible
  symptom of dispatch trouble
- orders ingested - traffic baseline; a flatline during business hours
  is an integration outage
- rate-limit rejections - sustained 429s are either an attack or a
  misconfigured client, both worth seeing

Error tracking (Sentry) is the other half of S4 and is wired separately
(app/logging_config.py); it stays gated on a real DSN.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

OPTIMIZER_CYCLE_SECONDS = Histogram(
    "lmx_optimizer_cycle_seconds",
    "Wall-clock duration of one dispatch optimizer cycle",
    labelnames=("hub_id", "engine"),
    # Budget is 5s; buckets bracket it tightly where it matters.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.5, 5.0, 7.5, 10.0),
)

OPTIMIZER_CYCLES_OVER_BUDGET = Counter(
    "lmx_optimizer_cycles_over_budget_total",
    "Optimizer cycles that blew the configured time budget",
    labelnames=("hub_id",),
)

HOLD_QUEUE_DEPTH = Gauge(
    "lmx_hold_queue_depth",
    "Orders sitting in the batch-hold queue, sampled at each optimizer cycle",
    labelnames=("hub_id",),
)

ORDERS_INGESTED = Counter(
    "lmx_orders_ingested_total",
    "Orders accepted by the ingestion layer",
    labelnames=("hub_id", "source_system"),
)

RATE_LIMIT_REJECTIONS = Counter(
    "lmx_rate_limit_rejections_total",
    "Requests rejected with 429 by the general API rate limiter",
)


def render_latest() -> tuple[bytes, str]:
    """(payload, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
