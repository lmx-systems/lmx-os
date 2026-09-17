# LMX 1.5 — Build Roadmap

**v1.1 · 13 September 2026 · Sourabh**

Source of truth for strategy: *LMX 1.0 vs 1.5* (13 Sep 2026).
Source of truth for build status: **this repository, inspected 13 Sep 2026** — 174 commits, 94 test files, 291 test functions, last commit 31 Aug.

Companion docs: `docs/ROADMAP.md` (the 1.0 roadmap, F/W/A/E item IDs — still the reference for anything not superseded here) · **`docs/MODEL_AND_DATA_BRIEF.md`** (§2.1 and §2.3 depend on it) · `docs/ROADMAP_RECONCILIATION.md` · `docs/ARCHITECTURE.md` · `docs/NEXT_STEPS.md`.

---

## 0. Inventory correction

The earlier version of this roadmap was written from a summary that materially understated what exists. Corrected against the code:

| Claimed | Actually in the repo |
|---|---|
| Driver app "never built" | **`driver-app/` — 52 source files**, Expo/React Native. Auth, biometric lock, offline outbox with optimistic apply, POD capture (photo, signature, barcode), COD panel, realtime route events over SSE, 16 screens |
| No shadow mode | **`app/shadow/recorder.py`** + `shadow_decision` model |
| No returns, "no pickup stop type at all" | **`app/returns/service.py`** + `return_item` model (thin — 57 LOC — but present) |
| Learning loop "doesn't learn yet" | **`app/learning_loop/`** — 475 LOC across detection, promotion, scheduler, service |
| 29 unit + 9 integration tests | **291 test functions across 94 files** |

Also present and not previously accounted for: `batch_queue/` · `sla/` · `fleet_state/` · `optimizer/` (with a Google Routes client and an event trigger) · `ingestion/` (978 LOC, adapter registry and router) · `billing/` · `gig_platform/` · `tracking/` · `reporting/` · `compliance/` · `payroll/`.

**What this changes.** Phase 1 is smaller and more specific than "build the instrument." Most of the instrument exists; it is pointed at the wrong job.

**Status key used throughout:** `BUILT` exists and appears fit for purpose · `RESHAPE` exists but is the wrong shape for 1.5 · `NEW` does not exist.

---

## 1. The governing rule

> Nothing gets built that does not close a gate or produce a measurement.

Of the eight open gaps in the source-of-truth document, **two are engineering and both are the same engineering**. The rest close with phone calls and a contract clause. Building ahead of that is how this repo reached 174 commits and 291 tests without ever processing a real order.

| Gap | Closes with | Engineering? |
|---|---|---|
| 1. A price has never been quoted | A call. Days. | No |
| 2. No control-arm measurement | One design partner, one quarter | **Partly** |
| 3. No second customer | The first order that isn't ours | No |
| 4. Supply side never asked for a number | One call | No |
| 5. False-positive rate unmeasured | Requires #2 running | **Partly** |
| 6. Data-rights clause does not exist | Days, with counsel | No |
| 7. Vertical two not started | Desk research | No |
| 8. The $6M never re-costed | Spreadsheet | No |

### 1.1 The two lenses (agreed by all three, 13 September 2026)

Richard asked for this on the record and both co-founders agreed. **There are two different answers to "what is LMX selling," and which one is true depends on who is asking.** Every feature below serves one lens or the other, and they do not share a deadline.

| | **Investor lens** | **Go-to-market lens** |
|---|---|---|
| What we are selling | The autonomy-integration and eligibility dataset that does not exist anywhere today | Batching and commingling — a cheaper delivery than the one they buy now |
| Said as | *"we're building this autonomy integration data set, eligibility — and by the way, DA2, we're the experts"* | *"What's the number one thing I need in order to get a sale? It's just batching and commingling"* |
| Told to the customer? | No | **Also no.** Matan: *"This is not something you tell the customer."* The customer hears one promise: it will be cheaper |
| Features it depends on | `IDN-1..4` identity · the dock survey · M5 modality fit · the payload dataset · `PRD-8` | Ingest · hold · batch · route · POD · commingle. **Hold, within-customer batching and POD are `BUILT`; ingest and the optimiser call are `RESHAPE`; cross-customer commingling is `TEN-4`, `NEW` and gated on the 0.2 data clause** |
| Earliest proof | Phase 4–5 | Phase 2, against the control arm (`EXP-1`). A modelled number is available from the order book on day one; a defensible one is not |

