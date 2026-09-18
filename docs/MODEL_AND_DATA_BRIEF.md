# LMX 1.5 — Model selection and the data need

**v2.3 · 18 September 2026 · Sourabh**

*v2.3: adds §14, which answers the external-review challenge that this should be built with agents rather than trained predictors. The short version: the challenge is right about three layers and wrong about two, the strongest case for it is commercial rather than technical, and `AGT-1` settles it with a measurement instead of an argument. Nothing in §4–§6 changes.*

*v2.2: reconciled against the seed commercial model. The Y1 account book is 25 Micro and 2 Small sites with no Regional or National account, which puts M2 out of reach in Y1 (§5, §6c); the 15% pair-commingle rate is calibrated on the wrong segment for that book (§5, M3); and the model's Y1 savings of 12–19% conflict with the 20% floor in KPI 2.*

*v2.1: the urgency requirement is restated in **drops** with a per-segment table (§5, M2), the Wilson-interval figure of 626 replaces the loose 500–1,000 as the number KPI 1 is written against, §6 gains the metro-cluster resolution, and internal remarks are no longer attributed to named individuals — this repository is public.*

Supersedes and combines `MODEL_AND_DATA_BRIEF.md` v1.2 and `DATA_NEED_BRIEF.md` v0.1. One document now answers all three questions: **what we are building, why those algorithms and not others, and how much data each one needs.**

Companion to `docs/ROADMAP_1.5.md` (§2.1 settles the model shape) and `docs/ROADMAP_SEED_RECONCILIATION.md`.

**Status of every figure:** the method is settled, the inputs are not. Assumptions are marked `[ASSUMPTION]` and each is a question for the Thursday review. Nothing marked that way should reach an investor before it is tested.

---

## 1. The claim, stated precisely

"Data is the moat" is too loose to build against and too loose to defend. The precise version:

> **The commodity layer is the journey. The scarce layer is the arrival.**
> We buy everything about getting there and we own everything about being there — because being there is what we are paid for.

What that rules out, deliberately:

| Not the claim | Why |
|---|---|
| We have a large model | Tabular machine learning on tens of thousands of rows. It trains in seconds on a laptop. Claiming otherwise invites a question we lose |
| We have more data than incumbents | We have less. An ERP vendor sits on thousands of sites |
| The data compounds across verticals | **The schema, the collection method and the solver compound. The weights do not** |
| The data is an accumulating asset | 12–24 month half-life. A subscription to presence, not a vault |

What survives all four: **we are the only party holding a resolved physical dock × the commercial context of the order × what happened afterwards.** A telematics vendor has position without orders. An ERP has orders without outcomes. A courier has trips without commercial context.

### How much data do we need before we can start? None.

All three founders agreed this on 13 September, and it has to lead, because everything below is about collecting data and reads otherwise.

| | What it needs | When |
|---|---|---|
| **Operating** — ingest, batch, hold, commingle, route, prove a delivery | Zero historical data | Day one |
| **A first number we can quote** — modelled, not defensible | M3 and M4 only, both computed rather than trained | Day one, from the order book |
| **A number that survives the customer arguing with it** | `EXP-1`, the randomised hold arm | Phase 2 |
| **The moat** — dwell we can promise against, urgency we can price, modality we can certify | M1, M2, M5 | Phase 2 onward |

The honest external sentence is **"we need no data to be useful and a lot of data to be undisplaceable"** — not "we need more data," which invites the reply that we should come back when we have it.

---

## 2. What the model actually does

> The model does not carry the parcel. It predicts whether an item can get from A to B inside a window, what that costs by each available means, and therefore which orders should ride together and on what.

Three decisions, and only three:

| Decision | Plain English | Fed by |
|---|---|---|
| **Batch / commingle** | Which orders ride together, and how long we hold before committing | `M3` computed · `M1` dwell |
| **Modality** | Which class of vehicle can carry this, to this dock | `M5` + rules |
| **Feasibility and cost** | Will it make the window, and what will it cost | `M1`, `M2`, `M4` |

Once the mechanism is chosen, the handoff happens and it goes its own way. **We are not accurate about driving. We are accurate about choosing.**

---

## 3. Architecture — a bought solver, fed by earned parameters

```
   bought                     earned                        bought
┌────────────┐        ┌──────────────────────┐        ┌────────────┐
│ travel time│───────▶│  five predictors      │───────▶│  solver    │
│ geocoding  │        │  (per vertical,       │        │  (Google   │
│ airspace   │        │   per customer)       │        │   Route    │
│ rates      │        └──────────────────────┘        │   Opt)     │
└────────────┘                    ▲                    └────────────┘
                                  │
                        ┌────────────────────────┐
                        │ the record             │
                        │ dock × order × outcome │
                        └────────────────────────┘
```

This is a three-layer shape — bought inputs, earned parameters, bought solver. Whether the earned middle should be trained predictors or an agent reading everything is a live question, raised in external review on 17 September. It is answered in **§14**, which should be read alongside this section.

**We never build a solver and we never learn a routing policy.** Routing is solved and cheap. What no API can know is how long *this* dock takes, whether *this* urgent flag is real, and which two orders could have ridden together.

---

