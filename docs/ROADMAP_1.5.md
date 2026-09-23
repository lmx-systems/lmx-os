# LMX 1.5 — Build Roadmap

**v1.2 · 18 September 2026 · Sourabh**

*v1.3 (23 September 2026) adds `DRV-7`, the dock survey at the stop: the missing writer for `IDN-4`'s surveyed and `M5` columns. Nothing else changes.*

*v1.2 adds the `agents/` module to Phase 1 and amends §2.1, in response to the external review of 17 September. The decision path is unchanged; the edges of the system are not. See `MODEL_AND_DATA_BRIEF.md` §14.*

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

**Amendment, 18 September 2026 — the agent challenge.** External review argued that model-selection-then-training is a traditional shape for this problem, and that headless agents reading across all available datasets would get to an answer faster. **The decision above stands for the decision path**, and is now scoped explicitly rather than by omission:

- **Agents belong at the edges** — order-feed ingestion, identity resolution, cold-start priors, and explaining a decision to an operator. Unstructured and variable input, no calibration requirement, errors visible and correctable.
- **Trained predictors belong in the middle** — wherever the output is traded against money in the solver and has to be reproducible when a customer disputes a savings figure.
- **Arithmetic stays arithmetic.** `M3` and `M4` are computed; an exact answer is available and neither an agent nor a model improves on it.

`MODEL_AND_DATA_BRIEF.md` §14 carries the test and the layer-by-layer split. The strongest form of the challenge is commercial rather than technical: the Y1 book is 25 Micro sites at $149 per month, where a per-customer integration never amortises, so **`AGT-2` is a precondition for the Micro segment existing rather than an optimisation of it.** `AGT-1` decides the question by measurement rather than by architecture argument.

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
| **DRV-3** | Warehouse geofence | `BUILT` | `hub_geofence_events` (migration `0064`) + `app/delivery/turnaround.py`. **A separate table from `stop_geofence_events`**, whose `stop_id` is a non-nullable FK — widening it would mean every reader of a dwell sample remembering to exclude the depot, and the first to forget gets the hub in the dwell distribution. Keyed on `(hub_id, driver_id, kind, occurred_at)` because several drivers cross one fence within a minute. **In the app it shares the stop region set**, marked by a `hub:` identifier prefix: iOS caps regions per *app* and `startGeofencingAsync` replaces the whole set, so a second task would fight the first for the cap — the 18-stop window always left this slot. Registered whenever a driver is on duty, **not only when they have a route**, because a driver back in the yard with nothing assigned is exactly the turnaround worth measuring. **Pairing is deliberately not the sensor's job:** three things are not turnarounds and each inflates the figure — an arrival with no departure (open, not zero), a departure with no arrival (pairing backwards reports an overnight), and anything past four hours (a car park). All three are counted and reported, with a `pairing_rate`, because a hub where half the crossings never pair has a fence problem that a median over the other half would hide |
| **DRV-4** | Stop-event outbox | `BUILT` | **The outbox already did the hard part, and one line undid it.** Arrive, scan, complete, flag and geofence crossings all queue to AsyncStorage and survive the app being killed — but `isPermanent = status >= 400 && status < 500` treated a **401** as a business-rule rejection, and the comment beside it named "a stale/rejected auth token" as something that returns the identical error however often it is retried. It is the one thing that does not. Queue a day of stops in a dead zone, let the token expire down there, come back into signal: every item 401s at once, every one is marked permanently failed, `flush` skips them for ever and nothing clears the flag. **The whole shift is lost, visibly, in the queue.** 401/408/429 are retryable now, a 401 triggers one token refresh before the next pass, and the classification is a pure exported function with its own tests. Two nested causes: `/auth/refresh` returned a fresh token and **nothing adopted it** — `AuthContext` discarded the result — so expiry was certain rather than unlucky; `src/auth/token.ts` now owns taking a token into use in both places it has to live. **Location pings stay fire-and-forget deliberately:** a ping is a sample of a continuous signal, and replaying a twenty-minute-old position would report where a driver *was* as where they are. The ops map already draws a stale fix hollow rather than hiding it |
| **DRV-5** | Battery and permission degradation | `PERMISSION HALF BUILT · BATTERY UNVERIFIED` | **"Clear state when permission is denied" is done.** Permission was checked when the watcher started and when geofences registered, and never again — so a driver who granted it at 6am and revoked it in Settings at 10am left the app holding state that claimed a sensor it no longer had: regions registered but silently not firing, `DRV-1`'s second-precision dwell quietly degrading to tap-grade for the shift. `useLocationDegradation` re-checks on foreground (the only moment it can have changed), tears down geofences and the watcher for whichever tier is gone, and tells the driver what stopped and what to do instead. It never re-requests — a cold prompt after a deliberate refusal is how an app earns a permanent denial (`docs/BACKGROUND_LOCATION_CONSENT.md` §4.4). The judgement is a pure function with eight tests, including that the copy never tells a driver to turn it back on. **The battery clause is not verified and cannot be from here:** "under 4% per 8-hour shift" is measured on a real phone over a real shift |
| **DRV-6** | Exception capture at the stop | `BUILT` | `FlagIssueScreen` exists — verify reason codes match `REC-3` and that it is under 3 seconds |
| **DRV-7** | Dock survey at the stop — the writer `IDN-4`'s surveyed columns never had | `NEW` | A driver completing a stop at an unsurveyed dock answers eight taps in **under 30 seconds**, offline or not, and that dock's profile then reads `is_surveyed`. Skipping never blocks completion. A dock is not asked again for 12 months. `set_access` and `set_autonomy_fit` leave the orphan allowlist. **80% of docks visited in the first month of go-live are surveyed.** Spec below |

