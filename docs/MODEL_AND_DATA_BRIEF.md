# LMX 1.5 — Models, Training and Data Collection

**v1.2 · 14 September 2026 · Sourabh**

*v1.2 reconciles this brief with the founder sync transcripts (22 Jul – 13 Sep). Two changes: M1's library is swapped after a co-founder veto (§3), and the "how much data do we need" question is answered against the 13 September three-way agreement (§1).*

Companion to `docs/ROADMAP_1.5.md` (§2.1 settles the model shape) and `docs/ROADMAP_RECONCILIATION.md`. Supersedes the framing in `10-lmx-1.5-dataset-design.md`, which was written against the four-line revenue model.

---

## 1. The claim, stated precisely

"Data is the moat" is too loose to build against and too loose to defend. The precise version:

> **The commodity layer is the journey. The scarce layer is the arrival.**
> We buy everything about getting there and we own everything about being there — because being there is what we are paid for.

What that rules out, deliberately:

| Not the claim | Why |
|---|---|
| We have a large model | This is tabular machine learning on tens of thousands of rows. It trains in seconds on a laptop. Claiming otherwise invites a question we lose. |
| We have more data than incumbents | We have less. An ERP vendor sits on thousands of sites. |
| The data compounds across verticals | **The schema, the collection method and the solver compound. The weights do not.** (§2.1) |
| The data is an accumulating asset | It has a 12–24 month half-life. It is a subscription to presence, not a vault. |

What survives all four: **we are the only party holding a resolved physical dock × the commercial context of the order × what happened afterwards.** A telematics vendor has position without orders. An ERP has orders without outcomes. A courier has trips without commercial context.

### How much data do we need before we can start? None.

This has to be said plainly, because everything that follows is about collecting data and it reads otherwise.

On **13 September all three founders agreed that zero data is required to begin operating.** Richard's words: *"We don't need any data to get started. We just need exactly what we said we needed for LMX 1.0 — ingest an order, batch it with another one, hold it for 18 minutes, commingle across two orders, and build a route."* Sourabh had argued the same thing. Matan tested whether the two positions differed and confirmed they did not.

That agreement and this brief are not in conflict, and the distinction is the whole point:

| | What it needs | When |
|---|---|---|
| **Operating** — ingest, batch, hold, commingle, route, prove a delivery | Zero historical data. A solver, a hold window and an address | Day one |
| **A first number we can quote** — modelled, not yet defensible | M3 and M4 only — both computed, not trained | Day one, from the order book |
| **A number that survives the customer arguing with it** | `EXP-1`, the randomised hold arm — the counterfactual is an Expensive label (§5) | Phase 2 |
| **The moat** — dwell we can promise against, urgency we can price, modality we can certify | M1, M2, M5. Weeks to months of collection | Phase 2 onward |

So the honest external sentence is **"we need no data to be useful and a lot of data to be undisplaceable"** — not "we need more data," which invites the obvious reply that we should come back when we have it.

---

## 2. Architecture — a bought solver, fed by earned parameters

Our own dispatch work already settled this: *the batch-hold timer is a discrete optimisation under hard SLA constraints, not a routing problem and not a machine-learning problem.*

```
   bought                     earned                        bought
┌────────────┐        ┌──────────────────────┐        ┌────────────┐
│ travel time│───────▶│  five predictors      │───────▶│  solver    │
│ geocoding  │        │  (per vertical,       │        │  (Google   │
│ airspace   │        │   per customer)       │        │   Route    │
│ rates      │        └──────────────────────┘        │   Opt)     │
└────────────┘                    ▲                    └────────────┘
                                  │
                        ┌─────────────────────┐
                        │ the record          │
                        │ dock × order × outcome │
                        └─────────────────────┘
```

**We never build a solver and we never learn a routing policy.** Routing is solved and cheap. What no API can know is how long *this* dock takes, whether *this* urgent flag is real, and which two orders could have ridden together.

---

## 3. The five predictors

### M1 — Dwell, per dock

