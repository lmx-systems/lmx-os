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

### Autonomy partners

How autonomous delivery — AV cars, sidewalk bots, drones, each run by
their operator — plugs in (cofounder conversation, July 2026). The
decision: **no separate app; autonomy partners integrate into the same
dispatch loop the driver app uses, via a capacity-provider adapter
layer.** The driver app is the *human* interface to LMX's
offer→accept→track→deliver loop; a partner's fleet API is a *machine*
interface to the identical loop. Their operators supervise vehicles in
the partner's own console — LMX owns the delivery lifecycle, the partner
owns the vehicle. This is the supply-side mirror of the ingestion
layer's demand-side adapters (Epicor/flat-file): nothing downstream
should ever branch on which partner carried an order, the same way
nothing branches on which POS created it.

**None of this is being built now.** The near-term rule it imposes: when
fleet/offer models get touched for other reasons, generalize toward a
"courier" abstraction rather than deepening the human-driver coupling.
The whole area is gated on a signed autonomy partner (a B-item when it
becomes real) — but the abstraction debt is named here so it's a design
constraint today, not a rewrite later.

| # | Item | Why it matters |
|---|---|---|
| P1 | Courier abstraction over the fleet model | Today's fleet state is human-shaped (`DriverCandidate`, phone/OTP auth, an implicit person behind every route). Generalize to a courier with a `provider_type` (human_driver \| autonomy_partner), service area/geofence, speed profile, payload limits, and capability flags (can batch multi-stop? sidewalk-only? weather-sensitive?). Human drivers become one provider type, not the type system. |
| P2 | Capacity-provider adapter layer | The supply-side mirror of `app/ingestion/adapters/`: one adapter per partner normalizing (a) capacity in — which vehicles are available, where, with what limits; (b) assignments out — a `RouteOffer` becomes an API call the partner's fleet manager accepts/declines within the same TTL a driver gets; (c) status back — partner webhooks map to our stop-status transitions and PoD. Ships with a stub partner (same unconfigured→stub pattern as Twilio/Rippling) so the whole loop is testable before any real partner exists. |
| P3 | Mode-aware dispatch | The optimizer gains eligibility filtering (weight, distance, geofence, tier, weather) before candidate generation, and — later — cost-per-drop mode selection: choose the cheapest *eligible* mode per order, which is where autonomy actually pays off economically. Ties directly to the unit-economics work (cost per drop vs. price per drop). |
| P4 | Unmanned handoff + proof of delivery | Nobody walks into the shop when a bot arrives: shop SMS grows a "load the bot" flow (compartment id, load-confirmed ack), and the customer side needs PIN-unlock delivery — which is exactly the A4 PIN issuance/verification item already on the driver-app list, making A4 shared infrastructure rather than app polish. |
| P5 | Partner settlement | Per-delivery payout to the partner — a third money flow next to client billing (in) and driver payroll (out), structurally the same shape as `client_rates`: per-partner, per-mode rates, monthly statements. Reuses C3's statement machinery. |
| P6 | Partner portal | A thin reporting surface (delivery history, settlement statements, failed-delivery disputes) like `client-portal/` — explicitly a *later* convenience, not the integration mechanism. Partners integrate through P2's API, full stop. |

### Competitive feature gaps

LMX is positioning LMX OS as the operating system for the whole company,
not just a dispatch tool — so it's worth checking it against the
category it's actually competing in. This is a feature-by-feature
comparison against four delivery/logistics platforms researched in July
2026: **Bringg** and **Wise Systems** (named directly), plus **Onfleet**
and **Locus** (added to round out the set — respectively the
small/mid-market and enterprise-retail-logistics ends of the same
category). Sourced from each vendor's public site, docs, and G2/Capterra
where accessible — see `docs/LMX_OS_Competitive_Feature_Analysis.docx`
for the full category-by-category tables and citations.

**Where LMX OS already holds its own:** the SLA-tier engine's strict
Hot-Shot non-commingling guarantee is a concrete, enforced rule none of
the four describe as precisely; the batch-hold queue's 4-question
decision logic is a more explicit, tunable batching strategy than the
generic "smart clubbing" language competitors use; the Annotation &
Learning Loop's human-approval gate (I2) is a more auditable model than
Locus's "agentic" DiSCO framing or Wise's compounding ML claims, once
I2 ships; and the autonomy-partner architecture (P1–P6) is already
designed in at the courier-abstraction level — none of the four have
live drone/sidewalk-bot/AV integration today, only Bringg lists
"autonomous" as a network category in concept.

