# Alerting

**What this covers:** the four conditions LMX OS considers worth interrupting
someone for, and how to make Google Cloud actually interrupt someone. Code lives
in `app/health/checks.py`; the endpoint is `GET /internal/health/dispatch`.

---

## The shape, and why it isn't Prometheus

`app/metrics.py` exports Prometheus metrics at `GET /metrics`. Those are useful
for a dashboard and **nothing can usefully alert on them as deployed**:

1. **Prometheus counters live in process memory.** Cloud Run autoscales and
   recycles instances, so `lmx_orders_ingested_total` resets on every cold start
   and differs per instance — and a scrape of the service URL reaches whichever
   instance the load balancer picked. `rate()` over a counter that resets and
   jumps between instances is noise.
2. **Nothing is scraping.** Managed Prometheus on Cloud Run needs a sidecar
   collector, and it would inherit problem 1.
3. **The thing we most need to know is an absence.** "Dispatch stopped" is not a
   number anywhere; it's the non-arrival of something. Expressing that in
   Prometheus needs a server with history.

So the app answers the question itself. Every input is read from **Redis or
Postgres** — state shared by every instance — so the answer is the same
whichever instance responds. The endpoint returns `200` when healthy and `503`
when something is wrong, and a Cloud Monitoring **uptime check** alerts on the
status code.

That is the entire alerting stack: a URL, a timer, and a notification channel.

```
Cloud Monitoring uptime check ──every 5 min──▶ GET /internal/health/dispatch
        │                                              │
        │                                       200 / 503 + JSON body
        ▼
   alert policy ──▶ notification channel (email / SMS / Slack)
```

---

## What it checks

| Check | Fires when | Why it's page-worthy |
|---|---|---|
| `redis` | Ping fails | Fleet state, the hold queue and every lock live here. Nothing dispatches without it. |
| `database` | A trivial query fails | Same. |
| `dispatch_liveness` | A hub has **orders waiting** AND its last dispatch cycle is older than `DISPATCH_STALE_AFTER_SECONDS` | **The reason this exists.** Dispatch runs off an in-process poll loop; if it stops, orders pile up and nothing says so. With one driver, "no offers arrived" and "no orders today" look identical from outside — so this can persist for a day and surface as an angry client. |
| `stuck_orders` | Orders are more than `STUCK_ORDER_AFTER_SECONDS` past `promised_at` with **no driver assigned** | Cycles can run perfectly and assign nothing — no driver on shift, every driver full, an unroutable stop. The heartbeat stays fresh the whole time, so liveness reports healthy through the failure that actually costs a client. |

### The conditions that deliberately do *not* fire

A check that fires when nothing is wrong gets muted, and a muted check is worse
than no check — it also removes the worry that would have made someone look. So:

- **A stale cycle with an empty queue** is a quiet evening, not an outage.
- **Orders waiting with a fresh cycle** is normal batching. Holding orders to
  combine them *is* the product.
- **A hub closed for the day** (`app/models/hub_closure.py`). `run_cycle` returns
  early for a closed hub and writes no cycle snapshot, so a correctly-behaving
  system looks stale all weekend. Without the calendar check this pages every
  Sunday morning.
- **A late order a driver already holds.** That's a fulfillment problem for ops
  to chase, not an engineering one.
- **An inactive hub's leftover queue.**

### What it does NOT detect: an ingestion outage

If orders stop arriving entirely, the hold queue stays empty and
`dispatch_liveness` reports healthy. Catching that means alerting on a traffic
flatline, which needs a volume baseline — and at pilot volume "no orders for
three hours" is a normal Tuesday afternoon, so such a check would false-alarm
until someone muted it. **Revisit once there's real volume history.** Stated
explicitly because the gap is invisible from the outside.

---

## Reading the response

The status code fires the alert; the body is written for whoever it wakes.

```json
{
  "status": "degraded",
  "checked_at": "2026-08-10T14:03:11.204Z",
  "failing": ["dispatch_liveness"],
  "checks": [
    {"name": "redis", "ok": true, "detail": "reachable"},
    {"name": "database", "ok": true, "detail": "reachable"},
    {"name": "dispatch_liveness", "ok": false,
     "detail": "hub 8f3c…: 6 order(s) waiting, last dispatch cycle 2140s ago (threshold 900s)"},
    {"name": "stuck_orders", "ok": true, "detail": "none past promise"}
  ]
}
```

Every check runs even when an earlier one fails, because "Redis is down" and
"Redis is down AND orders are already late" are a restart versus a round of
client phone calls.

**First moves by failing check:**

| Failing | Look at |
|---|---|
| `dispatch_liveness` | Is Cloud Run CPU still set to always-allocated with `min-instances=1`? Is the Cloud Scheduler job calling `/internal/dispatch/run-all` still enabled and succeeding? Hit `POST /internal/dispatch/run-all` by hand — if that clears the queue, the poll loop is the problem, not the optimizer. |
| `stuck_orders` | Any drivers on shift with a reported position? `GET /fleet/{hub}/overview` — a driver with a null position is invisible to the optimizer. Then check for unresolvable addresses (`geocode_provider_unavailable` in the logs). |
| `redis` / `database` | Instance up? Connection limit? For Cloud SQL, is Direct VPC egress still attached? |

---

## Wiring it up in the GCP console

You need the service URL and the `INTERNAL_API_TOKEN` value already set on the
Cloud Run service.

### 1. Confirm the endpoint answers

```bash
curl -i -H "X-LMX-Internal-Token: $INTERNAL_API_TOKEN" \
  https://<your-service>.run.app/internal/health/dispatch
```