## 4. The two exit KPIs

Everything in this document sizes off these. Both were named in the founder sync on the 15th and the split is the right one.

### KPI 1 — Model performance: does it choose correctly?

| Measure | Target | How it is measured | Measurable today? |
|---|---|---|---|
| **Modality recommendation accuracy** — of the orders we certify for a capacity class, the share that class could actually carry | False-positive rate ≤ 5% | 626 decisions with known outcomes per modality for ±2 points; 2,167 for ±1 point | **No** — needs an operator to have attempted them (`SUP-5`) |
| **Dwell promise coverage** — when we promise p90, does 90% land inside | Stated coverage holds on **unseen** docks | 900 conformal calibration points for 95% confidence | **No** — needs the second-precision sensor (`DRV-1`) |
| **Batch decision precision** — of the batches we formed, the share that beat solo routing | `[ASSUMPTION]` ≥ 90% | Re-solve each formed batch against its solo counterfactual | Partly — offline, on historical orders |
| **Urgency calibration** — when we say 20% chance of a consequence, does it happen 20% of the time | Brier score and a reliability curve, not accuracy | **626 observed consequences** for ±2 points — ~78,000 drops (§5, M2) | **No** — needs `EXP-1`, and the Y1 book does not reach it |

### KPI 2 — Efficiency: how much better does the operation get?

| Measure | Evidence today | How it is measured |
|---|---|---|
| **Cost per drop against a measured baseline** | Floor **$109,000 / 20%** (batching alone — software we have). Best case **$181,000 / 34%** (batching **+ pre-staging**, which requires the customer to change how his warehouse picks) | `EXP-1` control arm, 5–10% of orders dispatched as the customer would have |
| Stops per driver-hour | 1.88 measured → 2.38 at the floor → 2.81 at best case | Same |
| Share of orders commingled across customers | Zero today | Phase 4, gated on the signed pooling clause |

> **This target and the commercial model disagree, and the disagreement is unresolved.** The seed model puts Y1 customer savings at **12–19% across all segments**, below the 20% floor above. The floor was measured on one Regional-scale engagement where batching had room to work; the model's Y1 book is 93% Micro sites that buy third-party delivery today and have no dispatcher to out-perform. Either the floor is a Regional-only number and the Y1 target is 12–19%, or the model is under-claiming. **Settle it before either figure reaches a customer**, because `EXP-1` will measure whichever one we wrote down.

> **The number that needs re-deriving before it goes in a deck.** The same call committed to *"25 to 35%, at the minimum"* and separately assumed *"20% lift"*. 25–35% is the **ceiling**, and the top of it needs an operational change at the customer that is in no contract. The proposal made on that call is the right one: two free weeks, measure it, then set the number.

### The link between them — and the honest gap

The two KPIs are not independent, and **we cannot yet state the conversion between them.** That is the single largest hole in this brief. What we can state is its shape:

| Efficiency lever | Which model feeds it | What model error does |
|---|---|---|
| Batching uplift | `M1` dwell p90 · `M3` batch value | Over-predict dwell → hold too long, miss SLAs. Under-predict → decline batches we could have made. **The loss is asymmetric and the asymmetry is the thing to tune** |
| Modality substitution | `M5` eligibility | A false positive is a vehicle rolled for nothing — the operator's cost, not ours, which is why they will ask for the rate |
| Not paying for false urgency | `M2` calibration | One stop in six has a credible urgency argument at a customer claiming 100%. Mis-calibration here is paid for directly in carrier cost |

**The anchor that keeps this honest:** at the design partner, **50.2% of orders already get in-flight insertion by hand, and those reach customers faster** — 34-minute median against 40. The best human dispatcher is already good. The model's job is not to beat him; it is to **do what he does, consistently, at volume, without him.** Efficiency gain therefore comes mostly from covering the other 49.8% and from never having a bad day — not from out-thinking the best dispatcher on the floor.

`EXP-1` is what measures the conversion. Until it runs, any statement linking model accuracy to a savings percentage is modelled, not measured, and should be labelled that way.

---
## 5. The five predictors — what, why that algorithm, and how much data

### M1 — Dwell, per dock

| | |
|---|---|
| **Predicts** | Minutes at this location — p50 for planning, **p90 for what we can promise** |
| **Algorithm** | **LightGBM**, quantile objective, two models (α=0.5, α=0.9) |
| **Baseline it must beat** | Shrunk per-location quantile pooled toward node class. ~40 lines, already built |
| **Label** | `departed − arrived`, machine-generated by geofence. Never a tap |
| **Wrapped in** | Conformal prediction (MAPIE) before any figure reaches an SLA promise |

**Why this and not the alternatives**

| Option | Verdict |
|---|---|
| **Shrunk quantile lookup (the baseline)** | **Currently wins.** On our own harness it beat gradient boosting, and the p90 model covered 66.5% of cold-start cases after promising 90%. If it keeps winning, we ship it and say so |
| Linear / quantile regression | No interactions, no high-cardinality categoricals. Loses to the lookup |
| Random forest | No native quantile objective; quantile forests are slow at inference and worse calibrated |
| Neural network (MLP, TabNet, FT-Transformer) | Needs one to two orders of magnitude more rows. Below ~100k rows, boosted trees win on tabular data — this is well established and we have ~35k |
| **CatBoost** | The right tool for a thousands-of-levels categorical, and **vetoed** — see below |
| XGBoost | Has native categorical splits since 1.6, but **no ordered target-statistic encoding**, which was the entire reason to want CatBoost. No advantage over LightGBM here |
| **LightGBM** | **Chosen.** Already in `lmx-dwell/`, already wired to the causal priors, already the baseline's comparator |

