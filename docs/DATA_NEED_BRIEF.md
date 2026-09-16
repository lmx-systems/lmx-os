# The data need — how much data is good enough, and how we will know

**v0.1 · 16 September 2026 · Sourabh · for the 16 Sep session and the Chandan review on Thursday**

Companion to `docs/MODEL_AND_DATA_BRIEF.md`, which says *what the models are*. This says *how much data each one needs, and how we turn an exit KPI into that number.* Deliberately not called a model brief — the question in front of us is a data question.

**Status of every figure here:** the method is settled, the inputs are not. Assumptions are labelled `[ASSUMPTION]` and each one is a question for Thursday. Nothing below should reach an investor before those are tested.

---

## 1. The method, in four steps

This is what I described on the 15th, written down so it can be argued with:

1. **State the exit KPI as a measurable accuracy**, not as a business outcome.
2. **Work out how many observations it takes to hit that accuracy** — and, separately, how many it takes to *prove* we hit it. These are different numbers and the second is usually bigger.
3. **Work out how many of those observations one delivery produces.**
4. **Divide.** That gives deliveries, which the business plan can consume as customers × volume × time.

Step 3 is where the surprise is, and it is the reason this document is longer than a number.

---

## 2. What the model actually does

> The model does not carry the parcel. It predicts whether an item can get from A to B inside a window, what that costs by each available means, and therefore which orders should ride together and on what.

Three decisions, and only three:

| Decision | Plain English | Where it lives |
|---|---|---|
| **Batch / commingle** | Which orders ride together, and how long we hold before committing | `M3` — computed by the solver; the *policy* is learned |
| **Modality** | Which class of vehicle can carry this, to this dock | `M5` + the rules engine |
| **Feasibility and cost** | Will it make the window, and what will it cost | `M1` dwell, `M2` urgency, `M4` arithmetic |

Once the mechanism is chosen the handoff happens and it goes its own way. **We are not accurate about driving. We are accurate about choosing.**

---

## 3. The structural finding: there is no single "data per delivery"

Matan asked for one number — *one delivery gives us X*. The honest answer is that the five predictors accrue data in **four different units**, only one of which is the delivery, and one of them **stops accruing altogether**.

| Predictor | Data accrues per… | Scales with | Yield per delivery |
|---|---|---|---|
| `M1` dwell | **stop** | deliveries, without limit | ~0.97 |
| `M1b` censored dwell | **abandoned stop** | deliveries, without limit | ~0.03 `[ASSUMPTION]` |
| `M2` urgency | **observed consequence** | deliveries, without limit | ~0.008 |
| `M3` batch policy | **cross-customer pair sharing a window** | customers², not deliveries | see §4.4 |
| `M5` modality fit | **dock, surveyed once** | **customers' dock counts — it saturates** | n/a — see below |

**The two findings worth taking into the room:**

**(a) The label the investor story rests on does not accrue with volume at all.** A dock is surveyed once. One customer has a finite number of docks — the design partner has roughly 229 — and once they are surveyed, more deliveries to that customer produce **zero** new eligibility data. Modelling `M5` as a per-delivery rate is wrong in both directions: it overstates the long run and understates the first month. The correct statement is that `M5` is a **discovery process with a hard ceiling at the customer's dock count**, and the survey is a few weeks of fieldwork regardless of how much volume runs through.

**That is the argument for breadth, and it is a ceiling argument rather than a rate argument, which makes it stronger.** Doubling volume at one customer buys more dwell and more urgency and not one additional dock.

**(b) The scarce labels are scarce by two orders of magnitude.** Dwell arrives on essentially every stop; an urgency consequence arrives on about one stop in 120. A year at one customer produces ~34,000 dwell observations and ~290 urgency consequences.

---

## 4. Sizing each one

### 4.1 Dwell (`M1`) — the cheap one

| Target | Observations | At the design partner |
|---|---|---|
| A usable per-dock estimate | ~30 per dock, shrunk toward node class `[ASSUMPTION]` | 5–6 weeks |
| A **promise** we can stand behind — 90% conformal coverage landing within ±2 points | **900 calibration points** (95% confidence). 225 gives the same band only ~68% of the time | Weeks, once the sensor exists |
| Same, guaranteed in the **thinnest** node class | 900 in the rarest of 7 classes, not 900 ÷ 7 | Set by class skew, not by total volume |

> **A precision worth getting right before Thursday.** Split conformal's *marginal* coverage guarantee is exact and holds at any n ≥ 9. There is no sample-size floor below which it breaks. What n buys is **training-conditional stability** — how far *this particular* calibration set's realised coverage wanders from 90%. Exactly: at n = 225 the realised coverage lands within ±2 points 68% of the time; at n = 900, 95%; at n = 1,825, 99.6%. Calling 225 a "floor" would be wrong and is the kind of thing that costs credibility in the first ten minutes.

