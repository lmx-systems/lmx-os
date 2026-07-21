# LMX OS — Full-System Roadmap

Two things in one document: (1) every open item across the whole system —
not just the driver app — in one place, and (2) a phased plan to take LMX
OS from "code that works in a demo" to "running Hub 1 for real."

This supersedes the "Recommended next steps" list at the bottom of
`docs/ARCHITECTURE.md` (still there for historical context) and sits above
`docs/NEXT_STEPS.md`'s row-by-row punch list — that file is the detailed
backlog; this one is the map of how those rows fit into getting to launch.

## Part 1 — Every open item, in one place

Nothing below is new work discovered today — all of it was already called
out somewhere in `docs/ARCHITECTURE.md`, `docs/NEXT_STEPS.md`, or
`driver-app/README.md` as this got built. This just pulls it into one
list instead of leaving it scattered across three documents.

### Business / org (not code, but gates what the code is for)

| # | Item | Why it matters |
|---|---|---|
| B1 | Hire the senior backend engineer | Peer review names this the critical path — "do not hire down." Nothing below scales past one person without this. |
| B2 | Sign the first client contract | Unlocks real Epicor payload verification and the only real test of the 2.5 DPH assumption. Doesn't block most engineering work below, which can proceed on demo data. |
| B3 | Get access to the Source of Truth Index (Google Drive, LMX OS Brief v1.0–v1.2) | The batch-hold "4-question decision logic" and SLA hold-window minutes were reconstructed from a peer-review summary, not this canonical doc, because it wasn't reachable while building. |
| B4 | ~~Choose a payroll provider~~ → **Decided: Rippling** (cofounder alignment, July 2026) | Drivers are W2 employees; the earnings screen is placeholder-only until Rippling is provisioned (account + API access) and a pay formula is agreed — see A9. |
| B5 | Provision a real Twilio account + phone number | Every SMS today (OTP codes, masked customer/support messaging) runs through a stub that logs instead of sending. |

### Core backend — unverified or placeholder logic

| # | Item | Why it matters |
|---|---|---|
| E1 | Verify the Google Route Optimization client against a live Google Cloud project | Real client is built (`app/optimizer/google_routes_client.py`) but has never made one real `optimizeTours` call — the request/response mapping is unverified. |
| E2 | Tune `SLA_TIER_SKIP_PENALTY` values | Currently a placeholder ordering (T1 > T2 > T3), not calculated against real route economics. |
| E3 | Confirm real Epicor payload field names | `OrderNum`/`ShipToNum`/etc. are a guess (`app/ingestion/adapters/epicor.py`), not checked against a real tenant. Peer review calls this the most common cause of Phase 1 slippage. |
| E4 | Verify the batch-hold "4-question decision logic" against the Source of Truth Index | `app/batch_queue/queue.py`'s SLA → cluster-mate → driver-availability → hold-cap sequence is a reasoned interpretation, not a confirmed spec (gated on B3). |
| E5 | Recalibrate SLA hold-window minutes (T1=10min, T2=45min, T3=120min) | Placeholder values — the first thing to retune once real Hub 1 data exists. Per-shop overrides already exist (`active_rules`), so this is a data change, not a code change, once the right numbers are known. |
| E6 | Confirm the Learning Loop's flag-type naming convention with a driver-app stakeholder | `HOLD_TOO_SHORT_FLAG`/`HOLD_TOO_LONG_FLAG` (`app/learning_loop/detection.py`) is a proposed contract, not one anyone outside this build has signed off on. |
| ~~E7~~ | ~~Wire a real scheduler for the Learning Loop's nightly job~~ | **Done** — `app/learning_loop/scheduler.py`: in-app asyncio scheduler started from the lifespan, runs each active hub at 2am *hub-local* time (`Hub.timezone`), idempotent via a per-hub-per-night Redis SET NX marker (safe across restarts and multiple instances). Manual endpoint unchanged. |
| ~~E8~~ | ~~Move the event bus off in-process~~ | **Done** — `app/events/redis_bus.py`, selected via `EVENT_BUS_BACKEND=redis`: Redis pub/sub transport with per-event dedupe (SET NX) plus a cluster-wide per-hub run-lock + pending marker, preserving the in-process bus's debounce semantics across instances. In-process remains the default for single-instance/dev. |
| E9 | Validate the 2.5 deliveries-per-hour (DPH) figure | Called out by the peer review as a model assumption, not an established fact — only provable with real driver/order data at Hub 1 (gated on B2). |
| E10 | Tune HOT_SHOT's skip-penalty/hold-window placeholders | Phase 8 added `HOT_SHOT` ahead of T1 in `SLA_TIER_SKIP_PENALTY` and a 2-minute hold window (`app/sla/engine.py`, `app/optimizer/google_routes_client.py`) — same "reasonable guess, not calibrated" status as E2/E5, now for a fourth, premium-priced tier. |