> **Library veto — 13 September, binding.** CatBoost is a Yandex project. The veto on the call was flat: *"I can't. No, we can't, sorry."*
> **It costs no rework.** CatBoost was the intended upgrade, never the shipped choice — `requirements.txt` pins `lightgbm>=4.3` and nothing else.
> **The substitute named on the call was XGBoost; this brief overrides that** for the reason in the table.
> The veto itself is worth accepting without argument: a Russian-origin dependency inside a company selling to US enterprise distributors and holding autonomy partnerships is a procurement question we would rather not answer twice.

**What LightGBM costs us.** CatBoost's ordered target encoding handles `location_id` automatically and without leakage. LightGBM does not, so it is hand-rolled — and already is: `features.py` builds the receiver priors as expanding windows shifted by one, which is a causal target encoding by construction. **The one real gap:** those prior features are unshrunk, so a dock with three prior visits speaks as loudly as one with three hundred. `baseline.py` already does this correctly; the feature set has to catch up, which §7 rule (5) makes a release gate anyway.

**How much data**

| Target | Observations | At the design partner |
|---|---|---|
| A usable per-dock estimate | ~30 per dock, shrunk toward node class `[ASSUMPTION]` | 5–6 weeks |
| A promise we can stand behind — 90% coverage landing within ±2 points | **900 calibration points** (95% confidence). 225 gives the same band only ~68% of the time | Weeks, once the sensor exists |
| The same in the **thinnest** node class | 900 in the rarest of 7 classes, not 900 ÷ 7 | Set by class skew, not total volume |

> **A precision to get right before Thursday.** Split conformal's *marginal* guarantee is exact at any n ≥ 9 — there is no sample-size floor below which it breaks. What n buys is **training-conditional stability**: how far this particular calibration set's realised coverage wanders from 90%. At n = 225 it lands within ±2 points 68% of the time; at 900, 95%; at 1,825, 99.6%. Calling 225 a "floor" would be wrong.

`[ASSUMPTION]` The ~30-per-dock figure is inherited and derived nowhere. The repo's own baseline uses `prior_strength = 10.0`. One of the two is wrong.

**The blocker is not volume, it is the clock.** The dispatch export is minute-resolution, so **65.5% of stops compute to zero dwell.** The second-precision file proves those same stops really took 3–50 seconds. No filter recovers it. `DRV-1` and `DRV-2` are the prerequisite and both were gated on Phase 0.4 — now closed.

**M1b — censored dwell.** Failed and abandoned stops never produced a true dwell; we only know it exceeded some value. Ordinary regression either drops them or treats "gave up at 12 minutes" as "took 12 minutes," biasing estimates downward on exactly the worst docks. **XGBoost `survival:aft`** or `lifelines` — not because that problem has no categorical (it has the same one) but because `survival:aft` has no LightGBM equivalent, and M1b consumes the hand-rolled priors rather than raw `location_id`.

> **Known finding to design around.** Median dwell at the design partner is **2.1 minutes**, a third of stops under 60 seconds. At the national distributor it was ~13 minutes. **Not the same operation, and a pooled model is wrong about both.** Per-vertical models are forced by the data, not preferred.

### M2 — True urgency

| | |
|---|---|
| **Predicts** | P(a consequence occurs \| this order is late) |
| **Algorithm** | Gradient-boosted binary classifier + **isotonic calibration** |
| **Label** | Escalation call · credit issued · part returned · order cancelled · competitor-sourced · reorder gap — **or silence.** Silence when late is the label that says the flag was soft |
| **Requires** | The randomised hold arm (`EXP-1`). Unobtainable by watching |

**Why this and not the alternatives**

| Option | Verdict |
|---|---|
| The customer's own urgent flag | **Not a model, and wrong.** One stop in six had a credible urgency argument at a customer stating 100% are urgent |
| Logistic regression | Keep as the comparator — interpretable and hard to beat on few hundred events. Misses node-class × hour × order-value interactions |
| **GBM + isotonic calibration** | **Chosen**, conditional on volume |
| GBM + Platt (sigmoid) scaling | **Better below ~500 events.** Isotonic is non-parametric and needs the data to earn its flexibility. We expect 500–1,000, so this is genuinely marginal — **pick it on the calibration curve, not in advance** |
| Survival / time-to-consequence model | Richer, and overkill for a binary decision traded against cost |

**Why calibrated at all:** the output is traded against money in the solver. We need a probability, not a class. An uncalibrated classifier that is 80% accurate is useless if its 0.2 does not mean 0.2.

**How much data.** The band is 500–1,000 observed consequences, and the number inside it that matters is **626** — what a Wilson interval requires to size a ~5% false-alarm rate to ±2 percentage points, which is the tolerance KPI 1 is written to. The normal-approximation figure of 456 is wrong at that rate and should not be quoted anywhere.