> **DRV-1 and DRV-2 are the whole of Phase 1's risk.** Everything else in this phase is plumbing.

#### DRV-7 — the dock survey, specified

**Why it exists.** `app/identity/profile.py` already holds everything `M5` needs to learn from — `landing_surface`, `curb_access`, `door_path`, `obstruction`, `who_receives` — plus the access facts (`walk_distance_band`, `carry_effort`, `appointment_required`). Every one of them is validated against a fixed vocabulary, tested, and **written by nothing**: `set_access` and `set_autonomy_fit` sit in `tests/test_no_new_orphans.py` as *"profile field with no live writer."* `MODEL_AND_DATA_BRIEF.md` §6 says `M5` is a discovery process capped at the customer's dock count, and that the survey is weeks of fieldwork regardless of volume. It is weeks of fieldwork only if somebody has to go and do it. Our drivers are already standing at every one of those docks.

**The rule it inherits.** A measurement may fail; a delivery may not (`docs/THE_DRIVER_APP.md` §2). The survey appears **after** `complete_stop` has been queued, never before it, and every question has a skip.

**What the driver sees.** One screen, eight single-tap questions, in this order. Answer codes are the existing constants in `app/models/receiver_profile.py` — no new vocabulary on the phone.

| # | Question on screen | Writes | Values |
|---|---|---|---|
| 1 | Where could you stop the vehicle? | `stop_point` **(new, see below)** | `loading_dock`, `marked_bay`, `lot`, `street_legal`, `double_parked`, `none_legal` |
| 2 | Could a vehicle pull up at the kerb by the door? | `curb_access` | `CURB_ACCESS` |
| 3 | How far from the vehicle to the handover? | `walk_distance_band` | `WALK_DISTANCE_BANDS` |
| 4 | The way in to the door | `door_path` | `DOOR_PATHS` |
| 5 | Anything in the way? | `obstruction` | `OBSTRUCTIONS` |
| 6 | Who took it? | `who_receives` | `WHO_RECEIVES` |
| 7 | Open ground nearby a drone could set down on? | `landing_surface` | `LANDING_SURFACES` |
| 8 | Did you need an appointment or booking? | `appointment_required` | yes / no |

Optional: one photo of the drop point from where the vehicle stopped, through the existing `PodCapture` upload path. No people, plates or paperwork, stated on screen.

**What it deliberately does not ask.**

- **Time on site.** `DRV-1`'s geofence already measures it to the second. A driver-pressed timer would reintroduce the tap-grade dwell Phase 1 exists to remove.
- **Weight and size of the goods.** These belong to the order, from invoice lines, not to the dock. `carry_effort` is asked only where a stop has no line-item weights, and then as a property of that delivery, not the dock.
- **Receiving hours.** `set_receiving_hours` is *stated, by the receiver*. A driver's guess at a dock's hours is exactly the soft label `IDN-4` separates by source. Hours come from the dispatcher or the client portal.

**When it is shown.**

- The stop's dock has no profile, or `is_surveyed` is false, or `surveyed_at` is older than 365 days.
- At most **three surveys per driver per shift**, so a first week at a new customer does not turn every stop into paperwork. The remaining docks wait for the next visit.
- Never on a pickup, never on a failed stop, never while the vehicle is moving.

**Build.**

1. **Migration.** `receiver_profiles.stop_point` (`String(16)`, nullable) with `STOP_POINTS` beside the other constants, and `surveyed_by_driver_id` (nullable FK) so a bad surveyor can be found and their answers discounted. No other schema change.
2. **Endpoint.** `POST /driver/stops/{stop_id}/dock-survey` resolves stop → shop → `location_id` and calls `set_access` and `set_autonomy_fit` with whatever was answered. A stop whose shop has no `location_id` returns 409 with a reason, not a silent write to nothing (`IDN-1`).
3. **Stop payload.** The driver's route response carries `dock_needs_survey: bool`, computed server-side from the rules above, so the phone never restates the condition (`THE_DRIVER_APP.md` §6).
4. **Outbox.** A sixth action type, `survey`, idempotent on `(location_id, driver_id, service_date)`. Same retry classification as the other five; nothing new in `isPermanentFailure`.
5. **Tests.** Endpoint round trip and vocabulary refusal; the `dock_needs_survey` rule including the 365-day and three-per-shift limits; an outbox test that a survey queued offline survives a kill and flushes once. Remove the two allowlist entries in the same change, or `test_no_new_orphans` fails on its second check, correctly.
6. **Docs.** `THE_RECORD_AND_IDENTITY_LAYERS.md` §7 loses the `IDN-4` row; `THE_DRIVER_APP.md` §1 gains the sixth action.

**The standalone Dock Log.** A separate survey page exists for drivers who are not ours, to map docks before we serve them. It currently uses its own answer set (feet not metres, "one hand" not `hand_carry`), which `_require_in` would reject. It moves to this vocabulary before any of its rows are imported. Imported rows match a `Location` by coordinates, write only to a dock that none of our own drivers has surveyed, and never overwrite one that has — the same "never blend two sources" rule as `inherited_dwell_*`.

**Open questions.**

- Whether `curb_access` and `stop_point` are one question or two in practice. Keep both until the first 50 surveys show whether drivers answer them differently.
- Whether the three-per-shift cap should rise once the founding dock set is covered, since after that almost every survey is a genuinely new dock.