| | |
|---|---|
| **Predicts** | Minutes at this location — p50 for planning, **p90 for what we can promise** |
| **Algorithm** | **LightGBM**, quantile objective (`objective="quantile"`), two models (α=0.5, α=0.9) |
| **Why LightGBM, and what it costs** | LightGBM is what `lmx-dwell/` already runs. CatBoost was the intended upgrade and is **vetoed** — see the box below. The highest-signal feature is `location_id`: thousands of categories, few rows each. CatBoost's ordered target encoding handles that case automatically and without leakage. LightGBM does not, so the encoding has to be hand-rolled — and **it already is**: `lmx-dwell/dwell/features.py` builds `recv_prior_mean_dwell` and `recv_prior_p90_dwell` as expanding windows shifted by one, a causal target encoding by construction, and `train.py` passes `categorical_feature=CATEGORICAL` natively with `cat_smooth=20.0`. **The one real gap:** those prior *features* are unshrunk, so a receiver with three prior visits speaks as loudly as one with three hundred. `baseline.py` already does this correctly (`ShrunkQuantileBaseline`, pooling toward node class) — the feature set has to catch up, which §4 rule (5) makes a release gate anyway. On the substitute named on the call, see the box |
| **Baseline it must beat** | Shrunk per-location quantile with empirical-Bayes pooling toward node class. ~40 lines |
| **Label** | `departed − arrived`, machine-generated by geofence. Never a tap |
| **Wrapped in** | Conformal prediction (MAPIE) before any figure reaches an SLA promise — a coverage guarantee, not a hope |

> **Library veto — 13 September 2026, and it is binding.**
> CatBoost is a Yandex project. Matan, on the call: *"I can't. No, we can't, sorry."*
>
> **The veto costs no rework.** CatBoost was the intended *upgrade*; it was never the shipped choice. `lmx-dwell/requirements.txt` pins `lightgbm>=4.3` and nothing else, and `baseline.py`'s own docstring reads *"the baseline LightGBM has to beat."* So there is no migration — only the upgrade we now will not make.
>
> **The substitute named on the call was XGBoost, and this brief overrides that.** XGBoost does have native categorical splits (`enable_categorical`, since 1.6), so the objection is not that it cannot take the feature. The objection is that it has **no ordered target-statistic encoding** — which is the one thing CatBoost offers on a thousands-of-levels column like `location_id` and the entire reason it was wanted. LightGBM is no better on that axis, but it is already in the repo, already wired to the causal priors, and already the baseline's comparator. Changing to XGBoost would be churn for nothing.
>
> The veto is worth accepting without argument. A Russian-origin dependency inside a company that intends to sell to US enterprise distributors and hold autonomy partnerships is a procurement question we would rather not answer twice.
>
> **XGBoost stays correct for M1b** — not because that problem has no categorical (it has the same one), but because `survival:aft` has no LightGBM equivalent and M1b consumes the hand-rolled prior features rather than raw `location_id`.

**M1b — censored dwell.** Failed and abandoned stops never produced a true dwell; we only know it exceeded some value. Ordinary regression either drops them or treats "gave up at 12 minutes" as "took 12 minutes," biasing estimates downward on exactly the worst docks. Use **XGBoost `survival:aft`** or `lifelines`. The national distributor's logs are full of these.

> **Known finding to design around.** Median dwell at the design partner is **2.1 minutes**, a third of stops under 60 seconds. At the national distributor it was ~13 minutes. **These are not the same operation and a pooled model is wrong about both.** Per-vertical models are not a preference here, they are forced by the data.

### M2 — True urgency

| | |
|---|---|
| **Predicts** | P(a consequence occurs \| this order is late) |
| **Algorithm** | Gradient-boosted binary classifier + **isotonic calibration** (`CalibratedClassifierCV`) |
| **Why calibrated** | The output is traded against cost in the solver. We need a probability, not a class |
| **Label** | **The hard one.** Escalation call · credit issued · part returned · order cancelled · competitor-sourced · reorder gap — **or silence.** Silence when late is the label that says the flag was soft |
| **Requires** | The randomised hold arm (`EXP-1`). Unobtainable by watching |

Field evidence for why this is worth the trouble: on one full day's manifest, **one stop in six** had a credible urgency argument — at a customer who states that 100% of his deliveries are urgent.

### M3 — Batch value — **not a model**

Computed, not predicted: solve the route with the order and without it, take the difference. Predicting it is strictly worse than computing it.

What *is* learned is the **hold-window policy per node class**, and the evidence says it is node-class-specific to an extreme degree: extending the hold from 45 to 90 minutes buys **+3–7% at high-frequency shops** and **+195–271% at warehouse and transfer nodes.** `IDN-3` (node class) is therefore a modelling prerequisite, not metadata.