**Why this belongs in a build document.** The GTM lens is satisfied almost entirely by code that is already written; the investor lens is satisfied by almost none of it. That asymmetry is the reason Phase 1 is a measurement instrument rather than a feature release, and it is the reason a demo that wins a customer will not move an investor and vice versa. **When a feature is proposed, name its lens.** If it serves neither, it does not get built. Naming a lens is necessary, not sufficient — §1 still applies, and §6 forbids several things that would pass the lens test comfortably.

> **The trap this is here to prevent.** The two lenses make it tempting to build two products. We are not. One system produces both: the batching that sells the deal is what generates the eligibility data that sells the round. If a proposed feature serves the investor lens *without* riding on a revenue stop, it is a research project and we cannot afford it. **One exception: `PRD-8`.** It rides on no revenue stop and is a pure offline evaluation — it survives because §1's second clause covers it. It *is* the measurement.

---

## 2. Decisions that block the build

| Decision | Owner | Why it blocks | Landing |
|---|---|---|---|
| **Fee unit** — per allocation vs per order ingested | Matan | It is the metering primitive: billing event, idempotency key, what the decision log counts | **Per order ingested.** "Per allocation" reads as per-trip, re-introducing the exact incentive the model exists to remove |
| **Model or engine, one or many** | **Sourabh** | Decides the shape of `app/learning_loop/` and any new `predict/` | ✅ **DECIDED 13 Sep 2026 — see §2.1** |
| **Outcome liability** | Rich | If yes, adds an SLA-breach ledger and reserve calculation to Phase 3 | Do not build speculatively either way |
| **Background location** | Sourabh + Matan | iOS "always" tier needs an App Store justification and a real developer account (`docs/ROADMAP.md` A6). **Phase 1 is blocked on this** | Decide in week 1 — see DRV-2 |
| **Licence floor** | Matan | Decides whether `ING-2` meters one revenue event or two, and whether design partners have a contract at all | ⚠️ **REOPENED — see §2.3** |
| **Savings share** | Matan + Rich | If live, Phase 3 needs a per-customer counterfactual ledger and a dispute path. That is weeks of work nobody has scheduled | ⚠️ **REOPENED — see §2.3** |

### 2.1 Decision — model shape (Sourabh, 13 September 2026)

> **Small, domain-specific models behind one shared solver, across industries.**

**What it means concretely.** Each predictor — dwell, true urgency, batch value, modality fit — is its own model, trained per vertical and, where the data supports it, per customer. One solver consumes all of them and is the same engine in every vertical. It is bought, not built.

**What transfers between verticals is the schema, the collection method and the solver. Not the model weights.**

**What it commits us to**

- `app/learning_loop/` is repointed at a set of per-domain predictors rather than growing into one cross-vertical model.
- No shared embedding space and no single foundation model. If either is proposed, cite this decision.
- **CatBoost is vetoed** (Matan, 13 Sep — Yandex origin). M1's library is **LightGBM**, which is what `lmx-dwell/` already runs — not the XGBoost named on the call. XGBoost lacks the ordered target-statistic encoding that was the reason to want CatBoost at all. See `MODEL_AND_DATA_BRIEF.md` §3 for what the veto does and does not cost.
- `PRD-8` changes job. It was cross-*tenant* transfer evaluation; it is now **the evidence for the compounding claim**, and must measure transfer both across customers and across verticals, reporting either way.

**The honest reading, which should be said the same way externally.** "It compounds across verticals" is a claim about the schema and the collection method, not about model transfer. An investor will hear the second. Say which one we mean, and let `PRD-8` be the thing that settles it rather than an assertion.