### identity/ — new module

| ID | Feature | Status | Done when |
|---|---|---|---|
| **IDN-1** | `Location` table in `app/identity/`, one row per physical dock; `Shop.location_id` FK, backfilled (§2.2b) | `BUILT` | **The FK is now populated at shop creation, which it never was.** `resolve_location`'s only caller had been `scripts/load_identity_from_export.py`, a one-off backfill — so every shop created since had a null `location_id`, and the ad-hoc pickup path that is LMX Link's whole premise created docks the identity layer never saw. `link_shop_to_dock` now runs at both creation sites (intake and admin onboarding); an address naming no place still leaves it null, which is the honest record rather than a shared fictional dock. `app/identity/resolution.py`, merged. Placeholder addresses (`N/A`) are refused rather than normalised into one fictional dock |
| **IDN-2** | Alias map + merge review queue. **Human-confirms the founding ~230, auto-merge after, every merge audited and reversible** (§2.2c) | `BUILT` | **Wired both ends.** `propose_duplicate_locations` fills the queue on the nightly tick — claimed once a day across all hubs rather than per hub, because the same physical dock can be reached from two and scoping the comparison would hide exactly that pair. `GET /operations/merge-proposals` is where it is worked: both addresses shown, never the ids, since *"are these the same place"* cannot be answered from two UUIDs. Confirm, reject and revert are admin — a merge rewrites which dock a shop points at and every per-dock statistic moves with it — while reading the queue is open to anyone, because a dispatcher spotting a duplicate is how good proposals get noticed. **A proposal that extends a chain says so.** `confirm_merge` applies immediately and the queue described one pair, so a reviewer had no way to see that the dock on the right had already absorbed four others — and `AGT-1`'s transitive closure found what that costs: a dock holding a municipal DPW, two county departments and an unrelated business, every edge individually defensible because every reviewer saw one pair. `merge_scale` reports the shops behind each side and the docks each has already absorbed; the console labels the row and names how many records the click would make one place. A merge erases its own seam — that is what it is for — so there is exactly one moment the scale can be shown. Needed `IDN-1` first: until shops were linked to docks at creation there were no near-duplicates to find. `app/identity/merge.py`, merged. Built on UDID root/branch rather than coordinates, which the export does not carry; requiring two independent signals cut 192 candidates to 78 |
| **IDN-3** | Node-class labelling (shop / parts store / dealer / body shop / warehouse / transfer / municipal) | `BUILT, DONE-WHEN UNMET — the tail needs a person` | `app/identity/node_class.py`, merged. **The rules are now exhausted and the number is measured, not estimated: 28.7% of 230 accounts unlabelled against a target under 2%.** Re-running against the real book found three genuine gaps and closed them — `\bauto\b` never matched `automotive` (they are different tokens), fuel brands on a receiver taking parts are service stations, and `car care`/`car wash` — which took 36.1% to 28.7%. What remains is family and personal business names with no industry word at all, and the export carries no industry code, so **no further rule can read it.** The decision the row has always named is now actionable rather than theoretical: `GET /operations/unlabelled-docks` lists the tail busiest-first (by inherited dwell samples, the only evidence of volume before we deliver there) and a dispatcher labels it in the console. A human label outranks the rules and is never overwritten. Either somebody works that list, or the target moves — but nothing else will move the number |
| **IDN-4** | Receiver profile store | `BUILT` | **Cold start has an answer.** `inherited_dwell_*` (migration `0062`) holds dwell a previous operator measured at the same dock, imported from the design partner's second-precision export — its own columns, never ours, because the nightly refresh would erase a figure written to `dwell_p50_seconds` by 2am and because they are different measurements by different drivers. `dwell_estimate()` picks one and says which; it never blends, since a weighted average of our two deliveries and somebody else's four hundred is a number with no owner. Only the Detail export is importable — the whole-book file is minute-resolution and 65.5% of its stops compute to zero dwell. **Wired.** `refresh_hub_dwell_statistics` runs on the learning loop's nightly tick, per hub, stalest docks first and capped so a large hub makes progress every night rather than timing out. Nightly rather than per delivery: an exact `percentile_cont` over a dock's whole history is the right query once a day and the wrong one on the path a driver waits on. Before this nothing called `refresh_dwell_statistics` at all, so every profile held whatever a one-off script last left there. `app/identity/profile.py`, merged. Geofence evidence is preferred over driver taps per dock. **The surveyed columns — access and the `M5` autonomy labels — still have no writer; `DRV-7` is it** |

> Start here — it is days of work. The design partner's export has **230 customer IDs → 224 names → an unknown smaller number of physical docks**: five records for one body shop across two ID roots, one shop twice with its city spelled with zeros for O's, one record with city/state/zip literally `N/A`. Until identity holds still every per-dock number is wrong, and it has already put a withdrawn figure in investor materials.

### ingest/ — hardening