`[ASSUMPTION]` ~10% of orders run late · ~1 in 6 has a credible urgency argument · ~half of consequences are observable.
→ **0.008 consequences per drop.** 626 consequences is therefore **~78,000 drops** — 62,500 at the bottom of the band, 125,000 at the top.

**In drops, by customer segment.** A **drop** is one delivery instance at one location. It is the unit the customer already counts and the denominator for `M1`, `M1b` and `M2` — though not, as §6 shows, for `M3` or `M5`.

| Segment | Drops/day | Drops/year (~250 op days) | Alone, to ~78,000 drops |
|---|---|---|---|
| Micro operator | 8 | 2,000 | **39 years** |
| Small branch | 50 | 12,500 | **6 years** |
| Regional distributor | 150 | 37,500 | **25 months** |
| National distributor | 400 | 100,000 | **9 months** |

The design partner, at ~141 drops/day, sits between the last two rows and reaches 626 consequences in about 27 months alone.

> **Against the seed model's Y1 book, `M2` is not measurable.** The plan is 25 Micro sites and 2 Small branches — **no Regional and no National account.** That is 300 drops a day and **75,000 drops in the year**, against a requirement of ~78,000. And the plan states 27 sites is its *exit* rate, not its entry rate, so the delivered figure is roughly half that. **Landing one Regional or National account is the difference between measuring urgency in Year 1 and not measuring it at all.** That is a sales decision with a modelling consequence, and it is the sharpest finding in this brief.

**Five partners each at full volume reach it in about seven months.** Note what that does *not* say: five partners at a fifth of the volume each is the same total volume and therefore the same elapsed time. The speedup comes from more total drops, not from spreading the same ones thinner. **`M2` wants volume, and volume means a Regional or National account.**

### M3 — Batch value — **not a model, on purpose**

Computed: solve the route with the order and without it, take the difference. **Predicting a quantity you can calculate exactly is strictly worse than calculating it** — you inherit the approximation error for nothing. A model here would be a solution looking for a problem.

What *is* learned is the **hold-window policy per node class**, and it is node-class-specific to an extreme degree: extending the hold from 45 to 90 minutes buys **+3–7% at high-frequency shops** and **+195–271% at warehouse and transfer nodes.** `IDN-3` is a modelling prerequisite, not metadata.

**How much data — and this is the one that changes the roadmap.** Commingling is a **pairwise** property. With *n* customers in one catchment there are *n(n−1)/2* pairs, and only a fraction produce a commingleable order on any day.

`[ASSUMPTION]` ~15% daily chance that any given pair has two orders that could ride together — an internal estimate from the 10th, and **the single most important number here to test.** Target ~200 observed events, on operating days (~21 a month).

| Customers in one catchment | Pairs | Events/day | Time to 200 |
|---|---|---|---|
| 2 | 1 | 0.15 | **64 months** |
| 3 | 3 | 0.45 | **21 months** |
| 5 | 10 | 1.5 | 6.4 months |
| **10** | **45** | **6.75** | **6 weeks** |
| 20 | 190 | 28.5 | 1.4 weeks |

The position taken on the call was blunt: *"three different customers is not enough… I need 10, 10 unique customers."* Three is twenty-one months; ten is six weeks. The curve is quadratic, so the first few accounts buy almost nothing and accounts five through ten buy nearly everything. **Customer count, not customer size, is the constraint on the central claim** — and they must be in the same catchment, or you get three small pair-counts rather than one large one.

**Three things this model gets wrong, said before anyone else says them:**

1. **It saturates around n = 20.** An order rides in one batch. Expected partners per customer per day is 0.15(n−1) — 2.85 at n = 20, which asserts the average order rides with three others against 6-stop routes. Beyond that the per-pair rate must fall and the curve goes linear. **Read nothing off it above 20.**
2. **Pairs are the right unit for "these two rode together," not for "holding 45 beat 90."** The hold-window policy learns from batching *decisions*, bounded by orders per day, so linear in *n*.
3. **"200 events ≈ 30 per node class" does not follow.** Class frequency is skewed and the binding number is the rarest class. Unresolved: a commingle event belongs to a *customer pair*, node class to a *dock* — we have not defined which dock's class such an event is attributed to.

4. **The 15% rate is calibrated on the wrong segment for the book we are actually planning.** It was estimated against Regional-scale order frequency. The seed model's Y1 book is 93% Micro sites at 8 drops a day, and two shops each releasing 8 orders a day will co-locate far less often than two branches each releasing 50. The *pair count* is excellent — 27 accounts is 351 pairings — but the per-pair *rate* is the term that moves, and it moves down. Measure it from historical order timestamps and geography before Phase 4 is scheduled against it.

**What it understates, in our favour:** near-misses are labels (pairs that shared a window and failed on capacity, geography or timing are informative negatives), and same-customer multi-order batching produces hold-window evidence today with no cross-customer pair at all.

### M4 — Trip cost against order value — **not a model**

Loaded driver rate × minutes + miles, against invoice value. A SQL view and a threshold. It surfaces the $5.88-seal-on-a-35-mile-round-trip class of decision on day one with no training at all. **Anything a join can answer should not be a model.**

