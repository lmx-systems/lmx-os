# LMX OS — Full-System Roadmap

Two things in one document: (1) every open item across the whole system —
not just the driver app — in one place, and (2) a phased plan to take LMX
OS from "code that works in a demo" to "running Hub 1 for real."

This supersedes the "Recommended next steps" list at the bottom of
`docs/ARCHITECTURE.md` (still there for historical context) and sits above
`docs/NEXT_STEPS.md`'s row-by-row punch list — that file is the detailed
backlog; this one is the map of how those rows fit into getting to launch.

## Part 1 — Every open item, in one place

Most of this was already called out somewhere in
`docs/ARCHITECTURE.md`, `docs/NEXT_STEPS.md`, or `driver-app/README.md`
as it got built — this pulls it into one list instead of leaving it
scattered across three documents.

Three later additions are genuinely new rather than consolidated, and are
flagged as such where they appear: the **Competitive feature gaps**
(F-items, from benchmarking LMX OS against four industry platforms),
**Risk, compliance & real-world operations** (R-items, from deliberately
looking for what no existing doc mentioned at all), and **Operational
workflow gaps** (W-items, from the cofounder workflow review session,
July 2026). The R- and W-items matter most precisely because nothing in
the codebase or prior docs was tracking them.

> ### ⚠️ Read this first — the Hub 1 operating model changed
>
> The cofounder workflow review session (July 2026,
> `LMX_Workflow_Review_Session.docx`) establishes a materially different
> Hub 1 model than the one Part 2's phases were originally written
> against. **Hub 1 does not launch on LMX OS.** It launches on a
> scaffold — **Elite EXTRA** as the operating platform, with LMX vans
> and LMX W-2 drivers and human dispatch judgment — and LMX OS replaces
> that spine at a later cutover, per customer engagement, gated on a
> shadow-mode scorecard.
>
> Consequences already folded into this document: Phase 9 is rewritten
> (it previously assumed LMX OS ran Hub 1 directly), F14 is upgraded from
> a thin preview feature into the real shadow-mode comparison system, B2
> is reframed, and a new Phase 3.5 covers the scaffold-era integration
> work that nothing here previously tracked. **"Elite EXTRA" appears
> nowhere in the codebase** — that is the single largest unbuilt
> dependency this session surfaced.

### Business / org (not code, but gates what the code is for)

| # | Item | Why it matters |
|---|---|---|
| B1 | Hire the senior backend engineer | Peer review names this the critical path — "do not hire down." Nothing below scales past one person without this. |
| B2 | Sign customer #1 (workflow session D1) | **Reframed July 2026 — this is now the single gating item for the whole plan, not just a data unlock.** The workflow session records that there is no design partner: no signed customer means no orders, no shadow data, no revenue. Beyond the signature itself, three things are now contract terms rather than favors: co-location at the customer's warehouse, an Epicor warehouse/staging-module check (a sales-qualification question asked *before* signing — see W8), and **training-data rights** (model-training, cross-customer aggregation, and anonymization terms — see W7). Still unlocks real Epicor payload verification (E3) and the 2.5 DPH test (E9). |
| B3 | Get access to the Source of Truth Index (Google Drive, LMX OS Brief v1.0–v1.2) | The batch-hold "4-question decision logic" and SLA hold-window minutes were reconstructed from a peer-review summary, not this canonical doc, because it wasn't reachable while building. |
| B4 | ~~Choose a payroll provider~~ → **Decided: Rippling** (cofounder alignment, July 2026) | Drivers are W2 employees; the earnings screen is placeholder-only until Rippling is provisioned (account + API access) and a pay formula is agreed — see A9. |
| B5 | Provision a real Twilio account + phone number | Every SMS today (OTP codes, masked customer/support messaging) runs through a stub that logs instead of sending. |

### Operational workflow gaps

From the cofounder workflow review session (July 2026). That session
walked the order-to-delivery operation four ways and produced 39
persona-voiced user stories; cross-checking those stories against this
roadmap and the codebase surfaced seven whole workflows that **nothing
here or in the code was tracking**. These are not polish — several are
daily realities of the auto-parts trade that a distributor would notice
missing in week one.