**The blocker is not volume, it is the clock.** The dispatch export is minute-resolution, so 65.5% of stops compute to zero dwell. No quantity of that data helps. `DRV-1` and `DRV-2` are the prerequisite; **both are blocked on Phase 0.4** (driver-app access in the pilot terms — the drivers belong to the customer), and `DRV-2` additionally on 0.8.

`[ASSUMPTION]` The ~30-per-dock figure is inherited and has no derivation in any document. The repo's own shrinkage baseline uses `prior_strength = 10.0`. One of the two is wrong — question for Thursday.

### 4.2 Urgency (`M2`) — the slow one

Needs 500–1,000 observed consequences. A consequence is a call, a credit, a return, a cancellation, a competitor-sourced part, or a reorder gap — **or silence, which is the label that says the flag was soft.**

`[ASSUMPTION]` ~10% of orders run late · ~1 in 6 has a credible urgency argument (measured, one full manifest) · ~half of consequences are observable.
→ **0.008 consequences per delivery, ~290 per year at one partner. That is 21 to 41 months alone.**

**Five partners each at full volume reach 500–1,000 in about seven months** — five times the events, five times the speed. Note what that does *not* say: five partners at a fifth of the volume each is the same total volume and therefore the same 21–41 months. The speedup comes from more total deliveries, not from spreading the same deliveries thinner. Breadth wins here for a different reason than it wins in §4.4, and conflating the two is how the argument gets picked apart.

> **This supersedes the M2 row in `MODEL_AND_DATA_BRIEF.md` §6**, which says 4–6 months at one partner. That row was not derived; this one is. The companion should be corrected to match.

### 4.3 Modality fit (`M5`) — the one the investor story rests on

Two different numbers, and the second is the one nobody has costed:

| | Requirement | Note |
|---|---|---|
| **To build** an eligibility rule set | ~200 surveyed docks — **fieldwork, 6–8 weeks**, not a function of delivery volume | Capped at the customer's dock count |
| **To prove** a false-positive rate of ~5% to ±2 points | **626 decisions with known outcomes, per modality** → ~2,500 across four classes | Wilson interval; see below |
| Same, to ±1 point | 2,167 per modality → ~8,700 total | |

> **Do not use the textbook formula here.** The normal-approximation (Wald) sample size gives 456, and 456 does not deliver ±2 points at a 5% rate — its true Wilson interval is 3.35%–7.40%, an upper half-width of 2.4 points. Wald is anti-conservative in exactly the small-proportion regime and it fails on the side that matters. The honest numbers are **626** and **2,167**.
>
> **And ±2 points on a 5% rate is ±40% relative error.** An operator deciding whether to trust our payload cares whether their false-positive rate is 5% or 7% — that is a 40% swing in wasted vehicle movements. If their threshold is relative rather than absolute, the ±1-point row is the real requirement and the ±2-point row is decorative. Worth asking them rather than assuming.
>
> Both numbers are also conditional on the rate actually being 5%. At 10% the ±2-point requirement roughly doubles, to ~1,015 per modality.

**The hard gate.** An eligibility *outcome* only exists once an operator has attempted the delivery and succeeded or refused. Until `SUP-5` — one operator taking real volume — the false-positive rate is **not measurable at any data volume**. We can build the instrument and we cannot score it alone. This is the number the operator will ask for, because a false positive is their vehicle rolled for nothing.

### 4.4 Batching and commingling (`M3`) — the one that changes the roadmap

Commingling is a **pairwise** property. With *n* customers in one catchment there are *n(n−1)/2* possible pairs, and only a fraction produce a commingleable order on any given day.

`[ASSUMPTION]` ~15% daily chance that any given pair of customers has two orders that could ride together — Rich's own figure from the 10th, and **the single most important number in this document to test.**
**Target:** ~200 observed commingle events. Operating days, not calendar days — ~250 a year, ~21 a month.

| Customers in one catchment | Pairs | Events/day | Time to 200 events |
|---|---|---|---|
| 2 | 1 | 0.15 | **64 months** |
| 3 | 3 | 0.45 | **21 months** |
| 5 | 10 | 1.5 | 6.4 months |
| **10** | **45** | **6.75** | **6 weeks** |
| 20 | 190 | 28.5 | 1.4 weeks |

**Rich's instinct on the 15th was right and the arithmetic is sharper than the instinct.** He said *"three different customers is not enough… I need 10, 10 unique customers."* Three customers is twenty-one months. Ten is six weeks. The curve is quadratic, so the first few accounts buy almost nothing and accounts five through ten buy nearly everything.

Two consequences:

- **Customer count, not customer size, is the constraint on the central claim.** Twenty small accounts beat three large ones for this purpose — which is also what Rich argued for vans: *"I'd rather just have three Vans because that means I can have 20 accounts."*
- **They have to be in the same catchment.** Ten customers across three metros give three small pair-counts, not one large one. The same density constraint as 1.0, arriving through a different door.

**Three things this model gets wrong, which we should say before anyone else does:**