### M5 — Modality fit

| | |
|---|---|
| **Predicts** | Which capacity classes can carry this order to this dock |
| **Algorithm** | Deterministic rules (weight, dimensions, hazmat) **+** a small classifier over dock geometry |
| **Label** | Dock survey: landing surface, curb access, door path, obstruction, who receives it |
| **Known distribution** | **55% of orders by count but only 31% of revenue** fall under 2.5 kg |

**Why rules first, and not a model for the whole thing**

| Option | Verdict |
|---|---|
| **Rules for the physical constraints** | **Chosen.** Auditable to an operator — they will ask "why did you send me this?" and a rule answers. Amendable on evidence: three failures at a threshold changes the rule. A classifier can do neither |
| Small classifier over dock geometry only | **Chosen for that part**, where rules are brittle — access, approach, who receives it |
| One classifier for everything | Rejected. Weight and hazmat are hard constraints, not probabilities. A model that occasionally certifies a 40 kg parcel for a drone is worse than useless |
| Computer vision on dock photos | Attractive later; needs thousands of labelled images and has an unbounded failure mode. Not now |

**How much data — two numbers, and the second is uncosted**

| | Requirement |
|---|---|
| **To build** the rule set | ~200 surveyed docks — **fieldwork, 6–8 weeks**, not a function of delivery volume |
| **To prove** a false-positive rate of ~5% to ±2 points | **626 decisions with known outcomes per modality** → ~2,500 across four classes |
| The same to ±1 point | 2,167 per modality → ~8,700 total |

> **Do not use the textbook formula.** The normal-approximation (Wald) sample size gives 456, and 456 does not deliver ±2 points at a 5% rate — its true Wilson interval is 3.35%–7.40%, an upper half-width of 2.4 points. Wald is anti-conservative in exactly the small-proportion regime and fails on the side that matters. The honest numbers are **626** and **2,167**.
>
> **And ±2 points on a 5% rate is ±40% relative error.** An operator cares whether their false-positive rate is 5% or 7% — a 40% swing in wasted vehicle movements. If their threshold is relative, the ±1-point row is the requirement. Ask them rather than assume. Both numbers are conditional on the rate being 5%; at 10% the requirement roughly doubles.

**The hard gate.** An eligibility *outcome* exists only once an operator has attempted the delivery and succeeded or refused. Until `SUP-5`, the false-positive rate is **not measurable at any data volume.** We can build the instrument and cannot score it alone. It is unblocked by a phone call, not by a quarter of collection.

### Where a language model belongs — and does not

**Yes:** parsing messy order text and dispatcher notes into structured fields · normalising addresses and receiver names to stable entity IDs · drafting exception explanations for a dispatcher.

**No:** the policy engine. Our own advisor's words — *use it to spec and build it; don't use it to run it.* At these volumes a boosted tree is better, faster and free per inference.

---

## 6. The structural finding: there is no single "data per delivery"

The founder sync asked for one number. The five predictors accrue in **four different units**, only one of which is the delivery, and one of them **stops accruing altogether.**

| Predictor | Accrues per… | Scales with | Yield per delivery |
|---|---|---|---|
| `M1` dwell | stop | deliveries, without limit | ~0.97 |
| `M1b` censored dwell | abandoned stop | deliveries, without limit | ~0.03 `[ASSUMPTION]` |
| `M2` urgency | observed consequence | deliveries, without limit | ~0.008 |
| `M3` batch policy | cross-customer pair sharing a window | **customers², not deliveries** | see §5 |
| `M5` modality fit | dock, surveyed once | **the customer's dock count — it saturates** | n/a |

**(a) The label the investor story rests on does not accrue with volume at all.** A dock is surveyed once. The design partner has ~229. Once surveyed, more deliveries to that customer produce **zero** new eligibility data. Modelling `M5` as a per-delivery rate is wrong in both directions — it overstates the long run and understates the first month. `M5` is a **discovery process with a hard ceiling at the customer's dock count**, and the survey is weeks of fieldwork regardless of volume.

**That is the argument for breadth, and being a ceiling argument rather than a rate argument makes it stronger.** Doubling volume at one customer buys more dwell and more urgency and not one additional dock.

**(b) The scarce labels are scarce by two orders of magnitude.** Dwell arrives on essentially every stop; an urgency consequence on about one stop in 120. A year at one customer gives ~34,000 dwell observations and ~290 urgency consequences.

**(c) The two scarce labels pull in opposite directions, and the resolution is a metro, not a customer.** `M2` is priced in drops and `M3` is priced in accounts, and no single customer satisfies both. A Micro operator never reaches urgency — 39 years alone. A National distributor reaches it in nine months and produces, on its own, **zero** cross-customer commingling events at any volume, because commingling is two *different* customers' freight meeting at one dock.

The shape that satisfies both is one metropolitan area holding one Regional distributor, two Small branches and ten Micro operators:

| | |
|---|---|
| Drops per day | **~330** (150 + 2×50 + 10×8) |
| Accounts in catchment | 13, giving 78 pairs |
| `M2` urgency, to ~78,000 drops | **~11 months** |
| `M3` commingling, to 200 events | **~17 operating days** |
| `M5` eligibility | 13 dock estates in reach, not one |

