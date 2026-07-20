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
| B4 | Choose a payroll provider — ADP or Gusto | Drivers are W2 employees; the earnings screen is placeholder-only until this is picked and provisioned. |
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
| E8 | Move the event bus off in-process | `app/events/bus.py` only works within a single running instance — the moment this runs as more than one process/container, event-triggered re-optimization silently stops working for events raised on a different instance than the one handling a given request. |
| E9 | Validate the 2.5 deliveries-per-hour (DPH) figure | Called out by the peer review as a model assumption, not an established fact — only provable with real driver/order data at Hub 1 (gated on B2). |
| E10 | Tune HOT_SHOT's skip-penalty/hold-window placeholders | Phase 8 added `HOT_SHOT` ahead of T1 in `SLA_TIER_SKIP_PENALTY` and a 2-minute hold window (`app/sla/engine.py`, `app/optimizer/google_routes_client.py`) — same "reasonable guess, not calibrated" status as E2/E5, now for a fourth, premium-priced tier. |

### Security & production readiness

| # | Item | Why it matters |
|---|---|---|
| S1 | Real per-user authentication for the ops dashboard | `SharedSecretAuthMiddleware` gates the API behind one shared `X-API-Key` — anyone with the key sees everything, no per-user identity, no roles. Fine for one person on a private network; not fine for a team. |
| S2 | Secrets management | Every credential (DB password, API keys, JWT secret) lives in a `.env` file today. No vault/secrets manager in the loop. |
| S3 | A real production hosting decision | `docker-compose.yml` is a single-instance local/dev setup — one Postgres container, one Redis container, no managed database, no autoscaling, no load balancer, no automated backups or disaster-recovery plan. |
| S4 | Observability | No error tracking (e.g. Sentry), no metrics dashboards, no alerting. Right now the only way to know something broke is someone noticing. |
| S5 | General API rate limiting | Only driver OTP issuance is rate-limited (`app/driver_auth/otp_store.py`). Every other endpoint has none. |
| S6 | A real security review | Nobody outside this build has looked at this from a security angle yet — worth doing before real orders/drivers/money flow through it. |
| S7 | Twilio inbound-webhook signature verification | `POST /webhooks/twilio/inbound-sms` currently trusts whatever posts to it — no `X-Twilio-Signature` check yet (`app/api/webhooks.py`'s own docstring flags this). Must close before a real Twilio number points here. |
| ~~S8~~ | ~~Rate-limit `POST /client/auth/login`~~ | **Done** — `app/client_auth/login_rate_limit.py`, same "counter + NX-guarded TTL" shape as driver OTP issuance; resets on a successful login. |
| ~~S9~~ | ~~Enforce `CLIENT_JWT_SECRET` ≠ `DRIVER_JWT_SECRET` at startup~~ | **Done** — `app/config.py`'s `assert_jwt_secrets_are_distinct()`, called from `app/main.py`'s lifespan alongside the two existing per-secret checks; refuses to start outside `development` if both are ever set to the same real value. |

### Orchestrator dashboard (internal, for hub staff)

| # | Item | Why it matters |
|---|---|---|
| D1 | Add a "list hubs" endpoint | No read API exists for the `hubs` table, so hub selection is a raw UUID text field, not a dropdown — including in Phase 8's new "Onboard a new client" form, which inherits the same gap. |
| D2 | Stop baking the API URL in at Docker build time | Vite bakes `VITE_API_BASE_URL` in at build time — pointing the dashboard at a different API means rebuilding the image, not just restarting the container. |

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
| A8 | Harden inbound-SMS reply matching | Currently matches by phone number only — a driver with two concurrent conversations to the same number could have a reply attached to the wrong one. |
| A9 | Real earnings formula + payroll integration | Current estimate is an explicitly-labeled placeholder ($18/hr flat) — gated on B4. |

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
| T1 | No load/performance test against the design doc's <5s-cycle/20-driver/100-order budget | The optimizer has a hard performance target that's never been tested under realistic load. |
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

- B4 (choose ADP or Gusto)
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