### Security & production readiness

| # | Item | Why it matters |
|---|---|---|
| ~~S1~~ | ~~Real per-user authentication for the ops dashboard~~ | **Done** — `ops_users` table (migration `0009`, admin/operator roles, soft-deactivation, optional hub scoping), `app/ops_auth/` (third JWT surface, distinct secret — the distinctness check is now three-way pairwise), `POST /ops/auth/login` (rate-limited, same limiter as client login) + `GET /ops/me` + `POST /admin/ops-users`. Middleware accepts an ops Bearer token as the primary credential (role-gates `/admin/*` to admins); the shared `X-API-Key` remains as the bootstrap path for creating the first admin and for legacy scripts. Dashboard shows a sign-in screen only when the backend 401s — open-mode local dev unchanged. |
| S2 | Secrets management | Every credential (DB password, API keys, JWT secret) lives in a `.env` file today. No vault/secrets manager in the loop. |
| S3 | A real production hosting decision | `docker-compose.yml` is a single-instance local/dev setup — one Postgres container, one Redis container, no managed database, no autoscaling, no load balancer, no automated backups or disaster-recovery plan. |
| S4 | Observability | Partially done — `GET /metrics` (Prometheus format, `app/metrics.py`, auth-protected): optimizer cycle duration histogram + over-budget counter, hold-queue depth per hub, orders ingested, rate-limit rejections. Still open: error tracking (Sentry — needs an account/DSN), dashboards/alerting on top of the metrics (needs the hosting decision, S3). |
| ~~S5~~ | ~~General API rate limiting~~ | **Done** — `app/rate_limit.py`: per-IP per-minute budget across the whole API (middleware, runs before auth so it shields API-key guessing too), Redis counter + NX-guarded TTL, fails open on Redis outage, 429 + Retry-After when exceeded. Disabled by default in dev (`RATE_LIMIT_REQUESTS_PER_MINUTE=0`); the targeted per-email/per-phone limiters remain. |
| S6 | A real security review | Nobody outside this build has looked at this from a security angle yet — worth doing before real orders/drivers/money flow through it. |
| ~~S7~~ | ~~Twilio inbound-webhook signature verification~~ | **Done** — `app/messaging/twilio_signature.py` (pure HMAC-SHA1 algorithm, constant-time compare) enforced in `app/api/webhooks.py`: 403 on missing/invalid `X-Twilio-Signature` when `TWILIO_AUTH_TOKEN` is set; skipped (logged) when unset, same stub posture as `SmsClient`. `TWILIO_WEBHOOK_PUBLIC_URL` handles the behind-a-proxy URL mismatch. |
| ~~S8~~ | ~~Rate-limit `POST /client/auth/login`~~ | **Done** — `app/client_auth/login_rate_limit.py`, same "counter + NX-guarded TTL" shape as driver OTP issuance; resets on a successful login. |
| ~~S9~~ | ~~Enforce `CLIENT_JWT_SECRET` ≠ `DRIVER_JWT_SECRET` at startup~~ | **Done** — `app/config.py`'s `assert_jwt_secrets_are_distinct()`, called from `app/main.py`'s lifespan alongside the two existing per-secret checks; refuses to start outside `development` if both are ever set to the same real value. |

### Orchestrator dashboard (internal, for hub staff)

| # | Item | Why it matters |
|---|---|---|
| ~~D1~~ | ~~Add a "list hubs" endpoint~~ | **Done** — `GET /hubs` (`app/api/routes.py`, active hubs sorted by name, `include_inactive=true` for admin views); the dashboard's TopBar is now a real hub dropdown, falling back to the old UUID text input if the hubs list can't load. |
| ~~D2~~ | ~~Stop baking the API URL in at Docker build time~~ | **Done** — both `dashboard/` and `client-portal/` serve a `runtime-config.js` regenerated at container start from `API_BASE_URL` (+ `API_SHARED_SECRET` for dashboard) env vars via nginx's `/docker-entrypoint.d/`; one built image now points anywhere with `docker run -e`. VITE_* build args remain as a legacy fallback only. |

### Driver app