| ID | Feature | Status | Done when |
|---|---|---|---|
| **ING-1** | ERP webhook field-map verification against live orders | `RESHAPE` | `app/ingestion/` is 978 LOC with an adapter registry; the field mapping is unverified. Unknown fields fail loudly rather than silently null |
| **ING-2** | Idempotent intake, **billing key pluggable** pending 0.5 (§2.2d) | `RESHAPE` | A replayed order produces one record and one billable event, under either fee unit |
| **ING-3** | Replay and backfill | `BUILT (mechanism) / BLOCKED (no data)` | `Order.intake_mode` (migration `0061`) and `app/ingestion/backfill.py`. **Intake does six things and five are wrong for history** — pricing puts a historical delivery back in `generate_invoice`'s selection and bills the customer twice; the arm writes to append-only `experiment_assignments`, so an arm on an order delivered three weeks ago contaminates `EXP-1` permanently; the hold queue sends a driver to collect a delivery that already happened. **Replay is a property of the contract:** `source_order_ref` is required with `min_length=1`, so every order has something to be idempotent on and a re-ingest returns the existing row *untouched* — the unique index stops the second row and says nothing about the first, which is the "without mutating decisions" half. **Blocked on there being no order-level history to import** — not on an adapter, as the last revision claimed. Both exports describe *stops* and carry no order reference or street address, so no amount of engineering turns them into orders. Unblocking it is a commercial ask — an order-level export from the incumbent — not a build item. The mechanism is complete and tested for the day one arrives |
| **ING-4** | Historical export loaders | `BUILT — the done-when was wrong` | **The promotion it asks for should not happen, and reopening it in the last revision was my error.** `BaseIngestionAdapter.normalize()` returns a `NormalizedOrder`; both of the design partner's exports are **stop-level, keyed on `receiver_id`, with no street address, no pickup address and no order reference** (`ml/real/export.py`'s `TimingStop`/`DetailStop`, `dispatch_export.py`'s `ExportedStop`). Registering them as order adapters would mean inventing pickup addresses that are not in the data. The module's own docstring said so from the start: *"Not an order adapter… it never creates work, it describes work already done."* What it is actually for — seeding identity — works, and is wired through `scripts/load_identity_from_export.py`. Done-when should read *"parses both exports and seeds `IDN-1`'s docks"* |

### agents/ — new module

Scoped by §2.1's amendment. **Nothing in this module touches the decision path** — `M1`, `M2` and the solver are unchanged.

| ID | Feature | Status | Done when |
|---|---|---|---|
| **AGT-1** | **The resolution bake-off.** Hand-label the true physical dock count behind the 230 account IDs once, then run the existing deterministic resolver and an agent resolver on identical input | `HARNESS BUILT · AWAITING LABELS AND A DATA DECISION` | `ml/agt1/` + `scripts/agt1_bakeoff.py`, filed beside `ml/m1/` because it is a gate run and not a production path. **The pool is the measurement's honesty.** 229 accounts make 26,106 pairs and nobody labels 26,106, so pairs enter a pool by shared account root, postcode or rare name word — 1,856 — **union every pair the incumbent proposes anywhere**, because 20 of its 97 calls fall outside every block and a pool without them would report a precision with holes in it. 1,876 pairs to judge: precision is then exact, recall is an upper bound, and that sentence travels with the number. **The label file hides the verdicts and shuffles the blocks** — a reviewer shown the incumbent's answer agrees with it, and the truth set drifts until precision approaches 1.000 by construction. `?` is a real answer and is scored against nobody. **Clusters are scored, not just pairs**, because merges are transitive and the roadmap's question is a dock count: the incumbent's 97 proposals close into **174 docks**, and that closure is where it fails. **Two findings, before a single label exists.** Its largest implied dock holds **14 accounts** — `1960/0-A*` is a catch-all suffix bucket, so "same stem, differing suffix → HIGH" chains a municipal DPW, two county departments and an unrelated business into one place. Its second holds **nine repair shops in seven towns**, because `repair` is missing from `_COMMON_TOKENS` and is therefore scored as a *distinctive* word: every "X & Y AUTO REPAIR" has 1.00 distinctive-word overlap with every other. **Neither was fixed here** — changing the contestant because the harness found something, before the harness has been scored, is the anchoring failure in a different costume. Both are fixed in a separate change, measurable against the incumbent as it shipped: the measured trade and municipal words joined `_COMMON_TOKENS` and a shared stem whose names share no word is now `REVIEW`, which takes the book to **73 proposals, 182 implied docks, 1,715 pairs to label and no proposal outside the blocks** — the escaping twenty *were* the repair-shop pairs. The 14-account chain survives, because a tier is advice and the closure does not read tiers; it is answered in the queue instead, where `merge_scale` now shows a reviewer what a click would join. Figures above are the incumbent as `AGT-1` found it. **Two things stand between this and an answer:** nobody has labelled the file, and the agent challenger cannot run — there is no LLM credential in this repository, and every pair it would send is two of the design partner's customers by name and town, which 0.2's unsigned data-rights clause is what would govern. The report prints `DID NOT RUN` with the reason rather than omitting the row |
| **AGT-2** | Agentic order-feed mapping — an arbitrary customer export to our schema, with a human review gate before first live order | `NEW, GATED ON AGT-1` | A new Micro customer is onboarded in under a day with no engineer writing an adapter. `[ASSUMPTION]` the hand-built path is 2–4 engineering weeks per customer |
| **AGT-3** | Cold-start prior from unstructured evidence — dock survey, address, customer notes | `NEW, GATED ON AGT-1` | Outputs a distribution rather than a point, is superseded by observation once the dock has history, and beats the node-class prior on unseen docks — or is not shipped (§4 rule 3 in the brief applies unchanged) |
| **AGT-4** | Decision explanation for the operator console — why this order was held | `BUILT` | Every explanation cites `REC-1`'s decision log rather than narrating. No explanation the record cannot support |