1. **It saturates, and the table stops at the edge of where it stops being true.** An order can ride in only one batch. Expected commingle partners per customer per day is 0.15(n−1) — 1.35 at n = 10, **2.85 at n = 20**, which asserts the average order rides with three others against 6-stop routes. Beyond n ≈ 20 the per-pair rate must fall to keep batch size inside vehicle capacity, and the curve goes linear. **Read nothing off it above 20.**
2. **Pairs are the right unit for "these two rode together," not for "holding 45 minutes beat 90."** The hold-window policy learns from batching *decisions*, which are bounded by orders per day and so scale linearly in *n*, not quadratically. `M3`'s stated purpose is the hold-window policy, so the effective sample size for that purpose is smaller than this table implies.
3. **"200 events ≈ 30 per node class" does not follow.** Node-class frequency is skewed and the binding number is the rarest class. Also unresolved: a commingle event is a property of a *customer pair*, while node class is a property of a *dock* — we have not defined which dock's class such an event is attributed to, and the per-class target is meaningless until we do.

**What it understates, both in our favour:** near-misses are labels too (pairs that shared a window and failed on capacity, geography or timing are informative negatives), and same-customer multi-order batching produces hold-window evidence with no cross-customer pair at all — and is available today.

---

## 5. The exit KPIs, stated so they can be measured

Matan named two families on the 15th. Here they are with a measurable form and an honest status.

| # | KPI | Measurable form | Can we measure it today? |
|---|---|---|---|
| 1 | **Autonomy-ready handoff** | An operator accepts our order payload without a human touching it, at a stated false-positive rate | **No.** Needs `SUP-5`. Unblocked by a phone call, not by data |
| 2 | **Savings passed through** | Measured cost per drop against a control arm, over a defined baseline | **No.** Needs `EXP-1`, and the arm must be in the contract before the code |
| 3 | **Modality recommendation accuracy** | % of orders where the chosen class was carriable, split autonomy / non-autonomy | Partially — we can score the rules offline, not the outcomes |
| 4 | **Commingle rate** | % of orders that actually rode with another customer's order | **No.** Needs Phase 4 and the signed pooling clause |

**Three of the four are gated on a signature, not on a model.** That is worth saying out loud tomorrow, because the seed roadmap currently reads as though data volume is the path.

### The number that needs re-deriving before it goes in a deck

Matan committed to *"25 to 35%… at the minimum"* savings, and separately assumed *"20% lift"* in the same call. The only measured evidence says:

| | Annual | % | Requires |
|---|---|---|---|
| Floor | $109,000 | **20%** | Batching only — software we have written |
| Best case | $181,000 | **34%** | Batching **+ pre-staging** — the customer changes how his warehouse picks |

25–35% is the **ceiling**, and the top of it needs an operational change at the customer that is not in any contract. Rich's own proposal is the right one: run two free weeks, measure it, then set the number — *"it came out four percent… or it got 30."*

---

## 6. What this says the roadmap needs

| Need | Implies | Currently |
|---|---|---|
| 10 customers in **one** catchment | Account count is the Phase 4 gate, not volume | Roadmap has no customer-count gate |
| Eligibility outcomes | One operator agreement before the FPR is measurable | `SUP-5`, Phase 5 — and it costs a call |
| Second-precision dwell | `DRV-1`/`DRV-2` before any dwell number is real | Blocked on pilot terms 0.4 and 0.8 |
| A measured false-positive rate | A build item that does not exist | Proposed as `SUP-6` |
| One definition of a stop | Three sources report three different stop counts | `REC-5`, unbuilt |

---

## 7. Open — the Thursday agenda

1. **Is 15% the right pair-commingle rate?** Everything in §4.4 moves with it, and it is almost certainly not homogeneous — it is a function of each customer's order frequency, so it will sit below 15% at small *n* and above it at large *n*. Measurable today from historical order timestamps and geography, with no new collection. **First thing I would run.**
2. **Is ~30 observations per node class the right target for a hold-window policy?** Sensitivity is extreme by node class (+3–7% at high-frequency shops vs +195–271% at warehouse and transfer nodes), so the thin classes may need far more.
3. **Does an accuracy target or a cost-of-error target drive the sizing?** A false positive on a drone costs a wasted flight; a false negative costs a lost batching opportunity. They are not symmetric and the sizing should probably follow the expensive one.
4. **Is the yield table right?** The censoring rate and the observability of urgency consequences are both guesses, and `M1b` at 0.03 is the one I have least confidence in.
5. **What is the cheapest defensible substitute for waiting?** Simulation gives us mechanism coverage, not label truth. Where is that an honest bridge and where is it circular?

---

*Sources: the 13 and 15 September syncs · `docs/MODEL_AND_DATA_BRIEF.md` · `docs/ROADMAP_1.5.md` · the design partner's dispatch export (13 drivers, 229 receivers, 2 Jan – 6 Apr 2026) and the separate second-precision file. **Stop counts differ between those two sources and are not reconciled — that is `REC-5`, and every figure here inherits it.** Sizing: Wilson score interval for a proportion; split-conformal training-conditional coverage from the exact Beta(n+1−⌊(n+1)α⌋, ⌊(n+1)α⌋) distribution.*
