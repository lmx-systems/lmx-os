# Alerting

**What this covers:** the four conditions LMX OS considers worth interrupting
someone for, and how to make AWS actually interrupt someone. Code lives in
`app/health/checks.py`; the endpoint is `GET /internal/health/dispatch`.

> **Corrected August 2026.** This runbook was written against Google Cloud - Cloud
> Run, Cloud Scheduler, Cloud Monitoring - while the actual deployment target in
> `infra/` is AWS ECS Fargate (`infra/README.md`, ROADMAP `S3`). The endpoint and the
> three scheduled jobs were always cloud-agnostic; only these instructions were wrong,
> and following them would have put someone in the wrong console entirely. The AWS
> steps below have **not** been exercised against a real account - none exists yet -
> so treat the console paths as a map rather than a transcript.

---

## The shape, and why it isn't Prometheus

`app/metrics.py` exports Prometheus metrics at `GET /metrics`. Those are useful
for a dashboard and **nothing can usefully alert on them as deployed**:

1. **Prometheus counters live in process memory.** The app service autoscales
   between `app_desired_count` and 6 tasks (`infra/aws/ecs.tf`), so
   `lmx_orders_ingested_total` resets whenever a task is replaced and differs per
   task — and a scrape through the ALB reaches whichever task it picked. `rate()`
   over a counter that resets and jumps between tasks is noise.
2. **Nothing is scraping.** Amazon Managed Prometheus needs a collector alongside
   the tasks, and it would inherit problem 1.
3. **The thing we most need to know is an absence.** "Dispatch stopped" is not a
   number anywhere; it's the non-arrival of something. Expressing that in
   Prometheus needs a server with history.

So the app answers the question itself. Every input is read from **Redis or
Postgres** — state shared by every task — so the answer is the same whichever task
responds. The endpoint returns `200` when healthy and `503` when something is wrong,
and something on a timer alerts on the status code.

That is the entire alerting stack: a URL, a timer, and a notification channel.

```
CloudWatch Synthetics canary ──every 5 min──▶ GET /internal/health/dispatch
        │                                              │
        │                                       200 / 503 + JSON body
        ▼
  CloudWatch alarm ──▶ SNS topic ──▶ email / SMS / Slack
```

**Why a Synthetics canary and not a Route 53 health check.** A Route 53 health
check is the obvious reach and it cannot work here: it does not support custom
request headers, so it cannot send `X-LMX-Internal-Token`, and the internal router
**fails closed with a 404** when the token is absent. The check would report
permanently unhealthy for a reason unrelated to health — an alert that fires always
is an alert that gets muted, which is the failure mode this whole document is
written to avoid. A canary is a scripted request, so it can carry the header.

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
| `dispatch_liveness` | Is at least one app task actually running (`RunningTaskCount` on the ECS service — the `-app-unhealthy-tasks` alarm in `infra/aws/logs.tf` covers this)? Is the scheduled `/internal/dispatch/run-all` job still firing and succeeding? Hit `POST /internal/dispatch/run-all` by hand — if that clears the queue, the poll loop is the problem, not the optimizer. |
| `stuck_orders` | Any drivers on shift with a reported position? `GET /fleet/{hub}/overview` — a driver with a null position is invisible to the optimizer. Then check for unresolvable addresses (`geocode_provider_unavailable` in the logs). |
| `redis` / `database` | RDS and ElastiCache both available? Connection limit reached? Then the security groups (`infra/aws/security_groups.tf`) — the app's tasks reach both through VPC rules, so a subnet or SG change is a plausible cause of a sudden simultaneous failure of both checks. |

---

## Wiring it up in AWS

You need the ALB's DNS name (`terraform output alb_dns_name`) and the
`INTERNAL_API_TOKEN` value, which lives in Secrets Manager and is injected into the
app tasks (`infra/aws/secrets.tf`).

**All of this belongs in Terraform rather than the console.** Everything else about
this deployment is IaC, and there are currently **no scheduling or canary resources
in `infra/aws/` at all** — see the gap called out under "The three scheduled jobs"
below. The console steps here are written so someone can stand alerting up on day
one and so the intent is unambiguous when the Terraform gets written.

### 1. Confirm the endpoint answers

```bash
curl -i -H "X-LMX-Internal-Token: $INTERNAL_API_TOKEN" \
  https://<alb-dns-name>/internal/health/dispatch
```

Expect `200` and `"status": "ok"`. A `404` means `INTERNAL_API_TOKEN` isn't set on
the service (the router fails closed) or the token doesn't match. Note this goes
through the ALB on the app listener rule — the dashboard and client-portal services
sit behind their own rules and will not answer it.

### 2. Create the notification channel

**SNS → Topics → Create topic** (standard), then add a subscription with a real
address someone reads on a Saturday. Add an SMS subscription too if you want to be
woken; email alone is a next-morning alert, not a page.

> `infra/aws/logs.tf` already defines an `-app-unhealthy-tasks` alarm and its own
> comment says it is **wired to nothing** — no SNS topic existed to point it at. This
> is that topic. Set `alarm_actions` on that alarm to its ARN in the same change, or
> the alarm that catches total outage still notifies nobody.

### 3. Create the uptime canary

**CloudWatch → Application Signals → Synthetics canaries → Create canary**, using
the **API canary** blueprint.