| # | Item | Why it matters |
|---|---|---|
| A1 | Push notifications | Biggest real gap for daily use — a driver has to have the app open and polling to see a new job offer. No push infrastructure exists at all. |
| A2 | Real camera/barcode scanning | "Scan next parcel" is a manual tap that increments a count — no camera/barcode SDK wired in. |
| A3 | Real photo/signature capture + upload pipeline | Proof-of-delivery "tap to capture" records a placeholder URL — no actual camera/signature-pad integration or image storage. |
| A4 | A real PIN-issuance/verification system | The PIN field on proof-of-delivery is recorded but never checked against anything — there's no system that issues a real PIN to verify against. |
| A5 | Maps SDK / turn-by-turn navigation | Screens 1h/1i/1l are merged into one stops-list view with no live turn directions. |
| A6 | Mobile app store deployment pipeline | No EAS build config, no TestFlight/Play Store presence — the app only runs today via the Expo dev client. |
| A7 | Masked voice calling | Only masked SMS is built. Voice needs a separate, heavier Twilio Voice/Proxy integration. |
| ~~A8~~ | ~~Harden inbound-SMS reply matching~~ | **Done** — `app/api/webhooks.py`'s `_match_reply_to_thread`: candidates limited to a 24h window, conversations whose stop is still active are preferred over completed ones, and residual ambiguity (two active threads, same number) falls back to most-recent with a structured warning logged. Per-conversation proxy sessions (Twilio Proxy) remain the real fix if this ever matters at scale. |
| A9 | Real earnings formula + payroll integration | In progress — scaffolding shipped (`app/payroll/`): `PayrollClient` abstraction with `StubPayrollClient` (active, logs instead of sending) + `RipplingClient` skeleton (deliberately raises rather than guessing Rippling's pay-input API — verify against sandbox credentials first); `Driver.payroll_employee_id` link column (migration `0008`, drivers get hired IN Rippling, LMX never stores SSNs/bank details); pay-period export assembling BOTH hours and completed drops per driver so either pay formula can wire in. Still gated on: Rippling account + API access + sandbox, the pay formula, and the pay schedule (no scheduler wired to the export until that's decided). |

### Whole components not started at all

C1 (client-facing dashboard) and C2 (shop SMS) — the two items that used
to be listed here — shipped in Phase 8 (see below). What's left in this
category:

| # | Item | Why it matters |
|---|---|---|
| C3 | A real billing/invoicing system | Partially done — monthly statement assembly (`app/billing/statements.py`: delivered orders only, grouped by tier/rate, NULL fees surfaced as an explicit unbilled count, never $0) + invoice PDF (`app/billing/invoice_pdf.py`, reportlab), exposed to clients (`GET /client/billing/statements/{year}/{month}[/invoice.pdf]` + a Billing card in the portal) and to ops (`GET /admin/clients/{id}/statements/...` — same assembly, identical numbers). Still open: payment collection (needs a processor decision) and statement persistence/numbering for accounting-grade invoices. |
| C4 | Multi-user client accounts | Client portal is explicitly one login per client company today (`Client.portal_email`), per Sourabh's call — a real multi-user/role model (e.g. AP vs. ops contacts at the same client) is a later decision, not an oversight. |
| C5 | Self-service client signup | New clients are onboarded only via the internal `POST /admin/clients` form (dashboard) — there's no client-initiated signup flow, by design (this is a B2B onboarding relationship, not self-serve SaaS), but worth naming explicitly so it isn't assumed to exist. |

### Testing / process

| # | Item | Why it matters |
|---|---|---|
| ~~T1~~ | ~~No load/performance test against the design doc's <5s-cycle/20-driver/100-order budget~~ | **Done** — `tests/integration/test_optimizer_load.py` seeds exactly the design load against real Postgres+Redis and asserts the budget; measured 0.018s with the stub engine, i.e. the pipeline we own uses <1% of the budget. The live Google Route Optimization call's network latency is measured separately under E1's live verification. |
| T2 | Local dev/test sandbox can't fully exercise Redis-backed rate limiting (driver OTP issuance, and now client login) | The bundled test Redis (`redislite`/the sandbox's standalone binary, both v6.2.14) doesn't support `EXPIRE...NX`; production Redis (7-alpine) does. Confirmed not a real bug, but worth a note so it doesn't get "rediscovered" and mistaken for one - now affects `app/client_auth/login_rate_limit.py`'s tests too, same root cause. |

---

## Part 2 — Phased plan to Hub 1

Phases 1–3 (driver app: core delivery loop, profile, earnings/messaging)
and the Phase 1 core backend + internal dashboard are done. What follows
is the path from there to a real, running Hub 1.

**These phases are not strictly sequential.** Once there's more than one
engineer (B1), 4/5/6/7 can mostly run in parallel — they touch different
parts of the system. Phase 8 (client dashboard, Hot Shot tier, tiered
billing, shop SMS, minimal client onboarding) has already shipped, ahead
of the sequencing below — Sourabh's call, since the first client wanted
these at MVP rather than deferred, and LMX had no committed dates
constraining the build order.

### Phase 4 — Make the placeholders real
**Goal:** every "unverified" or "reconstructed from a summary" caveat in
`docs/ARCHITECTURE.md` gets closed before real orders run through it.