**Where the gap is real** — the biggest single finding: LMX OS has **no
live GPS tracking at all** today. `Driver` has no location field, the
driver app never pings a position, and neither the ops dashboard nor any
customer-facing surface can show where a driver actually is. Every one
of the four competitors treats live map tracking as baseline table
stakes. That's F1/F2 below, and it's the prerequisite for F3.

| # | Item | Why it matters |
|---|---|---|
| F1 | Live driver location pipeline | Driver app periodically pings lat/lng to the backend; `Driver`/a new `DriverLocation` table stores current position. Prerequisite for F2 and F3 — today this doesn't exist at all, not even for internal ops use. No external dependency. |
| F2 | Live map view (ops dashboard) | Hub staff can see where every driver on shift actually is, not just their assigned stop list. Depends on F1. |
| F3 | Customer-facing live tracking page | A public link (sent to the actual delivery recipient, not the shop) showing driver position + ETA — what Bringg/Onfleet/Locus lead with on their marketing sites. Depends on F1; new component, no external dependency beyond a public route. |
| F4 | Outbound status webhooks + a small integrations surface | Today's ingestion adapters are demand-side *in* (Epicor, flat-file); nothing goes back *out* — no webhook a client system can subscribe to, no Shopify/Zapier-style connector. Bringg, Onfleet, and Wise Systems all name this as a feature. |
| F5 | Flexible/rate-table billing | `client_rates` today is flat per-drop, per-tier. Onfleet and Locus both support per-piece/per-weight/per-mile rate tables — worth revisiting once C3's statement persistence (already open) is tackled, same billing surface. |
| F6 | Real-time mid-route re-optimization | Today's optimizer solves fresh each cycle (E7's scheduler) rather than continuously re-sequencing an in-progress route as conditions change — Wise Systems' and Onfleet's core marketing claim. Depends on E1 (verify the live Google Route Optimization client) being done first. |
| F7 | Client- and ops-facing analytics dashboards | Reinforces I4 (already on the roadmap) — DPH, on-time %, driver leaderboards — but every competitor also exposes a *client-facing* cut of this (their own on-time rate, delivery volume) in the portal, which I4 doesn't currently scope. |
| F8 | White-label / multi-brand portal theming | Bringg, Onfleet (Enterprise tier), and Locus all offer a rebrandable client-facing surface. Relevant if LMX ever resells through a partner or franchise model — not urgent for Hub 1. |
| F9 | Hybrid gig-fleet overflow dispatch | Wise Systems' "DoorDash Dial" auto-routes overflow orders to third-party gig couriers by cost/rules. This is a nearer-term slice of P1/P2's courier abstraction — a human gig-fleet partner doesn't need to wait on a signed *autonomous* partner the way Phase 11 is gated; worth building the abstraction so this is possible sooner. |
| F10 | A real path to SOC 2 (or equivalent) certification | Every one of the four competitors leads their security page with SOC 2 Type II (plus ISO 27001, sometimes HIPAA/GDPR audits). Reinforces S6 (security review) and S2 (secrets management) — this raises their urgency from "good hygiene" to "the thing enterprise clients will ask for in a security questionnaire." |
| F11 | SSO/SAML for ops and client logins | S1 built real per-user auth with roles, but not SSO — Bringg, Wise Systems, and Locus all support it for enterprise buyers. |
| F12 | Network/territory optimization tooling | Wise Systems' "Network Optimization" (depot/zone redesign, distinct from daily routing) — relevant once LMX runs multiple hubs, not for a single Hub 1 pilot. |

**Sequencing:** F1/F2 slot into Phase 6 (driver app hardening, alongside
A1/A5); F3 follows as a fast Phase 8 follow-up once F1 exists; F4/F5/F8
join C3/C4/C5 as Phase 8 follow-ups; F6 depends on E1 in Phase 4; F7
folds into Phase 10's I4; F9 is a design refinement to Phase 11's P1/P2,
buildable independent of Phase 11's autonomy-partner gate; F10/F11
reinforce Phase 5; F12 is a later, multi-hub-scale item.

### Intelligence layer