**That is a go-to-market instruction disguised as a data requirement**, and it is the most consequential line in this document: the account mix has to be chosen by what it teaches, not only by what it bills.

**The seed model does not plan that mix.** Its Y1 book is 25 Micro and 2 Small branches — 300 drops a day against the 330 above, which looks close and is not. The drops are there; the *concentration* is not, and `M2` needs one account with volume rather than twenty-seven without it.

| | Recommended above | Seed model, Y1 |
|---|---|---|
| Accounts | 13 | 27 |
| Drops/day | ~330 | ~300 |
| Largest single account | Regional, 150/day | Small branch, 50/day |
| `M2` urgency | ~11 months | **Not reachable in Y1** |
| `M3` pairings | 78 | 351 — if they share a catchment |
| `M5` docks in reach | ~500 | **~620 across 27 estates** |

So the planned book is close to optimal for `M3`, the strongest available for `M5`, and the worst available for `M2`. **Whether Year 1 buys us urgency is decided by whether one Regional account signs.**

The model is also silent on geography, and every `M3` figure here assumes a single catchment. Twenty-seven accounts spread across five metros is five small pair-counts, not one large one — which is the same density constraint that killed 1.0, arriving in a new costume.

---

## 7. How we train

Five rules. Each is a release gate, not a guideline.

**(1) Causal features only.** Every historical feature is an expanding window shifted by one:

```python
df["recv_prior_mean_dwell"] = (
    df.groupby("location_id")["dwell_min"]
      .transform(lambda s: s.shift(1).expanding().mean()))
```

Computing a location's average over the whole file leaks the answer into the question. You get a beautiful validation score and a model that collapses in production. The most common quiet failure in logistics ML, and we treat it as a build-breaking bug.

**(2) Split by time, and separately hold out whole locations.** A random split puts the same dock on both sides and lies. Chronological split for the headline number; **entire unseen locations** for the cold-start number, which is what a new customer experiences in week one.

**(3) Beat the baseline on *both* populations, or ship the baseline.** On our own harness the shrinkage baseline beat gradient boosting, and the p90 model covered **66.5%** of cold-start cases after promising 90%. Knowing that before it reaches a customer is the job.

**(4) Conformal intervals before any promise.** A point estimate with a hopeful p90 is not an SLA. A distribution-free coverage guarantee is.

**(5) Cold start is in the schema from commit one.** Hierarchical pooling — a new dock inherits a prior from its node class — is painful to retrofit and decides whether "it gets better with scale" is true at the moment a prospect is watching.

**Retraining.** Per vertical, per customer where volume allows, on a schedule plus a drift trigger. The reference table updates continuously; the models do not need to.

---

## 8. The label economy

The most useful way to think about collection. Labels sort by what they cost us:

| Class | Labels | Cost | How we get them |
|---|---|---|---|
| **Free** | Dwell · arrival · exception type · actual cost · batchability | **Zero — the delivery produces them** | `DRV-1` geofence sensor |
| **Cheap** | Consequence of lateness | One SMS | One question after a late delivery. The cheapest acquisition of the hardest label here |
| **Expensive** | The counterfactual · true urgency | A slice of orders done the old way | `EXP-1`, 5–10%, in the contract before the code |
| **Bought** | Travel time · airspace · rates · weather | Money | Commodity APIs |

> **This table is the business model.** Every other company in this category has to move labels from "expensive" to "free" by spending. We are paid to be in the place where they are already free. That is the whole argument, and it is one row wide.

---

## 9. Data collection — what we do, in order

**Already held (no collection needed).** 6,715 stops, 13 drivers, 229 receivers, 2 Jan – 6 Apr 2026, full order lifecycle · 1,451 stops at true second precision, one driver, 54 manifests · 17,565 invoices, median order value $74 · 57 ride-along stops with per-stop invoice value and return codes.

**Enough today for M3, M4 and the baselines. Not enough for M1, and nothing for M2.**

> **Stop counts differ between sources and are not reconciled.** That is `REC-5`, and every figure in this document inherits it.

**The precision problem, and why the sensor is first.** The main export is minute-resolution, so **65.5% of stops compute to exactly zero dwell.** The second-precision file proves those same stops took 3–50 seconds. Not corrupt — rounding destroyed them, and no filter recovers it. `DRV-1` ships before any dwell model: geofence arrive/depart, machine-generated, with **rolling registration**, because iOS monitors a maximum of 20 regions and the best driver runs 20.2 stops per route.

**Identity before anything (`IDN-1..4`, now built).** 230 account IDs → 224 names → an unknown smaller number of physical docks. Five records for one body shop across two ID roots; one shop twice with its city spelled with zeros for O's; one record with city, state and postcode literally `N/A`. Every per-dock estimate is computed on a fraction of that dock's history until this is fixed, and it has already put a withdrawn figure in investor materials.

**The dock survey.** Ten tapped fields per stop, no typing: place type · where you could stop · walk distance · who took it · wait before handoff · access barriers · carry effort · weight band · size band · goods category. Plus an optional photo. **Collect the autonomy-fit columns in the first migration**, thirty weeks before `SUP-3` needs them — they cost nothing now and are expensive to backfill, because you would have to revisit every dock.