Expect `200` and `"status": "ok"`. A `404` means `INTERNAL_API_TOKEN` isn't set
on the service (the router fails closed) or the token doesn't match.

### 2. Create the notification channel

**Monitoring → Alerting → Edit notification channels → Email → Add new.**
Use a real address someone reads on a Saturday. Add SMS too if you want to be
woken; email alone is a next-morning alert, not a page.

### 3. Create the uptime check

**Monitoring → Uptime checks → Create uptime check.**

| Field | Value |
|---|---|
| Protocol | HTTPS |
| Resource type | URL |
| Hostname | `<your-service>.run.app` |
| Path | `/internal/health/dispatch` |
| Check frequency | **5 minutes** |
| Regions | Global (or two regions — more regions means more probes, and each one is a separate request) |
| Request method | GET |
| Custom headers | **`X-LMX-Internal-Token` → your token. Tick "hide" so it's masked.** |
| Response timeout | 10s |
| Accepted status codes | **2xx only** — this is the part that makes the 503 an alert |

Under **Alert & notification**: name it something that reads well at 2am
("LMX OS dispatch degraded"), set the duration to **`5 minutes`** so one
transient probe failure doesn't page, and attach the channel from step 2.

> The response timeout matters. Each check is capped at 4 seconds
> (`CHECK_TIMEOUT_SECONDS`) and they run concurrently, so a healthy response is
> well under a second — but a timeout at the probe would look identical to an
> outage.

### 4. Prove it fires

Don't trust an untested alert. The cheapest real test: temporarily set
`DISPATCH_STALE_AFTER_SECONDS=1` on the service, put one order through, and wait.
The check should go `503` and the alert should arrive. **Set it back.**

### 4b. Add the webhook sweep to Cloud Scheduler

While you're in the console: `POST /internal/webhooks/deliver-pending` needs a job
alongside the dispatch one (docs/WEBHOOKS.md, ROADMAP F4). Owed webhooks are
attempted immediately after the status change commits, but that attempt is a task
in the process — a recycled instance loses it, and this sweep is what makes the
enqueued row a guarantee rather than a hope. Same header, every 5 minutes, safe to
over-call: a sweep with nothing due is one indexed query.

### 4c. Add the retention sweep to Cloud Scheduler

One more, daily rather than every five minutes: `POST /internal/retention/prune` runs
three sweeps (app/legal/retention.py, docs/LEGAL_BRIEF.md) —

| Sweep | Setting |
|---|---|
| Driver location trails | `LOCATION_PING_RETENTION_DAYS` (90) |
| Sent messages and call metadata | `COMMUNICATION_RETENTION_DAYS` (730) |
| Declined applications and their inactive logins | `DECLINED_APPLICATION_RETENTION_DAYS` (365) |

**These are promises, not optimisations.** Every one of those numbers is printed in the
privacy policy as a fact, so the schedule has to exist before the policy is published — a
stated period nothing enforces is worse than no statement. Safe to over-call and safe to
miss for a day: the commitment is a period, not a deadline measured in hours.

Nothing pages if this stops. The response reports each category separately rather than
`ok`, so a glance tells you which sweep actually did anything. Two fields worth noticing
on the declined-applications sweep: `skipped_undated` (rejections recorded before migration
0041, which have no date and are never guessed at) and `skipped_with_records` (a rejected
applicant holding orders or invoices — a bug upstream, and deleting is the wrong response
to a surprise).

### 5. Alert on the deployment itself

The uptime check above also covers total outage — if the service is down, the
probe fails. What it doesn't cover is a crash loop that never serves traffic, so
add one more:

**Cloud Run → your service → Metrics**, or a log-based alert on
`severity>=ERROR` with `resource.type="cloud_run_revision"`. Sentry covers
application exceptions already (`app/logging_config.py`) once a DSN is set —
including the deliberately-caught-and-logged ones, and `health_check_degraded` is
logged at warning level so a degraded check reaches Sentry even if nobody opens
the URL.

---

## Settings

| Env var | Default | Notes |
|---|---|---|
| `DISPATCH_STALE_AFTER_SECONDS` | `900` (15 min) | **Must be comfortably larger than the Cloud Scheduler interval** calling `/internal/dispatch/run-all`. That scheduler bounds how stale a healthy system can look; setting this below the interval means the check fires between every scheduled run and gets muted within a week. 15 minutes suits a 5-minute schedule. |
| `STUCK_ORDER_AFTER_SECONDS` | `1800` (30 min) | Grace past what the client was promised. Not zero on purpose — an order a minute late is ops's to chase, and paging for that trains people to ignore the alert that matters. |

---

## Notes for whoever changes this

- **`GET /internal/health/dispatch` is not `GET /health`.** The latter reports
  that this process can serve a request and is what a load balancer should use.
  Mixing them would mean a wedged dispatch loop pulls the instance out of
  service, which fixes nothing and takes the API down too.
- **The path is exempt from the general rate limiter** (`app/rate_limit.py`).
  That middleware needs Redis to decide anything, so with this path gated a Redis
  outage would raise in middleware and return an opaque 500 — losing the one line
  that says which dependency died. A 429 would be worse: monitoring would report
  the system unhealthy because monitoring polled it. The cost is low, since an
  unauthenticated flood is rejected with a 404 and zero I/O.
- **Adding a check is a commitment.** The bar is "would I get out of bed for
  this, every time it fires?" If the honest answer is no, it belongs on a
  dashboard, not here.