| Field | Value |
|---|---|
| Endpoint | `https://<alb-dns-name>/internal/health/dispatch` |
| Method | GET |
| Headers | **`X-LMX-Internal-Token` → your token.** Read it from Secrets Manager in the canary script rather than pasting it — a canary's configuration is readable by anyone with CloudWatch access |
| Schedule | **every 5 minutes** |
| Timeout | 10s |
| Accepted status | **2xx only** — this is the part that makes the 503 an alert |

The canary publishes `SuccessPercent` to the `CloudWatchSynthetics` namespace.

### 4. Alarm on the canary

**CloudWatch → Alarms → Create alarm**, on that canary's `SuccessPercent`:

- Threshold: **less than 100**, statistic Average, period 5 minutes
- **Datapoints to alarm: 2 out of 2** — so a single transient probe failure doesn't page
- `treat_missing_data: breaching`, matching the existing task alarm — a canary that
  stopped running is not good news
- Action: the SNS topic from step 2
- Name it something that reads well at 2am ("LMX OS dispatch degraded")

> The 10s timeout matters. Each check is capped at 4 seconds
> (`CHECK_TIMEOUT_SECONDS`) and they run concurrently, so a healthy response is well
> under a second — but a timeout at the probe would look identical to an outage.

### 5. Prove it fires

Don't trust an untested alert. The cheapest real test: temporarily set
`DISPATCH_STALE_AFTER_SECONDS=1` on the app task definition, put one order through,
and wait. The check should go `503` and the alert should arrive. **Set it back.**

### 6. The three scheduled jobs

**This is the actual gap in `infra/aws/`, not just a console step.** Three endpoints
need to be called on a timer and nothing in the repo schedules any of them:

| Endpoint | Interval | Why it exists |
|---|---|---|
| `POST /internal/dispatch/run-all` | 5 min | Belt to the in-process poll loop's braces (see below) |
| `POST /internal/webhooks/deliver-pending` | 5 min | Owed webhooks are attempted immediately after the status change commits, but that attempt is a task in the process — a replaced task loses it. This sweep is what makes an enqueued row a guarantee rather than a hope (`docs/WEBHOOKS.md`, ROADMAP `F4`) |
| `POST /internal/retention/prune` | daily | Deletes personal data past the periods the privacy policy states (`app/legal/retention.py`, `docs/LEGAL_BRIEF.md`) |

All three are safe to over-call — a sweep with nothing due is one indexed query — and
safe to miss for a run.

The AWS shape is an **EventBridge rule with a schedule expression targeting an API
destination**, where the destination's **Connection** carries the
`X-LMX-Internal-Token` header. The Connection is the important part: it is how the
token reaches the request without being pasted into a rule definition.

**The retention sweep is a promise, not an optimisation.** These are the numbers:

| Sweep | Setting |
|---|---|
| Driver location trails | `LOCATION_PING_RETENTION_DAYS` (90) |
| Sent messages and call metadata | `COMMUNICATION_RETENTION_DAYS` (730) |
| Declined applications and their inactive logins | `DECLINED_APPLICATION_RETENTION_DAYS` (365) |

Every one is printed in the privacy policy as a fact, so **the schedule has to exist
before the policy is published** — a stated period nothing enforces is worse than no
statement.

Nothing pages if the retention sweep stops. Its response reports each category
separately rather than `ok`, so a glance tells you which sweep actually did anything.
Two fields worth noticing on the declined-applications sweep: `skipped_undated`
(rejections recorded before migration 0041, which have no date and are never guessed
at) and `skipped_with_records` (a rejected applicant holding orders or invoices — a
bug upstream, and deleting is the wrong response to a surprise).

> **One thing that got simpler moving off Cloud Run.** The original version of this
> document warned about keeping CPU always-allocated with `min-instances=1`, because a
> serverless platform suspends the in-process poll loop dispatch relies on. **ECS
> Fargate tasks do not suspend**, so that caveat is gone — and the poll loop is safe
> across replicas already, via the `events:running:{hub_id}` Redis lock in
> `app/events/bus.py` that lets only one task run a cycle per hub at a time. The
> scheduled `run-all` above is now redundancy rather than the mechanism.

### 7. Alert on the deployment itself

The canary covers total outage — if the service is down, the probe fails. What it
doesn't cover is a crash loop that never serves traffic, and the existing
`-app-unhealthy-tasks` alarm on `RunningTaskCount` is exactly that check. Point it at
the SNS topic (step 2) and it starts working.

Beyond that, a metric filter on the app's CloudWatch log group for `level=error`
gives a log-based alarm. Sentry covers application exceptions already
(`app/logging_config.py`) once a DSN is set — including the
deliberately-caught-and-logged ones, and `health_check_degraded` is logged at warning
level so a degraded check reaches Sentry even if nobody opens the URL.

---

## Settings

| Env var | Default | Notes |
|---|---|---|
| `DISPATCH_STALE_AFTER_SECONDS` | `900` (15 min) | **Must be comfortably larger than the schedule interval** calling `/internal/dispatch/run-all`. That scheduler bounds how stale a healthy system can look; setting this below the interval means the check fires between every scheduled run and gets muted within a week. 15 minutes suits a 5-minute schedule. |
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