| # | Item | Why it matters |
|---|---|---|
| W1 | Returns & core pickups as first-class work | Stories GS-7, CP-6, DR-9, exception `RETURNS_NOT_READY`, training case E6. Cores are, in the session's words, "half the economics of the parts trade." Today there is **no model, no endpoint, no route-stop type** for a pickup — the system can only deliver. Needs: pickup stops with an item manifest, per-shop pickup-readiness patterns, a counter-facing "awaiting pickup by shop, with age" list, and a reschedule workflow when a core isn't ready. |
| W2 | COD collection & payment disputes | Exception `COD_DISPUTE`, stories DO-8, training case E3. Nothing in code. The driver rule is unambiguous and must be enforced by the UI, not by training alone: *never negotiate, one tap escalates to the distributor, keep moving.* Needs a dispute flag, an escalation path to the distributor, and a repeat-dispute count per account feeding a monthly owner report. |
| W3 | SLA-breach invoice credits | Story DO-3: contractual credits when SLA thresholds are breached. C3's billing computes fees from delivered orders but has **no credit mechanism** — a breach costs nothing today. Needs the credit schedule as contract data, computed from order-level SLA outcomes, appearing as a line on the statement. Ties to F5 (rate tables) — same billing surface, build together. |
| W4 | Driver-visible scorecard | Story DR-10: the driver sees *the identical metrics and definitions* the orchestrator sees. Explicitly framed as a trust decision, not a feature — "a shared standard, not a camera pointed at me." Folds into I4/F7's analytics work; the requirement is that the driver view is the same computation, not a separate reduced one. |
| W5 | Counter-person order status lookup | Story CP-3: search any order by shop name or order number, get live status and ETA in ten seconds. The client portal today is distributor-owner-facing with one login per company (C4). The counter person is a **distinct persona** with a distinct need — and CP-4 (wrong-part flag reaching the counter mid-route) is a second counter-facing surface. Reopens C4's "one login per client" decision on real grounds rather than as an oversight. |
| W6 | Orchestrator-editable urgency configuration | Story OR-6: "body panels are never urgent" should not require a developer. Distinct from I2 (which promotes *machine-proposed* rules) — this is direct human authoring of part-type tier rules, editable without a code deploy. The `active_rules` table can likely carry it; the gap is the editing surface and validation. |
| W7 | Training-data rights in the customer contract | Session closing note: model-training rights, cross-customer aggregation rights, and anonymization terms "belong in customer #1's contract before the first delivery, not in a future amendment." Distinct from R3 (privacy policy — what LMX does with personal data); this is about the right to train models on a customer's operational data. **Legal work, gates B2, not engineering.** |
| W8 | Epicor staging-module qualification check | Session D6. Whether a prospect's Epicor runs a warehouse/staging module is now a sales-qualification checklist question asked before signing, because it determines whether real-time ingestion is even possible for that customer. Not code — a sales-process artifact that gates which prospects are viable. |