**Why this rather than one model.** At the volumes in play — tens of thousands of rows per customer — small tabular models train in seconds and are beaten by a per-receiver lookup table often enough that the baseline has to be a release gate (see `PRD-3`). A single cross-vertical model would be harder to evaluate, harder to explain to a customer disputing a savings statement, and would make the compounding claim untestable rather than true.

### 2.2 Decisions — build shape (Sourabh, 13 September 2026)

Four calls taken together. Each one changes code in Phase 1.

**(a) Driver app location: geofence-only, no breadcrumb.**
Register a geofence per stop and record enter/exit in the background. **No continuous position trail for measurement purposes.** The existing 30-second position pings (`F1`) stay exactly as they are, foreground-only, for the ops map — they are not the measurement and must not be confused for it.

The reasoning is that we need the two edges of a stop, not the path between them. It is also the far easier consent ask when the drivers belong to the customer: *"we record when you arrive at and leave a delivery address, not where you are in between."*

> **Two things this does not get us out of.** Background geofencing on iOS still requires the "always" authorisation tier and an App Store justification — `A6` and Phase 0.8 remain open, this decision just makes them a much smaller ask. And **iOS monitors a maximum of 20 geofence regions per app**, while the design partner's best driver runs 20.2 stops per route. `DRV-1` therefore needs **rolling registration** — hold the next N stops only, re-register as the route advances. Design for it from the first commit; retrofitting it means re-testing the whole sensor.

**(b) Identity: a new `Location` table, with `Shop` pointing at it.**
`app/identity/` owns `Location`. `Shop` gains a nullable `location_id` foreign key, backfilled. The receiver profile store (`IDN-4`) hangs off `Location`, never off `Shop`.

This keeps the distinction the data needs: a **Shop is a customer account**, a **Location is a physical dock**, and the whole entity-resolution problem exists because those two have been the same field. The existing `geocoded_address` dedup from the 6–7 August decision feeds Location resolution rather than being replaced by it. Nothing in the optimizer, clustering or HOT_SHOT commingling paths changes.

**(c) Merging: human-confirms the first pass, automatic after.**
Every merge in the founding ~230 is proposed and confirmed by a person in one sitting. From then on, auto-merge on normalised address with a review queue for conflicts only. Every merge — automatic or confirmed — writes an audit record and is reversible.

The founding state of the reference table is the moat's initial condition, and the failure mode is silent: a bad merge is invisible afterwards. One afternoon of attention buys a verified starting point. Auto-merge thereafter is what keeps `ONB-3` time-to-first-order honest.

**(d) Metering key is pluggable, pending the fee unit.**
`ING-2` builds with the billing event key as configuration rather than a hardcoded assumption, so it is not blocked on Phase 0.5.

> **Recorded exception.** This breaks *no abstraction without two live callers* — there is one caller. It is taken deliberately, to avoid blocking a week of work on someone else's decision, and it is the only exception currently on the books. **Collapse it to a constant once 0.5 lands.** If it is still an abstraction three months from now, that is drift, not design.

### 2.3 Pricing — what this document said, and what the founders last actually said

Read against the sync transcripts, two lines in this roadmap were recorded as settled and are not. Correcting the record rather than the position, because the position is Matan's and Rich's to take.

| Line | Recorded (this doc, unless noted) | Last said on a call | Verdict |
|---|---|---|---|
| Fee unit | $1.50–$3.00 per order ingested | *"three makes sense per drop, four is aspirational"* (Matan, 13 Sep) | ⚠️ **Agrees on the unit, not the range.** The recorded floor is half the quoted target and $4 sits above the recorded ceiling. Move the floor to $3.00 |
| Carrier cost | Passed through at cost | *"you charged Alan 10 bucks and you're paying 8 bucks to Uber. You didn't make eight bucks. You just transferred eight bucks"* (Matan, 13 Sep) | ✅ **Agrees**, and the accounting is right |
| Licence | Design partners pay **no licence** | *"License is the floor. Fulfillment is the wedge"* (Matan, 10 Sep). Sized at ~$130–150/month, replacing the dispatcher plus the incumbent tool | ❌ **Conflicts** |
| Savings share | Retired | Killed 10 Sep, killed again 11 Sep — then **reopened by Matan himself on 13 Sep**: *"of $10 saved… I think 30%."* Rich: *"20%."* | ❌ **Conflicts. Recorded as closed in two documents and it is not** |

