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
| B4 | Provision a real Rippling account/API credentials | **Rippling** was chosen (not ADP/Gusto — handles both W2 payroll and 1099 contractor payments on one platform, reusing the same integration across the worker-classification phases below). `app/payroll/`'s `PayrollProvider` interface and the real hours/overtime engineering behind it are built and tested against a stub (`docs/NEXT_STEPS.md` item 22) — no money moves until a real account exists and `RipplingPayrollProvider`'s endpoint shape (a best-effort guess, unverified) is confirmed against it. |
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
| E7 | Wire a real scheduler for the Learning Loop's nightly job | Still manual-trigger only (`POST /learning-loop/{hub_id}/run-nightly-job`) — needs a cron/scheduled task instead of a person remembering to call it. |
| ~~E8~~ | ~~Move the event bus off in-process~~ | **Done** — `app/events/bus.py` now coordinates through Redis (a `dirty_hubs` set for idempotent cross-instance coalescing, a `SET NX EX` lock for mutual exclusion) instead of local asyncio state, with a fixed-interval poll loop started at app startup. Live-verified with two real, separate app containers sharing one Redis: an event published on instance A while instance A was paused (never got to run its own poll loop) was still picked up and completed by instance B; letting both race normally, exactly one of them ran a given hub's cycle, never both. |
| E9 | Validate the 2.5 deliveries-per-hour (DPH) figure | Called out by the peer review as a model assumption, not an established fact — only provable with real driver/order data at Hub 1 (gated on B2). |
| E10 | Tune HOT_SHOT's skip-penalty/hold-window placeholders | Phase 8 added `HOT_SHOT` ahead of T1 in `SLA_TIER_SKIP_PENALTY` and a 2-minute hold window (`app/sla/engine.py`, `app/optimizer/google_routes_client.py`) — same "reasonable guess, not calibrated" status as E2/E5, now for a fourth, premium-priced tier. |
| ~~E11~~ | ~~Real hours-worked/overtime calculation + admin payroll-run endpoint~~ | **Done** — `app/payroll/hours.py` replaces the old route-span earnings heuristic with real on-duty hours from a durable `driver_shift_events` log, plus federal 40hr/week overtime for `w2` drivers. New `POST /admin/payroll/{hub_id}/run`. Known gaps: no state-specific daily-OT rules, and a workweek split across two pay periods only sees hours visible in the period being computed — see `docs/NEXT_STEPS.md` item 22. |
| ~~E12~~ | ~~Real vehicle-capacity tracking for mid-route insertion~~ | **Done** — replaced the placeholder `MAX_STOPS_PER_ACTIVE_ROUTE` stop-count cap with real `DriverState.capacity_units - load_units` tracking, now actually incremented/decremented by `complete_stop`/`flag_stop_issue` — see `docs/NEXT_STEPS.md` item 21. |

### Security & production readiness