Where LMX OS's "learning" actually stands, and the ladder to make it real
(cofounder conversation, July 2026). Honest baseline: the Dispatch
Optimizer optimizes but does not learn (every cycle solves from scratch);
the SLA engine and batch-hold queue are hand-written rules with
placeholder numbers; the one genuine seed is the Annotation & Learning
Loop — driver annotations (`stop_flags`, written from the driver app) ARE
labeled data, and the loop's shape (capture → detect → propose → human
approves → per-shop override) is right — but detection is frequency
counting (3+ repeated flags per shop → propose a fixed ±10min T2
hold-window change), it covers only hold windows, the learning influences
*when orders release* (indirectly shaping batches), never route
construction itself, and the human-approval step has no tool (I2).

**The governing constraint: this layer is data-gated, not code-gated.**
Every day Hub 1 runs before ground-truth capture (I1) exists is training
data lost forever — drive times we never recorded can't be backfilled.
Models can wait; instrumentation can't. I1 and I2 are therefore the only
urgent rows below, and both have zero external dependencies.

| # | Item | Why it matters |
|---|---|---|
| I1 | Ground-truth event capture | The prerequisite for every stage above it. Concretely: `Order.delivered_at` (real timestamp, replacing the `updated_at` proxy billing/portal use today); `Stop.arrived_at` (so time-at-stop = arrived→completed becomes measurable, per shop); per-leg actual drive time vs. the optimizer's implied estimate; offer decline/expiry reasons; hold-queue release timing (held→released delta per order, vs. its window). All buildable now with no external dependency. |
| I2 | Rule review & promotion flow | The missing rung of the existing loop: `proposed_rules` accumulate nightly with nowhere to go — no endpoint or dashboard UI promotes them to `active_rules` (today it would be a manual SQL insert). Endpoint + a dashboard review card (proposal, evidence count, confidence, approve/dismiss) completes component 6's core loop. No external dependency. |
| I3 | Broaden the annotation vocabulary | Two flag types exist (`hold_window_too_short`/`_too_long`). Real per-shop knowledge drivers accumulate — parking difficulty, gate/access codes, shop prep slowness, receiving-dock quirks — should become structured flags too, so the labeled dataset covers more than hold timing. Coordinates with E6's naming sign-off; the schema (`stop_flags.flag_type` is a free string) already allows it. |
| I4 | Descriptive analytics on the captured truth | DPH per driver/hub/day, SLA hit rates by tier, hold-window effectiveness (release timing vs. driver flags), ETA vs. actual. First consumer of I1's data; feeds the E9 DPH validation. Needs a few weeks of pilot data to be meaningful, not to be built. |
| I5 | Calibration from data | Already tracked as E2/E5/E9/E10 — retune skip penalties, hold windows, the 2.5 DPH figure from real Hub 1 data instead of placeholders. The intelligence-layer framing just makes explicit that I1+I4 are what make these possible. |
| I6 | Predictive models | ETA prediction per leg/stop; per-shop order-volume forecasting (staffing/positioning); offer-acceptance likelihood (feed the optimizer a probability, not a hope); learned per-shop service times as optimizer inputs — this is where "the next route incorporates the learning" becomes literally true, closing the loop from annotations/ground truth into route construction. Gated on months of I1 data, not on code. |
| I7 | Ops copilot | LLM layer over the (by then rich) structured ops data: daily hub summaries, "why was yesterday slow?", anomaly explanations, natural-language queries over the dashboard. Unlike I6 it needs no training data — just good structured data (I1/I4) and an LLM API key (the one external dependency in this table). |

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
- F6 (real-time mid-route re-optimization — competitive-parity item,
  depends on E1 landing first; see "Competitive feature gaps" above)

**Exit criteria:** no remaining "not yet verified against a live X" line
in `docs/ARCHITECTURE.md`'s core-backend sections.

### Phase 5 — Security & production infrastructure
**Goal:** safe to run with real orders, real drivers, and eventually real
money — not just correct in a demo.

- S1–S7 (real auth, secrets management, hosting decision, observability,
  rate limiting, security review, Twilio webhook signing)
- E8 (event bus — only urgent once this needs to run as more than one
  instance, which a real hosting decision (S3) will likely force)
- F10, F11 (a real SOC 2-or-equivalent path, and SSO/SAML for ops/client
  logins — every competitor researched leads with both; raises S6/S2's
  urgency from internal hygiene to a real sales blocker with enterprise
  clients)