### M4 — Trip cost against order value — **not a model**

Loaded driver rate × minutes + miles, against invoice value. A SQL view and a threshold. It surfaces the $5.88-seal-on-a-35-mile-round-trip class of decision on day one, with no training at all.

### M5 — Modality fit

| | |
|---|---|
| **Predicts** | Which capacity classes can carry this order to this dock |
| **Algorithm** | Deterministic rules (weight, dimensions, hazmat) **+** a small classifier over dock geometry |
| **Label** | Dock survey fields: landing surface, curb access, door path, obstruction, who receives it |
| **Known distribution** | **55% of orders by count but only 31% of revenue** fall under 2.5 kg. The heavy 31% of orders carry 69% of the revenue |

This is the analysis a drone operator asked for and could get nowhere. It is a data product before it is a routing feature.

### Where a language model belongs — and does not

**Yes:** parsing messy order text and dispatcher notes into structured fields · normalising addresses and receiver names to stable entity IDs · drafting exception explanations for a dispatcher.

**No:** the policy engine. Our own advisor's words — *use it to spec and build it; don't use it to run it.* At these volumes a boosted tree is better, faster and free per inference.

---

## 4. How we train

Five rules. Each one is a release gate, not a guideline.

**(1) Causal features only.** Every historical feature is an expanding window shifted by one:

```python
df["recv_prior_mean_dwell"] = (
    df.groupby("location_id")["dwell_min"]
      .transform(lambda s: s.shift(1).expanding().mean()))
```

Computing a location's average over the whole file leaks the answer into the question. You get a beautiful validation score and a model that collapses in production. This is the most common quiet failure in logistics ML and we treat it as a build-breaking bug.

**(2) Split by time, and separately hold out whole locations.** A random split puts the same dock on both sides and lies. Chronological split for the headline number; **entire unseen locations** for the cold-start number — which is what a new customer actually experiences in week one.

**(3) Beat the baseline on *both* populations, or ship the baseline.** On our own harness the shrinkage baseline beat gradient boosting, and the p90 model covered **66.5%** of cold-start cases after promising 90%. Knowing that before it reaches a customer is the job.

**(4) Conformal intervals before any promise.** A point estimate with a hopeful p90 is not an SLA. A distribution-free coverage guarantee is.

**(5) Cold start is in the schema from commit one.** Hierarchical pooling — a new dock inherits a prior from its node class — is painful to retrofit and decides whether "it gets better with scale" is true at the moment a prospect is watching.

**Retraining.** Per vertical, per customer where volume allows, on a schedule plus a drift trigger. The reference table updates continuously; the models do not need to.

---

## 5. The label economy

The most useful way to think about collection. Labels sort into four classes by what they cost us:

| Class | Labels | Cost | How we get them |
|---|---|---|---|
| **Free** | Dwell · arrival · exception type · actual cost · batchability | **Zero — the delivery produces them** | `DRV-1` geofence sensor |
| **Cheap** | Consequence of lateness | One SMS | One question after a late delivery. **The cheapest acquisition of the hardest label on this list** |
| **Expensive** | The counterfactual · true urgency | A slice of orders done the old way | `EXP-1` control arm, 5–10%, in the contract before the code |
| **Bought** | Travel time · airspace · rates · weather | Money | Commodity APIs |

> **This table is the business model.** Every other company in this category has to move labels from "expensive" to "free" by spending. We are paid to be in the place where they are already free. That is the whole argument, and it is one row wide.

---

## 6. Data collection — what we do, in order

### Already held (no collection needed)

- **6,715 stops**, 13 drivers, 229 receivers, 2 Jan – 6 Apr 2026, with full order lifecycle (created → dispatched → arrived → departed)
- **1,451 stops at true second precision**, one driver, 54 manifests
- **17,565 invoices**, median order value $74
- 57 ride-along stops with per-stop invoice value, return codes and cross-branch source

**Enough today for M3, M4 and the baselines.** Not enough for M1, and nothing for M2.

### The precision problem, and why the sensor is first

The main export is **minute-resolution only**, so **65.5% of stops compute to exactly zero dwell.** The second-precision file proves those same stops really took 3–50 seconds. They are not corrupt; rounding destroyed them, and **no filter recovers it.**

`DRV-1` therefore ships before any dwell model: geofence arrive/depart, machine-generated, with **rolling registration** — iOS monitors a maximum of 20 regions and our design partner's best driver runs 20.2 stops per route.