**Why this blocks code and not just a slide.** A licence is a recurring charge with no per-order key, so it does *not* touch `ING-2` — it lands in settlement (`STL-3`) as a second billing event type, on a monthly cycle, with proration and cancellation. Small, but currently unbuilt and unscheduled.

Savings share is the expensive one. It requires a defensible counterfactual for **every** order — what the delivery would have cost — held per customer, reconcilable at month end, and survivable in a dispute. Phase 2 already carries `EXP-1` (the control arm), `STL-1` (savings statement) and `STL-2` (baseline definition and change control), which together give a **sampled** counterfactual and a statement. A savings-share *ledger* is a different object: per-order, not sampled, and adversarial by design because the customer is arguing about their own invoice. That is not costed anywhere.

**The recommendation, for what it is worth.** Take the licence. Leave savings share dead. Matan's own argument against it on 10 September is the strongest one anybody made: *"we performed so much better — and then [the customer] is going to say, what are you talking about? It's us that performed so much better."* A saving you have to prove against a counterfactual the customer disputes is a collections problem disguised as a revenue line, and it re-introduces exactly the incentive the fixed fee exists to remove.

**Until this is settled, treat §2.2(d) as still live.** The metering key stays pluggable; it does not collapse to a constant on 0.5 alone. If the licence lands, `ING-2`'s Done-when needs one word — "one record and one billable **ingest** event" — so that the subscription charge in `STL-3` is not read as a second ingest event.

---

## Phase 0 — Unblock (weeks 0–3, no code)

| # | Item | Owner | Closes |
|---|---|---|---|
| 0.1 | Quote a price to a named buyer | Matan / Rich | Gap 1 |
| 0.2 | Data-rights and pooling-consent clause | Rich + counsel | Gap 6 · **gates Phase 4 entirely** |
| 0.3 | Ask the supply side what the weight-and-dock join is worth | Rich | Gap 4 |
| 0.4 | Driver-app access and background-location consent negotiated into pilot terms | Matan | **All DRV items depend on this** — the drivers belong to the customer |
| 0.5 | **Fee unit** — per order ingested | Matan | **Open** — decided in §2, but §2.3 reopens the licence alongside it |
| 0.6 | **Model shape** — small specific models, one shared solver | Sourabh | ✅ **Decided 13 Sep** (§2.1) |
| 0.7 | **Outcome liability** | Rich | **Open** — blocks `DEC-1` credit percentages |
| 0.8 | **Background location** — the "always" tier and App Store justification | Sourabh + Matan | **Open**, but narrowed by §2.2(a) |

**Kill criterion:** if nobody will be quoted a price in three weeks, the problem is not the product.

---

## Phase 1 — The instrument (weeks 1–8)

**Goal:** measure a delivery truthfully.
**Why it matters:** the design partner's dispatch export is minute-resolution, so **65.5% of stops compute to zero dwell**. The one second-precision export shows those same stops really took 3–50 seconds. No filter recovers that — only a new sensor does.

### driver-app/ — the reshape, not a rebuild

The app exists and is good. Its location layer is pointed at the wrong job: `src/location/reportDriverLocation.ts` emits **position pings every 30s with a 25m floor, foreground only**, with `isIosBackgroundLocationEnabled: false` and `isAndroidBackgroundLocationEnabled: false`. That is correct for "show the driver on the ops map" and useless for "when exactly did this stop start and end."