- E1, E2 (Google Route Optimization — provision a service account, run a
  real call, tune skip penalties)
- E3 (Epicor payload — needs B2)
- E4, E5 (batch-hold logic + SLA windows — needs B3)
- E6, E7 (Learning Loop naming sign-off + real scheduler)

**Exit criteria:** no remaining "not yet verified against a live X" line
in `docs/ARCHITECTURE.md`'s core-backend sections.

### Phase 5 — Security & production infrastructure
**Goal:** safe to run with real orders, real drivers, and eventually real
money — not just correct in a demo.

- S1–S7 (real auth, secrets management, hosting decision, observability,
  rate limiting, security review, Twilio webhook signing)
- E8 (event bus — only urgent once this needs to run as more than one
  instance, which a real hosting decision (S3) will likely force)

**Exit criteria:** a documented production runbook and a completed
security review.

### Phase 6 — Driver app hardening
**Goal:** something a real driver can rely on for a full shift without
developer tooling.

- A1 (push notifications — do this first; everything else in this phase
  is polish by comparison)
- A2–A5 (camera/barcode, photo/signature capture, PIN system, maps SDK)
- A6 (app store deployment — start with TestFlight/Play internal testing,
  not a public release, for the first pilot)
- A7, A8 (masked voice calling, harden SMS reply matching)

**Exit criteria:** a driver can install this from an internal beta channel
and complete a full day's routes without needing you or dev tooling.

### Phase 7 — Payroll
**Goal:** earnings becomes a real number, not an estimate.

- B4 (provider decided: Rippling — remaining: account + payroll module,
  API credentials/sandbox, driver↔employee mapping)
- A9 (real pay formula — a business decision LMX/finance needs to make,
  not something to reverse-engineer from code; wire the chosen payroll
  API once the formula's agreed)

### Phase 8 — Client dashboard, Hot Shot tier, tiered billing, shop SMS — ✅ DONE
**Goal:** make LMX successful with the first client — a full client
portal (not a placeholder), a premium priority delivery tier, per-tier
billing, and automatic shop notifications, all as MVP requirements rather
than deferred to a later phase.

Shipped:
- **HOT_SHOT tier** — a fourth SLA tier, ahead of T1 in urgency, added to
  the `sla_tier` Postgres enum (migration `0007`). Classified from a
  payload flag (`app/sla/engine.py`), bypasses the batch-hold queue's
  cluster-mate wait entirely (`app/batch_queue/queue.py`), is prioritized
  in both the optimizer stub and the Google Route Optimization skip
  penalties (`app/optimizer/google_routes_client.py`), and — critically —
  is never commingled into a shared pickup stop with another order, even
  from the same shop (`app/api/driver_routes.py`'s `accept_offer`).
- **Tiered client billing** — a `client_rates` table (per client, per
  tier, `$/drop`), computed into `Order.fee_cents` at ingestion time
  (`app/ingestion/service.py`). Null (not zero) when no rate is
  configured, so a billing gap can never silently look like a free
  delivery.
- **Client portal** (`client-portal/`) — a separate Vite/React/TS/
  Tailwind app from the internal `dashboard/`, with its own real
  password-based JWT auth (`app/client_auth/`, one login per client
  company). Shows order history, status, and per-order fee; billing
  beyond that is intentionally minimal pending C3.
- **Minimal client onboarding** — `POST /admin/clients`
  (`app/api/admin_routes.py`) creates a client, its first shop, its
  per-tier rates, and its portal login in one action; also has a form in
  the internal dashboard (`OnboardClientForm.tsx`).
- **Shop SMS** — one-way, automatic notifications to a shop's phone at
  pickup and at "driver en route," with Hot-Shot-specific copy for both,
  reusing the existing Message/SmsClient infrastructure
  (`app/messaging/shop_notifications.py`).

**Follow-ups this phase surfaced, not yet done:** E10, C3, C4, C5 (S8 and S9
are now both done — see Part 1's tables above).

### Phase 9 — Hub 1 pilot
**Goal:** prove the model live.

- B2 (signed client — this is the actual gate for this phase)
- Run real orders through the full pipeline
- E9 (validate/recalibrate the 2.5 DPH figure and SLA hold windows
  against real data — this is the whole point of a pilot)
- T1 (load-test against realistic Hub 1 volume before it's live volume)

**Exit criteria:** a week of real Hub 1 operation with the DPH assumption
either confirmed or replaced by a real number, and hold windows retuned
from actual data instead of the Phase 1 placeholders.

---

## A note on sequencing

This order is a recommendation, not a fixed plan — it optimizes for "de-risk
what's already built before adding more," but the actual constraint is
almost always headcount (B1). Worth revisiting once that hire is in place,
since a team of two can run Phases 4–7 in parallel in a way one person
can't.