**Sequencing:** W1 and W2 are Phase 6/8 work and both need a day-one
written playbook before the first delivery regardless of whether the
software exists (session decision D5 names `WRONG_PART`, `COD_DISPUTE`,
`SHOP_CLOSED`, and `RETURNS_NOT_READY` as the four day-one playbooks —
note that all four are exactly the exceptions where LMX touches someone
else's money or customer). W3 joins C3/F5 as Phase 8 billing work. W4
folds into Phase 10's I4. W5 reopens C4 in Phase 8. W6 is small and
no-dependency. W7 and W8 are business/legal items gating B2 and should
be moving now.

### Risk, compliance & real-world operations

Surfaced July 2026 by deliberately looking *outside* the existing docs —
these were not in `ARCHITECTURE.md`, `NEXT_STEPS.md`, or this roadmap,
which is exactly why they're worth naming. Three are business/legal
items nobody would think to write code for; three are engineering gaps
that existed only as a passing comment in a source file and had never
been promoted to a tracked item.

**Why this section exists at all:** every other section here tracks work
someone already knew about. These are the ones that could quietly become
the actual Hub 1 blocker precisely because no one is watching them —
R1–R3 in particular are not things engineering can solve, and they gate
putting real drivers on real roads with real customer data.

| # | Item | Why it matters |
|---|---|---|
| R1 | Insurance & liability plan (commercial auto, cargo, general liability) | If a driver has an accident or a package is lost/damaged, what covers it? Not named anywhere in any doc despite being existential for a delivery company. A business decision like B4/B5, not an engineering task — but unlike those two, nothing in the system even hints it's missing. **Gates Phase 9** (real drivers, real roads). Needs Rich/Matan. |
| R2 | Driver background checks & MVR (motor-vehicle record) screening | The system tracks license and insurance *documents* with expiry dates (`driver_documents`) and blocks going online when one is expired — but nothing verifies the driver was safe to put behind the wheel in the first place. Document expiry is not a background check. **Gates Phase 9.** Needs Rich. |
| R3 | Privacy policy & data-handling/retention policy | LMX OS stores customer names, delivery addresses, and phone numbers (orders + shop SMS), plus driver PII. No document says what LMX does with any of it, how long it's kept, or how someone requests deletion. Real legal exposure the moment there are real clients and real drivers; also the first thing an enterprise client's security questionnaire asks about, alongside F10's SOC 2. **Gates Phase 9.** |
| R4 | Driver document upload pipeline | `app/models/driver_document.py`'s own comment: "No file-upload pipeline exists… `file_url` accepts whatever string the client sends." A driver could submit a fabricated URL as their license scan and the system would treat it as valid. Distinct from A3 (proof-of-delivery photos) — this is *onboarding compliance* evidence, and it's what makes R2 enforceable in software rather than on paper. |
| R5 | Failed-delivery / redelivery workflow | `Stop.status` has a `failed` value, but nothing handles what happens next: no redelivery attempt, no client notification, no billing adjustment, no defined resolution path. Every real delivery operation gets refused packages, wrong addresses, and closed shops — today those orders would sit in `failed` forever. Also the gap behind Locus's "failed-delivery disputes" and P6's partner-dispute surface. |
| R6 | Hub closure / holiday calendar | Nothing models a hub not operating. The Learning Loop's nightly scheduler (E7) and the optimizer both assume every active hub runs every day — the first holiday, weather closure, or planned shutdown will either misfire the nightly job or dispatch routes for a hub that isn't open. |

**Sequencing:** R1/R2/R3 are business/legal work that should start *now*
— they're slow (insurance quotes, policy drafting, screening-vendor
selection) and they gate Phase 9, so starting them when the pilot is
imminent is starting them too late. R4 fits Phase 6 alongside A3 (same
file-upload infrastructure, build once). R5 and R6 fit Phase 4 — both
are "the system assumes the happy path" gaps of exactly the kind that
phase exists to close, and both will surface immediately in a real pilot.

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