**AGT-4 found that there was nothing to explain.** `run_hold_cycle` has always
returned a reason for every order it looks at — hot shot, deadline reached, no
cluster mate, no driver available, conflict with a more urgent order — and
`run_cycle` kept only the set of ids it released. So a product whose central
claim is that **the hold is the product** held orders and recorded no reason for
any of it, and the only honest explanation of a hold was "we do not know".
`decision_snapshots.hold_decisions` now carries them (migration `0059`).
Snapshots written before it read as *reasons were never captured*, which is true
and is deliberately not backfilled.

**Also in scope, unresolved:** benchmark tabular and time-series foundation models — TabPFN, Chronos, TimesFM — against the shrunk baseline for `M1`. Small-sample tabular at ~15 observations per dock is the shape they are built for. This is the defensible form of "do not train from scratch"; training time itself is not a cost we pay.

### record/ — the discipline layer

| ID | Feature | Status | Done when |
|---|---|---|---|
| **REC-1** | Immutable decision log — feature snapshot at decision time | `BUILT` | Read back by `GET /operations/record-health`, which reports each writer in the record layer and **when it last wrote** — zero rows with no timestamp is a writer that never ran, zero rows with an old one is a writer that stopped, and both are zero. `app/record/decisions.py`, merged (#48). Append-only at the database; a replay that recomputes a different input hash raises rather than returning a plausible answer |
| **REC-2** | Execution trace — geofence, warehouse, exceptions, actual cost, **abstentions** | `BUILT` | **Wired, and in that order deliberately.** A dispatcher records what happened through `POST /orders/{id}/consequence`, working from `GET /operations/late-orders`; the nightly tick then closes the windows nobody judged. Wiring the scheduler first would have labelled *every* late delivery `silence` — false labels, in an append-only ledger, indistinguishable from true ones by the time anyone trained on them. The console asks for the silences explicitly, because somebody always reports the angry phone call and nobody reports the twelve deliveries that were late and fine. Stop-level arrive/depart **does** exist — `DRV-1` shipped `stop_geofence_event`, and the old row predates it. `app/record/cost.py` costs a **driver-day** (not a route: first-arrival-to-last-departure makes overhead exactly zero by construction) and writes each drop's share into REC-3's ledger with its whole basis — rate, rate source, timing source, how many orders shared the stop. Loaded costs sum to the wage bill; own-time answers "was this drop worth making". **The gap is warehouse turnaround** — `DRV-3` is the geofence that would break it out, so today it sits inside overhead rather than missing from it |
| **REC-3** | Outcome ledger | `BUILT` | **Wired at delivery completion.** `record_delivery_outcomes` runs inside `complete_stop`'s transaction for every dropoff, on the orders `advance_orders` actually moved — so a replayed offline action cannot write a second row into an append-only ledger. The commitment is resolved at delivery against the client's terms *then*, because terms change and a recomputed outcome would judge a delivery against a promise made after it. `snapshot_that_assigned` fills `decision_snapshot_id`, which was null on every row: `REC-1` and `REC-3` are built to be compared and nothing had ever joined them. Before this the ledger was empty in production — the only writer was `record_arm_abstention`, itself inert until a client contracts an arm (`docs/ROADMAP_AUDIT_2026-09.md`). `app/record/outcomes.py`, merged (#48). Values are copied, not referenced, so a later edit cannot move a replay. Supersede requires a reason |
| **REC-4** | Linkage flag engine — open return on the same part, duplicate order across branches, repeat visit today | `BUILT` | **Wired both ends.** `run_linkage_detectors` runs on the nightly tick and `GET /operations/linkage-flags` reads them into the console — raising flags into a table nobody opens is not better than not raising them. Resolving records that somebody looked rather than deleting: a dismissed flag is evidence a person considered the case, and deleting it would let the detector ask the same question tomorrow. `app/record/linkage.py`, merged (#48). All three detectors |
| **REC-5** | **Data dictionary** — dwell, stop, order, route defined in writing | `NEW` | One page, signed by all three founders. Three sources currently report three different stop counts for the same window |

---

## Phase 2 — Shadow and measure (weeks 6–16)

**Goal:** a measured cost-per-drop delta with a control arm. Converts the central claim from modelled to observed.

| ID | Feature | Status | Done when |
|---|---|---|---|
| **DEC-0** | Shadow-mode runner | `BUILT, OFF` | `app/shadow/scheduler.py` runs the cycle per hub on a cadence, `app/shadow/divergence.py` computes the delta, `scripts/shadow_day.py` reports a window. **Off until `shadow_scheduler_enabled` is set** — every cycle is a solver call and a live Google project bills for it. The report measures the cycle interval actually achieved and states how much of the dispatch lead the cadence alone explains. Cost-per-drop and on-time deltas are refused by design: the shadow plan was never driven, so `EXP-1` is the measurement |
| **EXP-0** | Historical baseline from the customer's own prior exports | `BUILT` | `app/baseline/` + `scripts/analyze_baseline.py`, merged. Reduces a driver-activity and a stop-invoice export to drops per driver-hour, miles per drop, on-time rate, batch rate, cost per billed stop and a two-file reconciliation. **Strictly weaker than `EXP-1` and superseded by it** — a historical comparison is confounded by season, mix and volume, so this sizes a prospect and seeds `STL-2`; it is not the counterfactual a savings statement rests on |
| **EXP-1** | Control-arm randomiser — 5–10% dispatched as the customer would have | `BUILT, OFF` | `app/experiment/arms.py`, and **wired into intake** — `app/ingestion/service.py` assigns the arm as the order lands, because "at intake" is a clause of the done-when rather than a convenience. A control order's batching hold collapses to zero and the hold we declined is written to the ledger (`app/record/abstention.py`), which is what makes the arm auditable. Still **off until a client has a recorded contract date**; no client is enrolled, so all of it is inert |
| **EXP-2** | Exploration policy and per-receiver caps | `BUILT` | `app/experiment/arms.py` + `exclusions.py`, migration `0055`. Assignment is a **permuted block per dock** — within each run of `round(1/fraction)` orders to one dock exactly one is control, so the share is arithmetic rather than an average. A quota-and-force-treatment cap was rejected: the orders it moves are the ones that came after a dock was already unlucky, which biases the control group against the busiest docks. Exclusions carry a required reason, keep their row when revoked, and `exclusion_impact` states the share of volume the measurement never covered — `STL-1` should print it |
| **EXP-3** | Arm integrity monitor | `BUILT` | `app/experiment/integrity.py`, and it **gates** rather than alerts — `STL-1` runs it before computing anything and prints no figure if it blocks. Six checks: verification, skew (Wilson, not Wald), EXP-2's per-dock guarantee, position collisions, terms drift, and **differential coverage** — if control orders are costed less often than the rest, the assignment stayed fair and the comparison did not. A seventh checks that every control order carries the abstention intake wrote. **One limitation remains, reported on every run**: the abstention covers the hold, not whether the optimizer batched the order afterwards — it never sees an arm label, by design |
| **PRD-1** | Batch value by node class | `BUILT, BLOCKED ON DATA` | `ml/prd/batch_value.py`, merged (#50). **The done-when cannot be met from the export we hold**: it contains one warehouse receiver and no transfer nodes, and the file's own `Transfer` flag is false on all 6,715 rows. The shop end measures +22% to +40% across three readings of "buys +X%", not +3–7%; among classes with 5+ docks the whole spread is 1.5×, not 40×. Needs either a customer with warehouse/transfer volume or a traced source for the stated figures |
| **PRD-2** | Trip cost versus order value | `BUILT` | `ml/prd/trip_cost.py`, merged (#50). On the export, 920 billed stops: at $45/hr 12.3% lose money and 43 are the cheap-part-long-trip class. Time only — there is no distance column anywhere in the export — so every figure is a lower bound. The driver rate is a required argument with no default |
| **PRD-3** | Shrinkage baseline library | `BUILT, PROMOTED` | `ml/m1/baseline.py`, merged (#50) — out of `lmx-dwell/` and onto the standard library. Beats a single global quantile on both warm and cold populations, so the claim reproduces |
| **PRD-4** | Evaluation harness — time split plus held-out receivers | `BUILT, PROMOTED` | `ml/m1/evaluate.py`, merged (#50). Warm and cold scored apart; receivers held out by hash so the same dock lands on the same side next month. **Caveat that governs every dwell number**: the only second-precision file is one driver's 54 manifests, so no driver effect can be separated and there is no held-out-driver split |
| **STL-1** | Savings statement generator | `BUILT` | `app/settle/statement.py`. Plain-text statement from `EXP-1`'s arm, `REC-2`'s cost and `EXP-2`'s exclusion disclosure. **The interval is the headline, not the midpoint**: if it spans zero the statement says no saving is shown yet, and below 30 costed drops per arm it produces no interval at all. Carries **no billable amount** — §2.2(d) says a savings-share ledger is per-order and adversarial while this is sampled, so a field a billing run could pick up would settle a live commercial question by default. Rendered to PDF in house style (`app/settle/pdf.py`) — logo, brand green, one page. **The Difference row shows the range, never the midpoint**: the first draft put a bold point estimate above a sentence retracting it, which only showed up on the rendered page |
| **STL-2** | Baseline definition and change control | `BUILT` | **The refusal is now enforced, not merely available.** `require_agreed` existed and was called by nothing, so a statement PDF could be written with no basis at all, on one signed by a single side, or on one it no longer reproduced under — and that last case was already being printed to the terminal and then ignored. `scripts/settle_month.py` now refuses at the only place that produces something a customer sees. `--draft` overrides it and **the page then says DRAFT and why, above the headline**: an escape hatch that produces an unmarked artifact is not an escape hatch, it is a bypass. `app/settle/basis.py`, migration `0058`. **Necessary the moment `--recompute` existed**: it supersedes costs after a statement has gone out, so the same command over the same period can produce a different figure and the customer is holding the first one. Issuing freezes the definition and fingerprints the per-order costs — reusing `REC-1`'s freeze-and-hash — so a re-run either reproduces or names the orders that moved. Costs are stored apart from the definition on purpose: folding them into `inputs_hash` would make every recomputation read as a change of method. Versioned per client, superseded rather than edited, and **both signatures or it is a draft** |
| **STL-3** | Metering and invoice export on the fee unit | `BUILT, ONE DECISION OPEN` | `app/billing/reconciliation.py` + `scripts/reconcile_billing.py`, windowed on **ingestion** because the fee unit is the ingest — billing windows on `delivered_at`, and only a check anchored to the ingest sees an order taken in one month and billed in the next. **It surfaces a unit mismatch rather than resolving one**: 0.5 sets the unit at *per order ingested* and `generate_invoice` bills *per order delivered*, so a cancelled or failed order is free today. That is §2.3's live commercial question with an owner, so the reconciliation counts it and refuses to call the period clean. Exit code 1 when it does not reconcile, so it can sit in a month-end job. The second billing event type for a licence still waits on 0.5 |

### What actually stands between here and the gate

*Audited against the code on 17 September 2026, not against the rows above.*

Seventeen of the nineteen Phase 2 items read BUILT. The phase still cannot
close, and the reasons are three different kinds of thing that the status column
cannot tell apart. Producing **one statement with a figure on it** requires all
of the following to be true at once:

| # | Required | State |
|---|---|---|
| 1 | A customer's contract records the date they agreed to a control arm | **People.** Nobody has signed one |
| 2 | That client is enrolled — `control_arm_contracted_at` and a fraction written | ✅ `scripts/control_arm.py enrol` |
| 3 | Orders reach intake and get an arm | ✅ `app/ingestion/service.py` |
| 4 | Drivers have `hourly_rate_cents` | ✅ `scripts/set_driver_rate.py` |
| 5 | Drivers clock on, so shift events exist | ✅ the endpoint exists, gated by `R4` compliance |
| 6 | Something computes cost per driver-day | ✅ `record_costs_for_period`, via `scripts/settle_month.py` |
| 7 | Something generates the statement | ✅ `scripts/settle_month.py` |
| 8 | 30+ costed drops in each arm | **Data.** Follows from 1–7 plus time |

**The switches existed nowhere and now exist as commands.** The audit found the
measurement layer complete, tested and unreachable: nothing set a contract date,
nothing recorded an exclusion, nothing computed a cost, nothing produced a
statement. The day a clause was signed, somebody would have been writing SQL
against production.

This was the same defect as `record_shadow_cycle` having no caller, which
`DEC-0` found and fixed — three more were introduced after it and nothing
caught them, because a module with tests and no entry point looks finished from
every angle except that one. `tests/integration/test_the_switches.py` now runs
the whole chain (enrol → ingest → cost → settle) so the next one fails a test
rather than an audit.

**Only item 1 is left**, and it is not ours.

**Blocked on people, not engineering:**

- **0.4** — Sourabh approved the background-location consent approach on 16 Sep.
  0.4 as written is Matan negotiating it into *pilot terms*, which is a different
  act and is still open.
- **0.5 / §2.3** — licence versus savings share. Blocks `STL-3`'s second billing
  event type. Killed twice, reopened 13 Sep.
- **0.7** — outcome liability. Blocks `DEC-1`'s credit percentages.
- **0.8** — the background-location "always" tier.
- **The shadow cadence.** `DEC-0` runs but is off; the cadence bounds what the
  dispatch lead can show and is an operating cost, so it is not ours to pick.
- **`REC-5`** — three founder signatures. The evidence for why is now concrete:
  the two exports disagree about what a route is by a factor of seven.

**Blocked on data nobody holds yet:**

- **`PRD-1`** — one warehouse receiver and no transfer nodes in the export, so
  the finding the item is defined by cannot be tested. Needs a customer with
  that volume, or a traced source for the stated figures.
- **`M2`** — 500–1,000 observed consequences, 21–41 months at one partner.
- **Cold-start dwell** — calibrated honestly, the p90 promise is 23.2 minutes
  for a stop whose median is two. More docks, not more code.

**So the shortest path to the gate is four switches and a signature**, in that
order — and the four switches are days of work, not weeks.


---

## Phase 3 — Live authority (weeks 12–26)

**Goal:** LMX makes the call on real orders.
**Know the ceiling:** **50.2% of orders already get in-flight insertion by hand**, and those reach customers *faster* (34-minute median vs 40). The differentiator is doing it consistently at volume without one irreplaceable dispatcher.

| ID | Feature | Status | Done when |
|---|---|---|---|
| **DEC-1** | SLA engine live | `BUILT` | `app/sla/` — engine + commitment, 265 LOC. Zero top-tier breaches attributable to holding |
| **DEC-2** | Batch-hold queue live | `BUILT` | `app/batch_queue/` — queue, clustering, store, 350 LOC. Measured stops-per-route improvement vs the control arm |
| **DEC-3** | Optimiser against a live Google project | `RESHAPE` | `app/optimizer/google_routes_client.py` exists and has never been called against a live project (`docs/ROADMAP.md` E1). p95 solve under 5s |
| **DEC-4** | In-flight insertion | `BUILT, UNMEASURED` | The mechanism is there — `run_cycle` inserts into active routes with a capacity check, and **all three dispatch triggers now have producers** (`event_trigger.py`'s docstring saying `stop_completed` has none is stale; `driver_routes.py:2273` publishes it). `app/reporting/insertion.py` adds the missing half — our own order-to-door, split in-flight against planned, on `GET /operations/scorecard`. **The baseline verifies exactly**: recomputed from the export, 34.0 min against 40.0 at a 50.2% share, matching all three figures. It reads `not_measured` until a pilot runs, and the comparison is orientation rather than proof — another company's operation, season and mix, which is the confound `EXP-0` is refused for |
| **DEC-5** | Fallback router | `BUILT` | `app/optimizer/fallback.py`. The routing client is built once and cached for the process, so before this a Route Optimization outage stopped dispatch until somebody redeployed without the project id. Tries Google, drops to the existing nearest-neighbour planner, and **records the degradation**: a plan from the fallback reports `stub_nearest_neighbor_fallback`, which a deployment configured without Google never does — one is a choice, the other an incident, and `REC-1` keeps the row for months. A breaker opens after three consecutive failures because the primary retries with backoff, and paying that budget every cycle would blow `DEC-3`'s p95-under-5s for the whole outage |
| **DEC-6** | Fleet state manager | `BUILT` | `app/fleet_state/` — verify it never assigns to an off-shift or full driver |
| **CON-1** | Dispatcher live board | `RESHAPE — the gap was order lookup` | **A dispatcher could not find an order.** The hold queue's search filters the held list, so one already released, assigned or delivered was unfindable — while `GET /client/orders?q=` has let the *customer* search their own orders all along. The person phoning could find it; the person answering could not. `GET /operations/orders` fixes that, searching the same fields for the same reason, scoped by hub, and a result opens straight into `AGT-4`'s explanation because the caller's next question is *why*. **The board was also getting worse:** six panels had accumulated in one column, so a fortnight-old labelling queue sat between a dispatcher and the hold queue. Split into *Dispatching* and *To record*, with a count on the second tab — a tab with no count is a tab nobody opens, which would trade clutter for work that silently stops getting done. The four panels behind it now load when opened rather than on every page view. Still `RESHAPE`: *"a working dispatcher can run a day on it"* is answered by a dispatcher working a day, not by me. **The five curl-only admin routes are now reachable** — hub closures folded into `HubSettingsPanel` (the same kind of standing hub fact as the state code), `DriverDevicesPanel` beside driver onboarding, `CodDisputesPanel` on *To record*, and gig jobs and density in one `GigPathPanel` because the density report summarises exactly those jobs. Four panels rather than five and no new tab, because four of the five carry no outstanding-work count and the badge rule above is what makes a tab worth opening. **The COD count is deliberately kept out of the badge:** with no SMS provider configured every dispute is un-escalated by definition, so the count would never fall — a badge that never falls is the same failure inverted. **Device revocation turned out not to be curl-only but unusable:** it takes a device id, and the only list of device ids was driver-authenticated, so ops had to already know an id they could only get from the driver who has lost the phone. `GET /admin/drivers/{id}/devices` is new |
| **CON-2** | Override with mandatory reason code | `BUILT` | `POST /orders/{id}/override`, `dispatcher_overrides` (migration `0060`). **There was no override** — nothing let a dispatcher release a held order or hold a released one, so the reason code had nothing to be mandatory on. Enforced in three places because each stops something different: the schema stops a malformed request, a NOT NULL CHECK stops a script or a future endpoint, and `other` must carry a note — an `other` with nothing written is an override with no reason wearing the costume of one. Any ops session, not admin-only: releasing an order when the customer calls is the job, and what makes that safe is the attributed append-only record, not the role |
| **CON-3** | Override → training data | `BUILT` | The queue's decision is copied onto the override **as values** — the pair *(queue said hold because no cluster mate; dispatcher said release because the customer called)* is the training example, and a foreign key would let half of it change or vanish. **`system_decision_known` is the honest half:** when no cycle had recorded a decision the override still happens and is still recorded, but `labelled_overrides` excludes it, because inferring the queue's position from the order's status would manufacture labels out of its silence. Agreement is separated from contradiction too — a dispatcher who reaches the queue's conclusion first is not a correction, and counting them as one inflates the first number anyone will look at. Feeds `MODEL_AND_DATA_BRIEF.md` §12 Phase 3 |
| **CON-4** | Exception queue | `BUILT` | **Its fourth kind had no producer until `CON-2`.** `released_but_unplaced` reads `OrderStatus.queued`, and nothing in `app/` ever wrote that status: the optimizer deliberately leaves an unplaced order `held` so the next cycle retries it, which is correct. A human release is the only thing that produces a released-but-unplaced order, so the kind was built ahead of the thing that creates it and starts firing now. `app/reporting/exceptions.py` on `GET /operations/exceptions`. **Not the health check** — `app/health/checks.py` counts stuck orders for a monitor; this is a worklist with the order, the customer, how long, and the next action. Four kinds, and the quietest is the worst: released from the hold queue and never placed looks like an order in transit from every other view. **Sorted by the clock, and that is a heuristic rather than a prediction** — the model that would rank these is `M2`, which needs hundreds of consequences that do not exist, so the field is `minutes_waiting` and not a score |
| **PRD-5** | Dwell predictor, p50 and p90 | `GATE RUN — SHIP THE BASELINE` | `ml/m1/challenger.py` + `scripts/train_m1_dwell.py`. On the second-precision export the challenger **wins three of four and loses warm/p90** — 1.740 pinball against the shrunk baseline's 1.680 — which is the one that counts, since p90 is what you promise and warm is most of the traffic. Rule (3) says ship the baseline, and this is the second time this team's shrinkage baseline has beaten gradient boosting on this problem. **Caveat that keeps it open**: `libomp` is missing on the dev machine so LightGBM cannot load, and the substitute (sklearn quantile GBM) has no native categorical handling — it is handicapped on exactly the high-cardinality `receiver_id` LightGBM was chosen for. Re-run the gate once `libomp` is installed |
| **PRD-6** | Conformal intervals on the promise | `BUILT` | `ml/m1/conformal.py`, merged (#50). Split conformal, distribution-free. It tightens the warm p90 from 14.3 to 12.3 min and holds at 89.8%; calibrated correctly for cold start it widens to **23.2 min for a stop whose median is two** — kept 100% of the time and not sellable, which is what too little cold-start data looks like. It refuses rather than clamps below 9 calibration residuals |
| **PRD-7** | Censored-dwell handling | `BLOCKED ON ENVIRONMENT` | §M1b specifies XGBoost `survival:aft`, which has no LightGBM equivalent. XGBoost installs and cannot load on this machine — same missing `libomp` as PRD-5, and no Homebrew to install it from. One package away, and nothing else about the item is blocked |
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