**Onboarding is a collection problem too.** Every figure above assumes the order feed is flowing. Getting there is currently a per-customer integration, and the Y1 book is 27 of them — 25 Micro sites at $149 a month, where weeks of integration engineering never amortises. **The collection plan is only as real as the onboarding path**, which is the argument §14 makes for agentic ingestion.

### The control arm (`EXP-1`) 5–10% of orders dispatched exactly as the customer would have. It produces the counterfactual, the true-urgency labels and the defensible savings number simultaneously. **It goes in the contract before it goes in the code** — an arm discovered rather than disclosed looks like negligence.

---

## 10. What compounds, and the honest limits

**Compounds:** the schema · the collection method · the solver · the per-metro dock reference table, while we keep delivering there.

**Does not compound:** model weights across verticals. Dock knowledge across metros. Anything we stop visiting.

Four limits to state before someone else does:

1. **A flow, not a stock.** Gate codes change, shops move, docks get rebuilt. 12–24 month half-life. Survivable because we are paid to be present — fatal to describe as accumulated.
2. **It does not travel.** A dock atlas for one metro is worth nothing in the next. The moat is won metro by metro, which is the same density constraint that killed 1.0.
3. **Coverage beats depth, and we will have depth.** 230 docks known intimately in a quarter is a pilot. An autonomy operator needs thousands.
4. **Nothing stops an incumbent doing this.** An ERP vendor sits on thousands of distributor sites and could instrument them tomorrow. The barrier is that **their revenue does not depend on the label being right** — an incentive argument, not a technical one. Say so.

**The claim has a test, and the test is `PRD-8`.** "It compounds" is an assertion until a model trained on one customer is evaluated on another, and on another vertical, with the result reported either way. Until it runs, the defensible sentence is: *we have built the collection method and the schema; whether the learning transfers is measured, not assumed.*

---

## 11. What this says the roadmap needs

| Need | Implies | Currently |
|---|---|---|
| 10 customers in **one** catchment | Account count is the Phase 4 gate, not volume | No customer-count gate exists |
| Eligibility outcomes | One operator agreement before the false-positive rate is measurable | `SUP-5`, Phase 5 — and it costs a call |
| Second-precision dwell | `DRV-1`/`DRV-2` before any dwell number is real | Unblocked — 0.4 and 0.8 closed |
| A measured false-positive rate | A build item that does not exist | Proposed as `SUP-6` |
| One definition of a stop | Three sources report three different counts | `REC-5`, unbuilt |

---

## 12. Build order

| Phase | What | Unblocks |
|---|---|---|
| **1** | `IDN-1..4` identity · `DRV-1..2` sensor · `REC-1..5` record · **`AGT-1` resolution bake-off** | Everything, and the agent question (§14) |
| **2** | `EXP-1..3` control arm · M3, M4 · baselines and harness | **KPI 2** — the measured number |
| **3** | M1 dwell + conformal · M1b censoring · override capture | Live authority |
| **4** | `PRD-8` transfer evaluation | The compounding claim |
| **5** | M5 modality · payload dataset · `SUP-5` operator adapter | **KPI 1** — and the supply side |

M2 sits between 2 and 3: the arm starts in 2, the classifier is trainable ~8 weeks later.

`AGT-2` (agentic order-feed mapping) is scheduled by `AGT-1`'s result, not ahead of it — but if it lands, it moves into Phase 1, because §14 argues it is a precondition for the Micro segment rather than an optimisation of it.

**Nothing here needs a GPU, a model server or a feature store.** The artefact is a few-megabyte file loaded in-process. The hard parts are the sensor, the identity layer and the experiment design — none of which is a research problem.

---

## 13. Open — the Thursday agenda

1. **Is 15% the right pair-commingle rate?** Everything in `M3`'s sizing moves with it, and it is almost certainly not homogeneous — it is a function of each customer's order frequency, so it will sit below 15% at small *n* and above it at large *n*. Measurable today from historical order timestamps and geography, with no new collection. **First thing to run.**
2. **Is ~30 observations per dock right?** Nothing derives it, and the repo's own baseline uses 10.
3. **Does an accuracy target or a cost-of-error target drive the sizing?** A false positive on a drone costs a wasted flight; a false negative costs a lost batching opportunity. Not symmetric, and the sizing should probably follow the expensive one.
4. **Isotonic or Platt for M2?** Genuinely marginal at 500–1,000 events. Decide on the calibration curve, not in advance.
5. **What is the transfer function between KPI 1 and KPI 2?** We can state its shape and not its coefficients. Is there a defensible way to estimate them before `EXP-1` runs, or do we simply say "measured, not modelled" and wait?
6. **What is the cheapest defensible substitute for waiting?** Simulation gives mechanism coverage, not label truth. Where is that an honest bridge and where is it circular?

---

*Sources: the 13 and 15 September syncs · `docs/ROADMAP_1.5.md` · `docs/ROADMAP_SEED_RECONCILIATION.md` · the design partner's dispatch export (13 drivers, 229 receivers, 2 Jan – 6 Apr 2026) and the separate second-precision file. Sizing: Wilson score interval for a proportion; split-conformal training-conditional coverage from the exact Beta(n+1−⌊(n+1)α⌋, ⌊(n+1)α⌋) distribution.*