### Identity before anything (`IDN-1..4`)

**230 account IDs → 224 names → an unknown smaller number of physical docks.** Five records for one body shop across two ID roots; one shop twice with its city spelled with zeros for O's; one record with city, state and postcode literally `N/A`.

Every per-dock estimate is computed on a fraction of that dock's history until this is fixed, and it has already put a withdrawn figure in investor materials.

### The dock survey

Ten tapped fields per stop, no typing: place type · where you could stop · walk distance · who took it · wait before handoff · access barriers · carry effort · weight band · size band · goods category. Plus an optional photo.

**Collect the autonomy-fit columns in the first migration**, thirty weeks before `SUP-3` needs them. They cost nothing now and are expensive to backfill — you would have to revisit every dock.

### The control arm (`EXP-1`)

5–10% of orders dispatched exactly as the customer would have. It produces the counterfactual, the true-urgency labels, and the defensible savings number simultaneously. **It goes in the contract before it goes in the code** — an arm discovered rather than disclosed looks like negligence.

### Volume and time to a usable model

At the design partner: ~141 stops/day, ~250 operating days, ~229 receivers → **~35,000 stops/year, ~150 per receiver.**

| Model | Needs | One partner | Five partners |
|---|---|---|---|
| M1 dwell (pooled prior) | ~30 obs per dock | 5–6 weeks | 1–2 weeks |
| M2 urgency | 500–1,000 observed consequences | **21–41 months** | ~7 months (five partners at full volume) |
| M3 batch value | Historical pairs | Available now | — |
| M4 cost vs value | One order book | Available now | — |
| M5 modality fit | SKU master + ~200 docks surveyed | 6–8 weeks | 3 weeks |

**Breadth beats depth — for two different reasons, which should not be conflated.** For `M2` the gain is simply more total deliveries: five partners *at full volume each* see five times the exception events. For `M5` the gain is structural — dock knowledge **saturates** at one customer's dock count, so volume at an existing customer buys none of it. Five partners at a *fifth* of the volume each is the same total volume and buys no `M2` speedup at all. See `docs/DATA_NEED_BRIEF.md` §3–§4, which derives these and supersedes the M2 row above.

---

## 7. What compounds, and the honest limits

**Compounds:** the schema · the collection method · the solver · the per-metro dock reference table, while we keep delivering there.

**Does not compound:** model weights across verticals. Dock knowledge across metros. Anything we stop visiting.

Four limits to state before someone else does:

1. **A flow, not a stock.** Gate codes change, shops move, docks get rebuilt. 12–24 month half-life. Survivable because we are paid to be present — fatal to describe as accumulated.
2. **It does not travel.** A dock atlas for one metro is worth nothing in the next one. The moat is won metro by metro, which is the same density constraint that killed 1.0.
3. **Coverage beats depth, and we will have depth.** 230 docks known intimately in a quarter is a pilot. An autonomy operator needs thousands.
4. **Nothing stops an incumbent doing this.** An ERP vendor sits on thousands of distributor sites and could instrument them tomorrow. The barrier is that **their revenue does not depend on the label being right** — an incentive argument, not a technical one. Say so.

### The claim has a test, and the test is `PRD-8`

"It compounds" is an assertion until a model trained on one customer is evaluated on another, and on another vertical, with the result reported either way. `PRD-8` is that experiment and it is the only evidence that settles the question.

**Until it runs, the defensible sentence is:** *we have built the collection method and the schema; whether the learning transfers is measured, not assumed.*

---

## 8. Build order

| Phase | What | Unblocks |
|---|---|---|
| **1** | `IDN-1..4` identity · `DRV-1..2` sensor · `REC-1..5` record | Everything |
| **2** | `EXP-1..3` control arm · M3, M4 · baselines + harness | The measured number |
| **3** | M1 dwell + conformal · M1b censoring · override capture | Live authority |
| **4** | `PRD-8` transfer evaluation | The compounding claim |
| **5** | M5 modality · payload dataset | The supply side |

M2 sits between phases 2 and 3: the arm starts in 2, the classifier is trainable ~8 weeks later.

**Nothing here needs a GPU, a model server or a feature store.** The artefact is a few-megabyte file loaded in-process. The hard parts are the sensor, the identity layer and the experiment design — none of which is a research problem.