**Exit criteria:** a documented production runbook and a completed
security review.

### Phase 6 — Driver app hardening
**Goal:** something a real driver can rely on for a full shift without
developer tooling.

- A1 (push notifications — do this first; everything else in this phase
  is polish by comparison)
- A2–A5 (camera/barcode, photo/signature capture, PIN system, maps SDK)
- F1, F2 (live driver location pipeline + the ops dashboard's live map —
  do these alongside A1/A5; every competitor researched treats live GPS
  tracking as baseline, and today LMX OS has none at all)
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
are now both done — see Part 1's tables above). Also picked up here from
the competitive analysis: F3 (customer-facing live tracking page, once
F1 lands in Phase 6), F4 (outbound status webhooks/integrations), F5
(flexible rate-table billing, alongside C3), and F8 (white-label portal
theming, if LMX ever resells through a partner).

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

### Phase 10 — Intelligence layer
**Goal:** the system gets measurably better at its job the longer it
runs — the annotations-and-ground-truth flywheel the design doc's
component 6 gestures at, made real. See Part 1's "Intelligence layer"
table for the item-by-item detail.

Sequencing relative to the pilot — this is the important part:
- **Before Phase 9 goes live:** I1 (ground-truth capture) and I2 (rule
  review/promotion flow). I1 because pilot days without instrumentation
  are training data lost forever; I2 because the pilot will generate
  driver annotations from day one, and proposals with no approval tool
  just pile up. I3 (broader annotation vocabulary) is strongly
  preferred pre-pilot too — the flags drivers can't write are the
  labels we won't have.
- **During/after the pilot:** I4 (descriptive analytics) as soon as
  there's a couple of weeks of data; I5 (calibration — E2/E5/E9/E10)
  is the pilot's whole point. F7 (client- and ops-facing analytics
  dashboards, from the competitive analysis) folds into I4's build —
  every competitor exposes a client-facing cut of this that I4 didn't
  originally scope; add it while I4 is being built, not as a separate
  pass.
- **Later, data-gated:** I6 (predictive models feeding the optimizer —
  where learning finally reaches route construction itself) and I7
  (ops copilot; the one row needing an external decision, an LLM
  provider/API key).

**Exit criteria (long-horizon):** at least one learned quantity (per-shop
service time or hold window) flows into dispatch decisions automatically
from data rather than from a hand-set placeholder, with a human approval
gate on every rule change.

### Phase 11 — Autonomy partners
**Goal:** deliveries carried by autonomous vehicles (AV cars, sidewalk
bots, drones) through their operators, dispatched by the same loop that
dispatches human drivers. See Part 1's "Autonomy partners" table (P1–P6)
for the item-by-item detail; the architectural decision (adapter layer,
not a separate app) is recorded there.

Sequencing:
- **Gate:** a signed autonomy partner with API access — this whole phase
  is a business-development outcome first. Until then the only active
  obligation is the design constraint: touch fleet/offer models in a
  courier-shaped way (P1's abstraction), not a human-driver-shaped way.
- **First buildable slice once a partner signs:** P1 + P2 with the stub
  partner, proving the offer→accept→status loop end-to-end before any
  real vehicle moves; then P3's eligibility filtering (a drone that gets
  offered a 40lb pallet is a bug, not a learning).
- **F9 (hybrid gig-fleet overflow dispatch)** doesn't need to wait on this
  phase's autonomy-partner gate — a human gig-fleet (Wise Systems' "DoorDash
  Dial" model) is a nearer-term test of the same P1/P2 courier abstraction
  and could ship independently, well before a signed AV/drone partner exists.
- **P4 (unmanned handoff/PIN)** is shared with the driver app's A4 —
  building A4 earlier quietly de-risks this phase.
- **Later:** P3's cost-per-drop mode selection (needs real partner
  pricing + the unit-economics numbers), P5 settlement, P6 portal.

**Exit criteria (long-horizon):** one real order, ingested from a real
client POS, delivered by a partner vehicle with no LMX human in the
loop — dispatched, tracked, PoD'd, and settled through the same pipeline
as every human-driven delivery that day.

---

## A note on sequencing

This order is a recommendation, not a fixed plan — it optimizes for "de-risk
what's already built before adding more," but the actual constraint is
almost always headcount (B1). Worth revisiting once that hire is in place,
since a team of two can run Phases 4–7 in parallel in a way one person
can't.