**Update, July 2026:** this abstraction now also covers gig-courier
human capacity (Uber/DoorDash-style), not just AV/drone/bot partners —
see P7 below. Sourabh's call: there's value in LMX being able to route
to whichever capacity is cheapest — its own fleet or a gig courier — as
a standing dispatch option, while unit economics and assumptions get
worked through, not just as a rare emergency valve. This reverses an
earlier same-day call to not build this at all (see the "Competitive
feature gaps" section's F9 history for the full back-and-forth) — worth
knowing it was a live debate, not a snap decision.

**Most of this is still not being built now.** The near-term rule it
imposes: when fleet/offer models get touched for other reasons,
generalize toward a "courier" abstraction rather than deepening the
human-driver coupling. P1–P6 (the AV/drone/bot side) remain gated on a
signed autonomy partner (a B-item when it becomes real). P7 (the
gig-courier side) is different — it doesn't need a signed AV partner,
gig-marketplace APIs exist today — but it does need real partner pricing
and the unit-economics numbers before it's built for real, not guessed.

| # | Item | Why it matters |
|---|---|---|
| P1 | Courier abstraction over the fleet model | Today's fleet state is human-shaped (`DriverCandidate`, phone/OTP auth, an implicit person behind every route). Generalize to a courier with a `provider_type` (human_lmx_driver \| autonomy_partner \| gig_courier), service area/geofence, speed profile, payload limits, and capability flags (can batch multi-stop? sidewalk-only? weather-sensitive?). Human LMX drivers become one provider type, not the type system. |
| P2 | Capacity-provider adapter layer | The supply-side mirror of `app/ingestion/adapters/`: one adapter per partner normalizing (a) capacity in — which vehicles/couriers are available, where, with what limits; (b) assignments out — a `RouteOffer` becomes an API call the partner's fleet manager (or gig-marketplace API) accepts/declines within the same TTL a driver gets; (c) status back — partner webhooks map to our stop-status transitions and PoD. Ships with a stub partner (same unconfigured→stub pattern as Twilio/Rippling) so the whole loop is testable before any real partner exists. |
| P3 | Mode-aware dispatch | The optimizer gains eligibility filtering (weight, distance, geofence, tier, weather) before candidate generation, and — later — cost-per-drop mode selection: choose the cheapest *eligible* mode per order, whether that's an autonomy partner or (per P7) a gig courier. Ties directly to the unit-economics work (cost per drop vs. price per drop). |
| P4 | Unmanned handoff + proof of delivery | Nobody walks into the shop when a bot arrives: shop SMS grows a "load the bot" flow (compartment id, load-confirmed ack), and the customer side needs PIN-unlock delivery — which is exactly the A4 PIN issuance/verification item already on the driver-app list, making A4 shared infrastructure rather than app polish. |
| P5 | Partner settlement | Per-delivery payout to the partner — a third money flow next to client billing (in) and driver payroll (out), structurally the same shape as `client_rates`: per-partner, per-mode rates, monthly statements. Reuses C3's statement machinery. Also the payout mechanism for P7's gig couriers. |
| P6 | Partner portal | A thin reporting surface (delivery history, settlement statements, failed-delivery disputes) like `client-portal/` — explicitly a *later* convenience, not the integration mechanism. Partners integrate through P2's API, full stop. |
| P7 | Gig-courier cost-optimized dispatch (reinstated F9) | A standing dispatch option — not just an SLA-emergency valve — where the optimizer can route an order to a gig-courier marketplace (Uber/DoorDash-style) when it's the cheaper *eligible* option, per P3. Client never sees or chooses this — same LMX brand, SLA, and billing either way. Three guardrails, non-negotiable: (1) every gig-courier-dispatched order is tagged distinctly in the data model so I1/I4's analytics and the Learning Loop don't treat it as LMX-optimizer ground truth; (2) a volume cap/threshold per hub so this stays an optimization, not a silent shift of capacity away from LMX's own fleet; (3) built against real gig-courier pricing data, not guessed rates — same discipline as P3's existing gating. Gated on unit-economics work, not on a signed AV partner. |

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
| F7 | Client- and ops-facing analytics dashboards | Reinforces I4 (already on the roadmap) — DPH, on-time %, driver leaderboards, cost-per-drop trend, SLA-breach history, CSV/BI export — but every competitor also exposes a *client-facing* cut of this (their own on-time rate, delivery volume) in the portal, which I4 doesn't currently scope. Directly serves the "market adoption" story for a distributor moving to per-drop pricing — it's the retention/upsell proof, not just an ops nicety. |
| F8 | White-label / multi-brand portal theming | Bringg, Onfleet (Enterprise tier), and Locus all offer a rebrandable client-facing surface. Relevant if LMX ever resells through a partner or franchise model — not urgent for Hub 1. |
| ~~F9~~ | ~~Hybrid gig-fleet overflow dispatch~~ → **Reinstated as P7** (Sourabh, July 2026 — see Autonomy Partners section) | Wise Systems' "DoorDash Dial" auto-routes overflow orders to third-party gig couriers by cost/rules. This item's history in one place, since it flipped twice in one day: (1) first flagged as an unresolved conflict against a companion analysis's "LMX is the fleet, not a Bringg competitor" stance; (2) decided **not** to build it, full stop; (3) revisited same-day and reinstated — with three guardrails (data tagging, a volume cap, real pricing before building) — as a *generalized cost-optimized dispatch option* rather than a narrow overflow valve, folded into P1–P3's courier abstraction as **P7**, gated on unit economics rather than dropped as a permanent no. |
| F10 | A real path to SOC 2 (or equivalent) certification | Every one of the four competitors leads their security page with SOC 2 Type II (plus ISO 27001, sometimes HIPAA/GDPR audits). Reinforces S6 (security review) and S2 (secrets management) — this raises their urgency from "good hygiene" to "the thing enterprise clients will ask for in a security questionnaire." A companion analysis adds a concrete trigger: enterprise dealer groups (the recommended anchor client type) ask for SOC 2 in diligence, and it's a multi-month audit — start readiness well before it's needed, not when it's blocking a deal. |
| F11 | SSO/SAML for ops and client logins | S1 built real per-user auth with roles, but not SSO — Bringg, Wise Systems, and Locus all support it for enterprise buyers. |
| F12 | Network/territory optimization tooling | Wise Systems' "Network Optimization" (depot/zone redesign, distinct from daily routing) — relevant once LMX runs multiple hubs, not for a single Hub 1 pilot. |
| F13 | Ratings & feedback capture | One-tap post-delivery rating (+ optional comment) prompt to the shop, landing on the order/stop record. Low effort, and it feeds the Learning Loop (I3's broader annotation vocabulary) with a ground-truth satisfaction signal none of the four researched competitors structurally capture the same way. No external dependency. |
| F14 | Orchestrator route-preview / shadow mode | **Substantially upgraded July 2026 — see W9 below.** Originally scoped as a view to preview the optimizer's proposed plan before it commits. The workflow session's decision D3 makes shadow mode far larger: the standard onboarding gate for *every* customer engagement, not a one-time pilot tool. The preview/override surface described here is still wanted, but it is now the small half of this item. |
| W9 | Shadow-mode comparison engine & cutover scorecard | The real shape of shadow mode per session decision D3: every initial customer engagement runs live on the Elite EXTRA scaffold while LMX OS **decides in parallel on the same orders**, and the two are compared until a scorecard passes and that engagement cuts over. Needs: a parallel decision path that records what LMX OS *would* have done without acting; per-order divergence capture (the session is explicit that aggregate metrics look fine while the two systems agree — the divergent orders are the entire point); and a nine-metric scorecard — drops per driver-hour, T1 on-time rate, batch rate, hold-release integrity, miles per drop, re-plan speed (<5s at real volume), human touches, decision divergence with outcome delta, and data completeness. Also a **sales asset**: "we transition only when our OS beats the baseline on your own orders." Open decisions for D3: the thresholds, the minimum consecutive passing weeks, and the weekly review owner. |

**Revisited — F9 vs. the operator-not-aggregator thesis:** a companion
competitive analysis run separately (`docs/LMX_OS_Roadmap_Addendum_Feature_Delta.docx`,
uploaded by Sourabh) explicitly lists "multi-carrier selection + carrier
network" under features LMX **deliberately does not build**, reasoning
that it's the aggregator model LMX rejected — "LMX is the fleet, not a
Bringg competitor." F9 as originally scoped (auto-routing overflow
orders to third-party gig couriers) was the same model in a narrower
form, and was first decided as **not building it** on that basis.

Sourabh revisited this same day: there's value in going this direction
while unit economics and assumptions get worked through, rather than
ruling it out permanently. The distinction that matters — this is about
LMX *sourcing capacity* from a gig marketplace while keeping its own
brand, SLA, and billing (the client never sees or chooses a carrier),
not about becoming a multi-carrier marketplace itself (a bigger,
separate question that would also reopen the "never sell LMX OS as
SaaS" decision, and would need Matan/Rich). On that narrower reading,
**reinstated as P7** in the Autonomy Partners section, generalized into
the same courier-abstraction work already planned for AV/drone/bot
partners, with three guardrails (data-flywheel tagging, a volume cap,
real pricing before building — see P7's row above) so a standing
cost-optimization option doesn't quietly become a bypass around
confronting LMX's own unit economics.

**Sequencing:** F1/F2 slot into Phase 6 (driver app hardening, alongside
A1/A5); F3 follows as a fast Phase 8 follow-up once F1 exists; F4/F5/F8
join C3/C4/C5 as Phase 8 follow-ups; F6 depends on E1 in Phase 4; F7
folds into Phase 10's I4; F10/F11 reinforce Phase 5; F12 is a later,
multi-hub-scale item. F13 (ratings/feedback) is low-effort and
no-dependency — a good Phase 8 follow-up alongside F3/F4. F14
(route-preview/shadow mode) fits Phase 9 (Hub 1 pilot) — it's the
trust-building surface for the pilot itself, not a pre-pilot gate.

**Deliberately not building (validated by the same companion analysis):**
checkout/delivery-slot self-scheduling (B2C retail surface — LMX orders
originate from distributor POS, matching the existing C5 "no self-serve
signup, by design" decision); a no-code automation/workflow builder and
a configurable driver toolbox (Bringg needs both because its customers
self-configure a shared platform; LMX runs one operation and the
Learning Loop already generates rules — configurability here would be
anti-differentiation, not a feature); and a manager mobile app (not a
gap that costs deals at hub scale — revisit post-Series A). Gig-fleet
capacity sourcing is **no longer on this list** — see P7 above; a true
multi-carrier marketplace (clients choosing between carriers) still is,
and would need a Matan/Rich conversation before it's reconsidered.

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
| I1 | Ground-truth event capture | The prerequisite for every stage above it. Concretely: `Order.delivered_at` (real timestamp, replacing the `updated_at` proxy billing/portal use today); `Stop.arrived_at` (so time-at-stop = arrived→completed becomes measurable, per shop); per-leg actual drive time vs. the optimizer's implied estimate; offer decline/expiry reasons; hold-queue release timing (held→released delta per order, vs. its window). All buildable now with no external dependency. **Superseded in scope, July 2026:** the workflow session's 36-item training coverage matrix is a far richer specification of exactly this item — organized as urgency/tiering (4 situations), hold-and-batch (5), fleet dynamics (4), exceptions (8), communication (3), human-vs-system (4), edge cases and economics (6), and the learning loop itself (2). Build I1 against that matrix, not against the five fields listed here. Two things in the matrix matter disproportionately: **negative examples** (a batch declined because pairing would breach a window; an insertion rejected to protect a driver — "when not to batch is half the skill") and **paired counterfactuals** (the same shop visited before and after an access note exists; the same order decided by LMX OS and by the scaffold). |
| I8 | Manual capture of the non-default training situations | The session's own operating note: roughly a third of the 36 matrix situations **are not captured by default** — the paired access-note comparison, the interim dispatcher's gut-call log, the scaffold-era "where's my part" call tally, and the shadow divergence pairs all require someone deciding in week one that they are worth writing down, on a shared sheet if no app exists yet. Two of these are only capturable *during* the scaffold era and are gone forever after cutover: the call tally (C2) and the human dispatcher's tacit expertise, right and wrong (H1/H2). This is an ops checklist assignment, not a build — but it is on the critical path for I6 and belongs to whoever runs Hub 1 operations. |
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

**The Hub 1 model changed in July 2026 — see the callout at the top of
Part 1.** Hub 1 launches on the Elite EXTRA scaffold, not on LMX OS. The
new Phase 3.5 below covers the scaffold-era work; Phase 9 has been
rewritten from "run Hub 1 on LMX OS" to "run Hub 1 on the scaffold,
shadow with LMX OS, cut over when the scorecard passes."

**These phases are not strictly sequential.** Once there's more than one
engineer (B1), 4/5/6/7 can mostly run in parallel — they touch different
parts of the system. Phase 8 (client dashboard, Hot Shot tier, tiered
billing, shop SMS, minimal client onboarding) has already shipped, ahead
of the sequencing below — Sourabh's call, since the first client wanted
these at MVP rather than deferred, and LMX had no committed dates
constraining the build order.

### Phase 3.5 — The Elite EXTRA scaffold (NEW, July 2026)
**Goal:** Hub 1 generates revenue on a scaffold while LMX OS is still
being built. This phase did not exist in earlier versions of this plan,
which assumed LMX OS ran Hub 1 from day one.

The scaffold, per the workflow session: **Elite EXTRA** as the operating
platform, LMX-owned vans, LMX W-2 drivers (recruited per signed customer,
hiring qualified incumbents from that customer's crew where possible),
co-located at the customer's warehouse, with human dispatch judgment in
place of the SLA engine and batch-hold queue.

- **The integration that does not exist yet:** "Elite EXTRA" appears
  nowhere in the codebase. At minimum LMX OS needs to read the same
  order stream and the same delivery outcomes the scaffold sees, or the
  shadow comparison (W9) has nothing to compare against. Scope this
  before committing to a cutover date.
- **Interim ingestion latency (session decision D6):** Epicor drops a
  file every 15 minutes into Elite EXTRA. Worst case an order sits 15
  minutes before anyone sees it — against a 45-minute T1 promise, that
  is a third of the window gone before dispatch. Three options on the
  table: tighten the drop interval, narrow the interim T1 promise, or
  accept the risk. **Unresolved — needs a decision.**
- W8 (Epicor staging-module qualification) gates which prospects this
  scaffold can even work for.
- I8's scaffold-era-only captures (the "where's my part" call tally, the
  human dispatcher's gut-call log) can **only** be collected during this
  phase. After cutover they are gone permanently.
- **Deliberately not built:** hold/batch logic inside Elite EXTRA. The
  session is explicit that paying Elite EXTRA to build hold logic would
  hand LMX's core differentiator to a competitor. The scaffold dispatches
  as fast as possible and lives with ~1.75 drops/driver-hour; the batching
  advantage arrives with LMX OS, not before.

**Exit criteria:** Hub 1 delivering real orders on the scaffold, with
enough instrumentation that Phase 9's shadow comparison has a baseline
to measure against.

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
- R5, R6 (failed-delivery/redelivery workflow, and a hub closure/holiday
  calendar — both are "the system assumes the happy path" gaps of
  exactly the kind this phase exists to close, and both surface
  immediately in a real pilot)

**Exit criteria:** no remaining "not yet verified against a live X" line
in `docs/ARCHITECTURE.md`'s core-backend sections, and no core workflow
that silently dead-ends when the happy path doesn't hold.

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
- R4 (driver document upload pipeline — build alongside A3, same
  file-upload infrastructure; this is what makes R2's background-check
  policy enforceable in software rather than on paper)
- W1, W2 (returns/core pickups as real route stops, and the COD-dispute
  escalation flow — both are driver-app surfaces and both need their
  day-one written playbook before the first delivery regardless of
  whether the software has shipped)
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
(flexible rate-table billing, alongside C3), F8 (white-label portal
theming, if LMX ever resells through a partner), and F13 (one-tap
ratings/feedback capture — low-effort, no dependency). And from the
workflow session: W3 (SLA-breach invoice credits — build alongside C3
and F5, same billing surface) and W5 (counter-person status lookup,
which reopens C4's one-login-per-client decision on real grounds).

### Phase 9 — Shadow, prove, cut over
**Goal:** earn the cutover from the scaffold to LMX OS on the customer's
own orders. **Rewritten July 2026** — this phase previously read "Hub 1
pilot: run real orders through the full pipeline," which assumed LMX OS
ran Hub 1 directly. Under the scaffold model, Hub 1 is already live and
generating revenue on Elite EXTRA (Phase 3.5); what this phase proves is
that LMX OS should replace it.

**Gates:**
- B2 (signed customer #1 — still the actual gate for everything)
- **R1, R2, R3 — the other gate, and the one most likely to be missed.**
  Insurance/liability coverage, driver background checks, and a
  privacy/data-retention policy all need to be *in place* before real
  drivers carry real packages containing real customer data — not started
  when launch is imminent. The workflow session independently reached the
  same conclusion (decision D4: fleet insurance and workers-comp lead
  times "now gate launch"). All three are slow, none are engineering
  work, and they need Rich/Matan moving in parallel with Phases 4–8.
- W7 (training-data rights in the contract) — the shadow comparison
  generates training data on a customer's orders at their customers'
  doors. Those rights belong in the contract before the first delivery.

**The work:**
- W9 (shadow-mode comparison engine + the nine-metric scorecard) — this
  is the phase's centerpiece, not a side feature
- F14 (route preview/override for the orchestrator during shadow running)
- E9 (validate or replace the 2.5 DPH figure — now measurable as
  *shadow-planned DPH vs. scaffold actual on identical orders*, which is
  a far stronger proof than a standalone pilot number)
- T1 (load-test against realistic volume before it's live volume — the
  scorecard's re-plan-speed metric asserts <5s at actual hub volume)
- I8's scaffold-era-only captures, before the window closes

**Cutover decision (session D3):** per customer engagement, not once
globally. Agree the thresholds, the minimum consecutive passing weeks,
and the weekly review owner — all three are still open.

**Exit criteria:** for the first engagement, a scorecard passing for the
agreed number of consecutive weeks on that customer's real orders, with
the divergent-order analysis (not just the aggregates) reviewed and
signed off — and insurance, background checks, and a privacy policy
(R1–R3) demonstrably in place before day one, not retrofitted after.

**Also worth running as its own goal (session D7):** "excellent on the
scaffold" defined in numbers — on-time rate, drops per hour, exception
rate — reviewed in the same weekly session as the shadow scorecard. The
scaffold funds the end-state; it shouldn't be treated as a holding
pattern.

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
  pass. W4 (the driver-visible scorecard) folds in here too — same
  computation, shown to the driver, which the workflow session frames as
  a trust decision rather than a feature.
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
bots, drones) — and, per P7, gig-courier human capacity — through their
operators, dispatched by the same loop that dispatches human drivers.
See Part 1's "Autonomy partners" table (P1–P7) for the item-by-item
detail; the architectural decision (adapter layer, not a separate app)
is recorded there.

Two independent tracks in this phase now, gated differently:

**Track A — AV/drone/sidewalk-bot partners (P1, P2, P4–P6):**
- **Gate:** a signed autonomy partner with API access — this track is a
  business-development outcome first. Until then the only active
  obligation is the design constraint: touch fleet/offer models in a
  courier-shaped way (P1's abstraction), not a human-driver-shaped way.
- **One exception to that gate, added July 2026:** the workflow session
  puts **autonomy-eligibility scoring on every delivery from van one**
  (Stage 9; training case X6 — weight, dimensions, corridor, and an
  eligible/ineligible label captured per drop). That is data capture,
  not integration, and it starts at Hub 1 launch rather than waiting on
  a signed partner. It builds the addressability dataset that makes the
  first partner conversation quantitative instead of speculative — so
  fold the fields into I1's capture work, not into this phase.
- **First buildable slice once a partner signs:** P1 + P2 with the stub
  partner, proving the offer→accept→status loop end-to-end before any
  real vehicle moves; then P3's eligibility filtering (a drone that gets
  offered a 40lb pallet is a bug, not a learning).
- **P4 (unmanned handoff/PIN)** is shared with the driver app's A4 —
  building A4 earlier quietly de-risks this phase.
- **Later:** P5 settlement, P6 portal.

**Track B — Gig-courier cost-optimized dispatch (P7, reinstated F9):**
- **Gate:** unit economics and real gig-marketplace pricing data — not a
  signed partner. This can move independently of, and likely faster
  than, Track A.
- **Sourabh's call (July 2026):** worth pursuing as a standing
  cost-optimization option, not just an SLA-emergency valve, while the
  economics get worked through — reversing a same-day earlier decision
  not to build it at all. See the "Competitive feature gaps" section's
  F9 history for the full reasoning on both sides.
- **Before writing real dispatch logic:** P7's three guardrails (data
  tagging so I1/I4 don't treat these as LMX-optimizer ground truth, a
  volume cap per hub, and real pricing data rather than guessed rates)
  are not optional polish — they're what keeps this from quietly
  becoming a bypass around confronting LMX's own unit economics.
- **Shares P3's cost-per-drop mode-selection logic and P5's settlement
  machinery** with Track A — one dispatch decision, one settlement
  system, two kinds of capacity provider.

**Exit criteria (long-horizon), Track A:** one real order, ingested from
a real client POS, delivered by a partner vehicle with no LMX human in
the loop — dispatched, tracked, PoD'd, and settled through the same
pipeline as every human-driven delivery that day.

**Exit criteria, Track B:** the optimizer routes a real order to a gig
courier because it was the genuinely cheaper eligible option under real
pricing data — not a guess — with the order correctly tagged so it never
contaminates I1/I4's ground-truth analytics, and the hub's volume cap
holding.

---

## A note on sequencing

This order is a recommendation, not a fixed plan — it optimizes for "de-risk
what's already built before adding more," but the actual constraint is
almost always headcount (B1). Worth revisiting once that hire is in place,
since a team of two can run Phases 4–7 in parallel in a way one person
can't.