| ID | Feature | Status | Done when |
|---|---|---|---|
| **DRV-1** | Geofence arrive/depart per stop — `startGeofencingAsync` + TaskManager, emitting **stop events**, not position. **Rolling registration**, iOS caps at 20 regions (§2.2a) | `NEW` | Arrive and depart recorded to the second with no driver tap, on a route of 25+ stops |
| **DRV-2** | Background geofencing enabled, consent copy rewritten to the §2.2(a) wording. **No background breadcrumb** | `RESHAPE` | Geofence events continue when backgrounded; position pings stay foreground-only. **Blocked on 0.4 and 0.8** |
| **DRV-3** | Warehouse geofence | `NEW` | Turnaround measured per return trip |
| **DRV-4** | Stop-event outbox | `RESHAPE` | Reuse `src/offline/` — a full shift with no signal loses no stop events. Location pings are currently fire-and-forget and dropped silently |
| **DRV-5** | Battery and permission degradation | `RESHAPE` | Under 4% battery per 8-hour shift with background on; clear state when permission is denied |
| **DRV-6** | Exception capture at the stop | `BUILT` | `FlagIssueScreen` exists — verify reason codes match `REC-3` and that it is under 3 seconds |

> **DRV-1 and DRV-2 are the whole of Phase 1's risk.** Everything else in this phase is plumbing.

### identity/ — new module

| ID | Feature | Status | Done when |
|---|---|---|---|
| **IDN-1** | `Location` table in `app/identity/`, one row per physical dock; `Shop.location_id` FK, backfilled (§2.2b) | `BUILT` | `app/identity/resolution.py`, merged. Placeholder addresses (`N/A`) are refused rather than normalised into one fictional dock |
| **IDN-2** | Alias map + merge review queue. **Human-confirms the founding ~230, auto-merge after, every merge audited and reversible** (§2.2c) | `BUILT` | `app/identity/merge.py`, merged. Built on UDID root/branch rather than coordinates, which the export does not carry; requiring two independent signals cut 192 candidates to 78 |
| **IDN-3** | Node-class labelling (shop / parts store / dealer / body shop / warehouse / transfer / municipal) | `BUILT, DONE-WHEN UNMET` | `app/identity/node_class.py`, merged. **36.2% of the 229 receivers are still `unknown` against a target of under 2%**, and account names alone will not close it — the export carries no industry code. Needs a decision: a person labels the tail, or the target moves |
| **IDN-4** | Receiver profile store | `BUILT` | `app/identity/profile.py`, merged. Geofence evidence is preferred over driver taps per dock |

> Start here — it is days of work. The design partner's export has **230 customer IDs → 224 names → an unknown smaller number of physical docks**: five records for one body shop across two ID roots, one shop twice with its city spelled with zeros for O's, one record with city/state/zip literally `N/A`. Until identity holds still every per-dock number is wrong, and it has already put a withdrawn figure in investor materials.

### ingest/ — hardening

| ID | Feature | Status | Done when |
|---|---|---|---|
| **ING-1** | ERP webhook field-map verification against live orders | `RESHAPE` | `app/ingestion/` is 978 LOC with an adapter registry; the field mapping is unverified. Unknown fields fail loudly rather than silently null |
| **ING-2** | Idempotent intake, **billing key pluggable** pending 0.5 (§2.2d) | `RESHAPE` | A replayed order produces one record and one billable event, under either fee unit |
| **ING-3** | Replay and backfill | `NEW` | History re-ingests without double-counting or mutating decisions |
| **ING-4** | Historical export loaders | `BUILT` | `lmx-dwell/` already parses both dispatch exports — promote it into `app/ingestion/adapters/` |

### record/ — the discipline layer