---

## 14. Where agents belong, and where they do not

Raised in external review, 17 September: that model-selection-then-training is a traditional shape for this problem, and that headless agents reading across all available datasets might get to *"the best way to deliver this package"* faster than a set of trained predictors feeding a solver.

Taken seriously, the challenge is **right about three layers of this system and wrong about two**, and the split is not a matter of taste — it follows from what kind of question each layer asks.

### The test

Three properties decide it for any layer:

1. **Is the input unstructured and variable?** If yes, an agent wins — that is the thing language models do that nothing else does.
2. **Does the output get traded against money?** If yes, it needs calibration, and an agent does not produce calibration.
3. **Is an exact answer computable?** If yes, neither an agent nor a model belongs there. Compute it.

### The layers

| Layer | The question it asks | Unstructured in? | Traded against money? | Exact answer available? | Verdict |
|---|---|---|---|---|---|
| Order-feed ingestion | What does this customer's export mean? | Yes | No | No | **Agent** |
| Identity resolution (`IDN`) | Are these records the same physical dock? | Yes | No | No | **Agent** |
| Dock survey capture | What is this dock like? | Yes (photo, free text) | No | No | **Agent-assisted** |
| Cold-start prior | What to expect at a dock we have never visited? | Yes | Indirectly | No | **Agent, bounded** |
| `M1` dwell | How long will this stop hold the driver? | No | Yes — it is the SLA promise | No | **Model** |
| `M2` urgency | Probability of a consequence if late? | No | Yes, directly | No | **Model, calibrated** |
| `M3` batch value | Is this batch better than solo? | No | Yes | **Yes** | **Compute** |
| `M4` trip cost | Does this trip earn its keep? | No | Yes | **Yes** | **Compute** |
| Operator explanation | Why did we hold this order 18 minutes? | — | No | No | **Agent** |

In one line: **agents at the edges, where data enters and decisions are explained; models in the middle, where money is traded; arithmetic wherever arithmetic suffices.**

### The strongest case for agents is commercial, not technical

The Y1 book is 27 sites, 25 of them Micro at $149 per month (§6c). A hand-built order-feed integration is weeks of engineering; twenty-five of them is not a plan, and at $149 a month it never amortises.

So agentic ingestion is **not an efficiency gain on the Micro segment — it is the precondition for that segment existing.** And the Micro cluster is exactly what `M3` commingling depends on (§5 M3, §6c). The agent question and the Year 1 data question turn out to be the same question, which is a stronger form of the challenge than the one that was put to us.

`[ASSUMPTION]` a hand-built integration is 2–4 engineering weeks per customer; agentic mapping with a human review gate brings it under a day. If that holds, it is worth more than any modelling decision in this document.

### Why the decision path stays model-and-solver

Three reasons, in order of how well they hold up:

**Latency.** The batch-hold decision runs on a ~5 second solve budget and evaluates many candidate pairings. Serial agent reasoning over candidates does not fit that envelope.

**Auditability.** The first time a customer argues with a savings figure, we have to show why an order was held. A gradient-boosted model with a calibration curve is inspectable and reproducible. An agent's reasoning trace is not evidence.

**Cost — real, but shrinking, and not the argument to lead with.** At Y1 (~330 drops/day, ~20 candidate evaluations each) a model call per evaluation runs roughly $7,000 a year against ~$75,000 of allocation-fee revenue: about 10%. By Y5 the same arithmetic is nearer 1.5% of revenue. Cost is a Year 1 objection, not a structural one. We should say so rather than overstate it.

### On not training from scratch — the version worth testing

The advice does not transfer directly: LightGBM on our volumes trains in seconds (§7), so training time is not a cost we are paying, and there is no pre-trained dwell model for thousands of docks at ~15 observations each.

There is a real form of the point. **Tabular and time-series foundation models** — TabPFN for small-sample tabular, Chronos and TimesFM for time series — are pre-trained and given context at inference, and small-sample tabular is precisely the shape of per-dock dwell. That is a benchmark to run against the shrunk baseline under §7 rule (3), not a posture to adopt.

### `AGT-1` — how this gets decided

Not by argument. `IDN-1..4` is already built and merged, which means we have both a working implementation and a task with a checkable answer.

**The resolution bake-off.** Hand-label the true physical dock count behind the 230 account IDs, once. Run the existing deterministic resolver and an agent resolver on identical input. Compare precision and recall on merge decisions, and count the cases each gets right that the other does not. One engineer, a few days.

If the agent wins on the messiest real problem we have, the ingestion layer gets rewritten with evidence behind it and `AGT-2` moves into Phase 1. If it loses, we have a measured answer for the next review rather than an opinion.

### What does not change, whatever `AGT-1` says

`M2` still needs 626 observed consequences and the control arm. `M3` still needs neighbouring accounts in one catchment. The Y1 book still cannot reach urgency (§5 M2). **No agent produces a label that was never recorded** — and that is the part of this brief the challenge does not reach.