| # | Item | Why it matters |
|---|---|---|
| ~~S1~~ | ~~Real per-user authentication for the ops dashboard~~ | **Done** — `app/ops_auth/`'s `OpsUserAuthMiddleware` replaces the old shared `X-API-Key` with a real per-account Bearer JWT (same password+JWT shape as the client portal); `dashboard/` has a real login screen. `scripts/create_ops_user.py --role admin\|viewer` bootstraps accounts. Real role model now exists (migration `0012`): `admin` (everything) vs `viewer` (read-only) - `require_admin` gates the mutating endpoints (run-cycle, run-nightly-job, onboard a client, revoke a driver device, run payroll), and the dashboard hides those controls entirely for a viewer rather than showing them disabled. Live-verified with real admin and viewer accounts over real HTTP and in a real browser. Still just two roles, not a full permissions matrix - revisit if a reason for finer granularity ever shows up. |
| ~~S2~~ | ~~Secrets management~~ | **Partially done** — `app/secrets_provider.py`'s `SecretsProvider` abstraction (`EnvSecretsProvider` today, a real `AWSSecretsManagerProvider` ready but unexercised without a real AWS account) loads into `os.environ` before `Settings` is constructed in `app/config.py`, so a real vault's values take precedence over `.env` with zero changes needed anywhere else `settings.foo` is read (`os.environ.setdefault` never overrides an explicit env var). Same "unconfigured -> stub" status as Twilio/Rippling/Sentry, one level up. Real gap: which vault to actually adopt, when to migrate, and how rotation should work operationally are still open, deployment-platform-specific decisions (same nature as S3). |
| S3 | A real production hosting decision | `docker-compose.yml` is a single-instance local/dev setup — one Postgres container, one Redis container, no managed database, no autoscaling, no load balancer, no automated backups or disaster-recovery plan. |
| ~~S4~~ | ~~Observability~~ | **Partially done** — error tracking via Sentry (`app/logging_config.py`), same "unconfigured credential -> no-op" status as Twilio/Rippling until a real account/DSN exists. A structlog processor forwards warning/error/critical/exception-level events straight to Sentry, since this codebase's structlog setup never touches stdlib logging (Sentry's default `LoggingIntegration` hook would otherwise miss every "caught, logged, and intentionally swallowed" exception, e.g. `HubEventBus`'s handler-failure path) - so both unhandled exceptions (via the FastAPI/Starlette integrations) and deliberately-caught-and-logged ones reach Sentry. Metrics dashboards/alerting still not started. |
| ~~S5~~ | ~~General API rate limiting~~ | **Done** — `app/rate_limit.py`'s `GeneralRateLimitMiddleware`, a Redis counter+NX-TTL per client IP (deliberately generous - this system leans on client-side polling, see the module's own docstring), 429 + `Retry-After` once tripped, `/health`/docs paths exempt. Known limitation: keyed by the direct TCP peer, not `X-Forwarded-For` - correct only until a real reverse proxy sits in front (Phase 5's hosting decision). |
| ~~S6~~ | ~~A real security review~~ | **Partially done** — a self-review pass across auth, authorization, injection/input-validation, and secrets/CORS/infra. Fixed: driver OTP codes were unconditionally echoed in the API response regardless of Twilio configuration (`app/driver_auth/otp_store.py` — a hardcoded `sent_via_sms=False` meant this would have kept leaking even with real Twilio creds configured, since no real send was ever wired up either; now actually sends via `TwilioSmsClient` and only omits the code when that succeeds), the phone-number-existence check on `request-otp` was an unthrottled enumeration oracle (rate limit now charged before the DB lookup), two fleet-state-mutation endpoints were missing `require_admin` (a viewer could overwrite any driver's status/location), `docker-compose.yml`'s Postgres/Redis ports were published on every interface with a well-known default password, the app container ran as root, a few request bodies took unconstrained strings where a `Literal`/length bound was cheap and correct, and the Twilio webhook now warns loudly at boot if signature verification would be silently disabled in production. Real gap still open: the JWT-secret/webhook boot-time checks all key off `ENVIRONMENT != "development"`, so an operator who simply forgets to set `ENVIRONMENT` in production gets zero protection instead of the most — a cross-cutting fail-safe-default question worth a deliberate decision, not a change made unilaterally in this pass. No one outside this build has reviewed it yet either. |
| ~~S7~~ | ~~Twilio inbound-webhook signature verification~~ | **Done** — `app/messaging/twilio_signature.py` verifies `X-Twilio-Signature` (HMAC-SHA1 over the full URL + sorted POST params, keyed by `TWILIO_AUTH_TOKEN`), enforced only once that token is configured; `TWILIO_WEBHOOK_BASE_URL` overrides scheme+host for the eventual reverse-proxy case. |
| ~~S8~~ | ~~Rate-limit `POST /client/auth/login`~~ | **Done** — `app/client_auth/login_rate_limit.py`, same "counter + NX-guarded TTL" shape as driver OTP issuance; resets on a successful login. |
| ~~S9~~ | ~~Enforce `CLIENT_JWT_SECRET` ≠ `DRIVER_JWT_SECRET` at startup~~ | **Done** — `app/config.py`'s `assert_jwt_secrets_are_distinct()`, called from `app/main.py`'s lifespan alongside the two existing per-secret checks; refuses to start outside `development` if both are ever set to the same real value. |

### Orchestrator dashboard (internal, for hub staff)

| # | Item | Why it matters |
|---|---|---|
| ~~D1~~ | ~~Add a "list hubs" endpoint~~ | **Done** — `GET /hubs` (`app/api/routes.py`, `app/schemas/hub.py`'s `HubSummary`) backs a real dropdown in `dashboard/src/components/TopBar.tsx`; the old raw-UUID text field survives only as a fallback if that fetch fails or returns empty. The "Onboard a new client" form takes `hubId` from the same TopBar-selected value, so it inherited the fix with no separate change. |
| ~~D2~~ | ~~Stop baking the API URL in at Docker build time~~ | **Done** — `dashboard/docker/generate-env-config.sh` runs as an nginx entrypoint script at container start, writing `env-config.js` from the real `DASHBOARD_API_BASE_URL` env var; `src/lib/api.ts` reads `window.__RUNTIME_CONFIG__` first, falling back to the Vite build-time env var for local `npm run dev` only. Pointing the same image at a different API is now a restart, not a rebuild. `client-portal/` mirrors this exact pattern. |

### Cross-app / branding

| # | Item | Why it matters |
|---|---|---|
| ~~D3~~ | ~~Real brand assets + unified brand-green accent across dashboard, client portal, driver app~~ | **Done** — placeholder "L"/"LX" box logos and mismatched indigo/approximate-green accents replaced with the real LMX mark and the decided brand green (`#0A6644`) everywhere. See `docs/NEXT_STEPS.md` item 20. Known gaps: no vector master (SVG/AI) exists, so every asset is raster-derived; native icon/splash/adaptive-icon changes need a real device build (A6) to verify visually — Expo Go always shows its own icon regardless of `app.json`. |

### Driver app

| # | Item | Why it matters |
|---|---|---|
| ~~A0~~ | ~~Screen consolidation, flag-an-issue, offline write queue, device-bound biometric auth, live route-change push~~ | **Done**, ahead of this roadmap's original sequencing — matches a separate wireframe spec's design intent (consolidated screens, offline-first, device-bound re-entry instead of repeated OTP, live notification of mid-route changes) discovered mid-build. See `docs/NEXT_STEPS.md` item 19 for full detail. Real gap: this is foreground-only SSE (the driver has to have the app open), not true OS-level push — A1 below (job-offer push while backgrounded/killed) is still a distinct, unstarted gap. |
| A1 | Push notifications | Biggest real gap for daily use — a driver has to have the app open and polling to see a new job offer. No push infrastructure exists at all (A0's live route-change push is a different, foreground-only mechanism for an already-active route, not this). |
| A2 | Real camera/barcode scanning | "Scan next parcel" is a manual tap that increments a count — no camera/barcode SDK wired in. |
| A3 | Real photo/signature capture + upload pipeline | Proof-of-delivery "tap to capture" records a placeholder URL — no actual camera/signature-pad integration or image storage. |
| A4 | A real PIN-issuance/verification system | The PIN field on proof-of-delivery is recorded but never checked against anything — there's no system that issues a real PIN to verify against. |
| A5 | Maps SDK / turn-by-turn navigation | Screens 1h/1i/1l are merged into one stops-list view with no live turn directions. |
| A6 | Mobile app store deployment pipeline | No EAS build config, no TestFlight/Play Store presence — the app only runs today via the Expo dev client. Also the only way to visually verify A0/D3's native icon/splash/adaptive-icon work, since Expo Go always shows its own icon. |
| A7 | Masked voice calling | Only masked SMS is built. Voice needs a separate, heavier Twilio Voice/Proxy integration. |
| ~~A8~~ | ~~Harden inbound-SMS reply matching~~ | **Done** — `_find_matching_thread` (`app/api/webhooks.py`) infers channel from `From` before matching (closes a cross-driver collision: every driver's support messages previously shared one `counterparty_phone`), prefers threads with no inbound reply already recorded, and requires a customer-channel stop to still be non-terminal. Real, flagged remaining gap: two genuinely concurrent, still-unanswered threads to the same number can't be told apart without a Twilio Proxy-style number pool or a reply reference code - now logged as ambiguous rather than silently guessed at. |
| A9 | Real earnings formula + payroll integration | Hours/overtime engineering and the `PayrollProvider` interface are now built (E11, gated on B4 for a real account) — what's left is genuinely a business decision, not code: state-specific daily-OT rules beyond the federal 40hr/week baseline, and confirming the Rippling integration against a live account. |
| A10 | 1099 contractor onboarding — resolve the worker-autonomy question | Phase 2 of the W2 → 1099 → gig rollout (`docs/NEXT_STEPS.md` item 22). Today's single-push-offer, single-online-toggle, ops-provisioned model reads closer to "at-will staff" than "independent contractor" — a legal call on how much real job-choice/schedule-flexibility the product needs to show for defensible 1099 classification, needed **before** 1099 onboarding ships, not an engineering task itself. |
| A11 | Gig per-delivery pay model | Phase 3 of the same rollout. No fare/price field exists anywhere in `Order`/`Route`/`Stop` — job offers show stop count/SLA tier, never a dollar amount. Needed before any gig-classified driver could be onboarded: a real pricing model, showing pay on the offer itself, instant/fast payout (e.g. Stripe Connect — a payroll-cycle assumption doesn't fit gig work), self-serve onboarding, and per-trip identity re-verification. |

### Whole components not started at all

C1 (client-facing dashboard) and C2 (shop SMS) — the two items that used
to be listed here — shipped in Phase 8 (see below). What's left in this
category:

| # | Item | Why it matters |
|---|---|---|
| C3 | A real billing/invoicing system | Phase 8 only computes and stores a per-order `fee_cents` (`app/models/order.py`) — there's no statement generation, invoice PDF, or payment collection anywhere. The client portal's billing view is deliberately minimal pending this. |
| C4 | Multi-user client accounts | Client portal is explicitly one login per client company today (`Client.portal_email`), per Sourabh's call — a real multi-user/role model (e.g. AP vs. ops contacts at the same client) is a later decision, not an oversight. |
| C5 | Self-service client signup | New clients are onboarded only via the internal `POST /admin/clients` form (dashboard) — there's no client-initiated signup flow, by design (this is a B2B onboarding relationship, not self-serve SaaS), but worth naming explicitly so it isn't assumed to exist. |

### Testing / process

| # | Item | Why it matters |
|---|---|---|
| ~~T1~~ | ~~Load/performance test against the design doc's <5s-cycle/20-driver/100-order budget~~ | **Done** — `tests/integration/test_optimizer_load.py`, real Postgres Order/Driver rows (not just Redis/hold-queue data) so the writeback step does the same real work a live cycle would. Measured: 20 drivers/100 orders completes in **~0.08s** (~65x margin under the 5s budget); a 5x stress probe (100 drivers/500 orders, not a contractual target) completes in **~0.24s**. This tests the stub nearest-neighbor engine only - Google Route Optimization's own API latency (E1) is a separate, unmeasured external dependency without live credentials. |
| T2 | Local dev/test sandbox can't fully exercise Redis-backed rate limiting (driver OTP issuance, and now client login) | The bundled test Redis (`redislite`/the sandbox's standalone binary, both v6.2.14) doesn't support `EXPIRE...NX`; production Redis (7-alpine) does. Confirmed not a real bug, but worth a note so it doesn't get "rediscovered" and mistaken for one - now affects `app/client_auth/login_rate_limit.py`'s tests too, same root cause. |

---

## Part 2 — Phased plan to Hub 1

Phases 1–3 (driver app: core delivery loop, profile, earnings/messaging)
and the Phase 1 core backend + internal dashboard are done. What follows
is the path from there to a real, running Hub 1.

**These phases are not strictly sequential.** Once there's more than one
engineer (B1), 4/5/6/7 can mostly run in parallel — they touch different
parts of the system. Phase 8 (client dashboard, Hot Shot tier, tiered
billing, shop SMS, minimal client onboarding) and part of Phase 6 (A0's
screen redesign/offline queue/biometric auth/live push) and part of Phase
7 (W2 payroll's engineering) have already shipped, ahead of the
sequencing below — Sourabh's calls, since none of these had committed
dates constraining the build order.

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

A0 (screen consolidation, flag-an-issue, offline write queue, device-bound
biometric auth, live route-change push) already shipped ahead of this
phase's sequencing — Sourabh's call, following a separate wireframe spec
discovered mid-build (`docs/NEXT_STEPS.md` item 19). What's left:

- A1 (push notifications — do this first; everything else in this phase
  is polish by comparison)
- A2–A5 (camera/barcode, photo/signature capture, PIN system, maps SDK)
- A6 (app store deployment — start with TestFlight/Play internal testing,
  not a public release, for the first pilot; also the only way to
  visually verify A0/D3's native icon/splash/adaptive-icon work)
- A7, A8 (masked voice calling, harden SMS reply matching)

**Exit criteria:** a driver can install this from an internal beta channel
and complete a full day's routes without needing you or dev tooling.

### Phase 7 — Payroll & worker classification
**Goal:** earnings becomes a real number, not an estimate — phased across
three worker classifications per Sourabh's stated sequencing: W2 employees
first (paid monthly), then 1099 contractors (paid weekly), then gig
per-delivery workers.

**W2 (Phase 1 of this rollout) — mostly done:** `Driver.employment_type`/
`hourly_rate_cents`, a durable `driver_shift_events` log, real on-duty
hours replacing the old route-span heuristic, federal 40hr/week overtime,
a monthly pay period, and the `PayrollProvider` interface (Rippling
chosen — see B4) are all built and tested (`docs/NEXT_STEPS.md` item 22).
What's left is B4 (a real Rippling account) and any state-specific
daily-OT rules beyond the federal baseline — a business/legal decision,
not more reverse-engineering from code.

**1099 (Phase 2) — not started:** A10 (the worker-autonomy/misclassification
question needs a legal answer before this ships) plus W-9 collection
(likely inside Rippling's own onboarding, same as W2's I-9/W-4, not a new
screen in this app).

**Gig (Phase 3) — not started:** A11 (a real per-delivery fare model,
priced offers, instant payout, self-serve onboarding, per-trip identity
re-verification) — the largest of the three, closer to a second product
line sharing this backend than a config change.

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