| ID | Feature | Status | Done when |
|---|---|---|---|
| **REC-1** | Immutable decision log — feature snapshot at decision time | `BUILT` | `app/record/decisions.py`, merged (#48). Append-only at the database; a replay that recomputes a different input hash raises rather than returning a plausible answer |
| **REC-2** | Execution trace — geofence, warehouse, exceptions, actual cost | `RESHAPE` | `driver_location_ping` and `driver_shift_event` exist; stop-level arrive/depart does not |
| **REC-3** | Outcome ledger | `BUILT` | `app/record/outcomes.py`, merged (#48). Values are copied, not referenced, so a later edit cannot move a replay. Supersede requires a reason |
| **REC-4** | Linkage flag engine — open return on the same part, duplicate order across branches, repeat visit today | `BUILT` | `app/record/linkage.py`, merged (#48). All three detectors |
| **REC-5** | **Data dictionary** — dwell, stop, order, route defined in writing | `NEW` | One page, signed by all three founders. Three sources currently report three different stop counts for the same window |

---

## Phase 2 — Shadow and measure (weeks 6–16)

**Goal:** a measured cost-per-drop delta with a control arm. Converts the central claim from modelled to observed.

| ID | Feature | Status | Done when |
|---|---|---|---|
| **DEC-0** | Shadow-mode runner | `BUILT, OFF` | `app/shadow/scheduler.py` runs the cycle per hub on a cadence, `app/shadow/divergence.py` computes the delta, `scripts/shadow_day.py` reports a window. **Off until `shadow_scheduler_enabled` is set** — every cycle is a solver call and a live Google project bills for it. The report measures the cycle interval actually achieved and states how much of the dispatch lead the cadence alone explains. Cost-per-drop and on-time deltas are refused by design: the shadow plan was never driven, so `EXP-1` is the measurement |
| **EXP-0** | Historical baseline from the customer's own prior exports | `BUILT` | `app/baseline/` + `scripts/analyze_baseline.py`, merged. Reduces a driver-activity and a stop-invoice export to drops per driver-hour, miles per drop, on-time rate, batch rate, cost per billed stop and a two-file reconciliation. **Strictly weaker than `EXP-1` and superseded by it** — a historical comparison is confounded by season, mix and volume, so this sizes a prospect and seeds `STL-2`; it is not the counterfactual a savings statement rests on |
| **EXP-1** | Control-arm randomiser — 5–10% dispatched as the customer would have | `BUILT, OFF` | `app/experiment/arms.py`, merged (#50). Assigned at intake, immutable at the database, and **off until a client has a recorded contract date** — the gate is a date rather than a boolean so turning it on requires stating when the customer agreed. No client is enrolled. Not yet wired into intake, because nothing would assign until one is |
| **EXP-2** | Exploration policy and per-receiver caps | `BUILT` | `app/experiment/arms.py` + `exclusions.py`, migration `0055`. Assignment is a **permuted block per dock** — within each run of `round(1/fraction)` orders to one dock exactly one is control, so the share is arithmetic rather than an average. A quota-and-force-treatment cap was rejected: the orders it moves are the ones that came after a dock was already unlucky, which biases the control group against the busiest docks. Exclusions carry a required reason, keep their row when revoked, and `exclusion_impact` states the share of volume the measurement never covered — `STL-1` should print it |
| **EXP-3** | Arm integrity monitor | `NEW` | A skewed or contaminated arm alerts before a statement is generated |
| **PRD-1** | Batch value by node class | `BUILT, BLOCKED ON DATA` | `ml/prd/batch_value.py`, merged (#50). **The done-when cannot be met from the export we hold**: it contains one warehouse receiver and no transfer nodes, and the file's own `Transfer` flag is false on all 6,715 rows. The shop end measures +22% to +40% across three readings of "buys +X%", not +3–7%; among classes with 5+ docks the whole spread is 1.5×, not 40×. Needs either a customer with warehouse/transfer volume or a traced source for the stated figures |
| **PRD-2** | Trip cost versus order value | `BUILT` | `ml/prd/trip_cost.py`, merged (#50). On the export, 920 billed stops: at $45/hr 12.3% lose money and 43 are the cheap-part-long-trip class. Time only — there is no distance column anywhere in the export — so every figure is a lower bound. The driver rate is a required argument with no default |
| **PRD-3** | Shrinkage baseline library | `BUILT, PROMOTED` | `ml/m1/baseline.py`, merged (#50) — out of `lmx-dwell/` and onto the standard library. Beats a single global quantile on both warm and cold populations, so the claim reproduces |
| **PRD-4** | Evaluation harness — time split plus held-out receivers | `BUILT, PROMOTED` | `ml/m1/evaluate.py`, merged (#50). Warm and cold scored apart; receivers held out by hash so the same dock lands on the same side next month. **Caveat that governs every dwell number**: the only second-precision file is one driver's 54 manifests, so no driver effect can be separated and there is no held-out-driver split |
| **STL-1** | Savings statement generator | `NEW` | A customer can read it without a call |
| **STL-2** | Baseline definition and change control | `NEW` | Baseline changes need sign-off from both sides and are versioned |
| **STL-3** | Metering and invoice export on the fee unit | `RESHAPE` | `app/billing/` exists. Billable events reconcile to ingested orders, to the unit |

---

## Phase 3 — Live authority (weeks 12–26)

**Goal:** LMX makes the call on real orders.
**Know the ceiling:** **50.2% of orders already get in-flight insertion by hand**, and those reach customers *faster* (34-minute median vs 40). The differentiator is doing it consistently at volume without one irreplaceable dispatcher.

| ID | Feature | Status | Done when |
|---|---|---|---|
| **DEC-1** | SLA engine live | `BUILT` | `app/sla/` — engine + commitment, 265 LOC. Zero top-tier breaches attributable to holding |
| **DEC-2** | Batch-hold queue live | `BUILT` | `app/batch_queue/` — queue, clustering, store, 350 LOC. Measured stops-per-route improvement vs the control arm |
| **DEC-3** | Optimiser against a live Google project | `RESHAPE` | `app/optimizer/google_routes_client.py` exists and has never been called against a live project (`docs/ROADMAP.md` E1). p95 solve under 5s |
| **DEC-4** | In-flight insertion | `RESHAPE` | `app/optimizer/event_trigger.py` is the hook. Matches or beats the human dispatcher's 34-minute median |
| **DEC-5** | Fallback router | `NEW` | A solver outage produces a usable plan, not an outage |
| **DEC-6** | Fleet state manager | `BUILT` | `app/fleet_state/` — verify it never assigns to an off-shift or full driver |
| **CON-1** | Dispatcher live board | `RESHAPE` | `dashboard/` exists. A working dispatcher can run a day on it |
| **CON-2** | Override with mandatory reason code | `NEW` | No override completes without a reason |
| **CON-3** | Override → training data | `NEW` | Every override lands in the decision log as a labelled example |
| **CON-4** | Exception queue | `RESHAPE` | Exceptions surface before the customer calls |
| **PRD-5** | Dwell predictor, p50 and p90 | `NEW` | **Beats PRD-3 on both familiar and unseen docks, or it does not ship** |
| **PRD-6** | Conformal intervals on the promise | `BUILT` | `ml/m1/conformal.py`, merged (#50). Split conformal, distribution-free. It tightens the warm p90 from 14.3 to 12.3 min and holds at 89.8%; calibrated correctly for cold start it widens to **23.2 min for a stop whose median is two** — kept 100% of the time and not sellable, which is what too little cold-start data looks like. It refuses rather than clamps below 9 calibration residuals |
| **PRD-7** | Censored-dwell handling | `NEW` | Abandoned stops stop biasing the worst docks downward |
| **NTF-1** | Receiver notification throttle | `BUILT` | `app/messaging/` — one update only when ETA moves >10 minutes |

> Two findings to carry in. Median dwell at the design partner is **2.1 minutes** with a third of stops under 60 seconds, against ~13 minutes at the national distributor — not the same operation, and a pooled model is wrong about both. And on the test harness the **shrinkage baseline beat gradient boosting**, with the p90 model badly under-covering on unseen docks. That is the failure that breaks SLA promises.

---

## Phase 4 — Cross-company (weeks 20–36)

**Gated on 0.2. Does not start without the signed clause.**

| ID | Feature | Status | Done when |
|---|---|---|---|
| **TEN-1** | Three-level tenancy — source → customer → location | `RESHAPE` | `client`/`client_user` models exist. Every read and write carries a tenant scope; none can be omitted |
| **TEN-2** | Pooling consent model | `NEW` | A customer can name who they will and will not be pooled with |
| **TEN-3** | Leak tests as a blocking CI gate | `NEW` | A cross-tenant read fails the build, not review |
| **TEN-4** | Cross-tenant batching under consent | `NEW` | Two consenting customers ride one route with correct cost attribution |
| **TEN-5** | Per-tenant audit log and export | `NEW` | A customer can see and take everything recorded about them |
| **ONB-1** | Self-serve feed onboarding | `RESHAPE` | The ingestion adapter registry is the foundation. A new order stream live in days |
| **ONB-2** | Field-mapping with schema inference | `NEW` | A new export maps without an engineer |
| **ONB-3** | Onboarding telemetry | `NEW` | Time-to-first-order per customer on a dashboard — **the real KPI of this phase** |
| **PRD-8** | Cross-tenant transfer evaluation | `NEW` | A model trained on customer A measured on customer B, reported either way |

> The decisive risk is the **leak asymmetry** — one order visible to the wrong tenant is unrecoverable, and the published evidence (Sternberg et al., JSCM 58(4) 2022, four countries) says these arrangements fail through participant defection, not technology.

---

## Phase 5 — Supply side (weeks 30+)

| ID | Feature | Status | Done when |
|---|---|---|---|
| **SUP-1** | Partner abstraction layer | `RESHAPE` | `app/gig_platform/` is the seed. Two live providers, no provider-specific code above the layer |
| **SUP-2** | Quote and pass-through accounting | `NEW` | Carrier cost passes through at cost, provably, on the invoice |
| **SUP-3** | Modality eligibility engine | `NEW` | Per order, not per category |
| **SUP-4** | **Payload-eligibility dataset** | `NEW` | The weight-and-dock join a drone operator asked for and could get nowhere. **55% of orders by count but only 31% of revenue** fall under 2.5 kg. Possibly sellable on its own |
| **SUP-5** | Autonomy operator adapter | `NEW` | One operator quoting real volume |

---

## 6. Not building

All Bringg-parity, none closes a gate: branded tracking pages · customer self-rescheduling · ratings and CSAT · tipping · a no-code rules engine · a BI widget catalogue · rate-shopping UI · delivery-slot checkout promise · driver scheduling and shifts · vehicle inspection forms · ID and age verification.

**Returns — revisit after Phase 3, do not extend now.** `app/returns/service.py` exists at 57 LOC. It is genuine white space (the category leader has no reverse-logistics product at all, and the field logs are full of returns failure — an $82 core part missed on two visits and never collected, three returns written off in one day with no reschedule). It is also not on the path to a measured number.

---

## 7. Module map

```
app/
  ingestion/      ING-1..4   RESHAPE  adapter registry, router, manifest
  identity/       IDN-1..4   NEW      canonical location_id, alias map, node class
  shadow/         DEC-0      RESHAPE  recorder exists; runner does not
  baseline/       EXP-0      BUILT    historical control group from vendor exports
  experiment/     EXP-1..3   NEW      control arm, exploration policy, integrity
  learning_loop/  PRD-1..8   RESHAPE  repoint at per-domain predictors (§2.1) — one model per
                                       predictor per vertical, never one model across them
  billing/        STL-3      RESHAPE  metering on the fee unit
  settle/         STL-1..2   NEW      savings statements, baseline control
  sla/            DEC-1      BUILT
  batch_queue/    DEC-2      BUILT
  optimizer/      DEC-3..5   RESHAPE  never called against a live project
  fleet_state/    DEC-6      BUILT
  messaging/      NTF-1      BUILT
  gig_platform/   SUP-1..5   RESHAPE
  returns/        —          deferred
driver-app/       DRV-1..6   RESHAPE  geofence + background are the gap
dashboard/        CON-1..4   RESHAPE
lmx-dwell/        PRD-3..4   BUILT    promote into app/
```

Conventions: **no abstraction without two live callers** · **vendor, don't depend** · every decision row written at decision time with only what was known then.

---

## 8. How to tell if this is working

| # | Number | Phase |
|---|---|---|
| 1 | Someone has been quoted a price | 0 |
| 2 | Measured cost-per-drop delta, with a control arm | 2 |
| 3 | Time-to-first-order for customer two | 4 |
| 4 | One order that isn't our own customer's | 4 |

The first needs a phone call. The second needs one sensor change and one quarter. The third and fourth need a contract clause. Everything else is downstream of those four.
