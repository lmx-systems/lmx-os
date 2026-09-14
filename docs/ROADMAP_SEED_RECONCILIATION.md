# Seed roadmap reconciliation — Rich's framework × the build roadmap

**14 September 2026.** Every element of *LMX 1.5 · Seed Roadmap — Framework* (Rich, built from the 13 Sep sync) mapped against `docs/ROADMAP_1.5.md`, `docs/MODEL_AND_DATA_BRIEF.md` and the founder sync record.

**Neither document supersedes the other.** Rich's framework is the **commercial chain**: what the round buys, in what order, gated on evidence. This repo's roadmap is the **build**: what gets written, in what order, gated on measurement. They answer different questions and both are needed. What they must not do is use the same words for different things — which, today, they do.

**Tags:** `COVERED` the build roadmap already carries it · `RESHAPED` carried, but in a different shape than the framework assumes · `MISSING` real work with no home in either document · `CONFLICTS` the two documents disagree on fact or on a decision · `NOT-ENGINEERING` correctly absent from the build roadmap, needs an owner elsewhere.

---

## 1. Read this first — the phase numbers collide

The two documents both have a "Phase 1" and they are not the same phase. Anyone holding both will sequence work wrongly.

| Rich's phase | What it gates | Nearest build phase | Relationship |
|---|---|---|---|
| **P0** Standing start | One live order end to end | **Phase 0 + Phase 1** — and it also reaches into **Phase 4** (`TEN-4` commingling) and **Phase 5** (`SUP-3` modality rules) | Rich's P0 is far larger than the build's Phase 0. It contains the whole measurement instrument *and* two items the build gates behind a signed data-rights clause |
| **P1** First account, first van | Measured cost-per-drop reduction, control arm | **Phase 2** | Off by one. Rich's P1 ≈ my Phase 2 |
| **P2** Second account, pooled decision | Commingled route beats the two solo routes | **Phase 4** | Off by two |
| **P3** Density inside one catchment | Drops/day inside a radius | **nothing** | Sales capacity, not engineering. See §7 |
| **P4** The handoff | One signed operator agreement | **Phase 5** | Off by one, and the only pair that matches in substance |

**The practical consequence.** The build phases are not a sequence of commercial milestones and were never meant to be. Phase 3 (live authority) and Phase 4 (cross-company) both have to be running *before* Rich's P2 gate can even be attempted, and Phase 1 has to finish before Rich's P1 gate means anything — because a cost-per-drop delta measured on a minute-resolution clock is not a measurement.

**Recommendation:** rename Rich's phases **S0–S4** ("seed gate 0–4") in the framework, and leave the build phases numbered as they are. One word per heading, and the ambiguity is gone permanently.

---

## 2. The backward chain, link by link

| Link | Claim | Where it is built | Tag |
|---|---|---|---|
| **L1** "No data needed to start — ingest, batch, hold 18 min, commingle, route, POD" | Correct, and recorded in `MODEL_AND_DATA_BRIEF.md` §1 against the 13 Sep three-way agreement | `ING-1..4`, `DEC-1`/`DEC-2` `BUILT`, `DEC-3` `RESHAPE`, `DRV-6` POD `BUILT`. **Commingling is not here** — cross-company is `TEN-4`, Phase 4 | `RESHAPED` |
| **L1** One to three vans, not fifteen | No engineering item. Correct — a van is not code | — | `NOT-ENGINEERING` |
| **L1** Fleet capped at ~3.6% of flow, **tracked monthly from month one** | The cap is in the strategy doc; the *tracking* exists nowhere. It is a monthly number with no owner and no dashboard | — | `MISSING` |
| **L2** LMX holds the decision across companies | `TEN-1..5`, gated on Phase 0.2 | Phase 4 | `COVERED` |
| **L2** Second shipper consents to being pooled | `TEN-2` — a customer can name who they will not be pooled with | Phase 4 | `COVERED` |
| **L2** Carrier cost passed through at cost | §2.3 confirms it against the 13 Sep call | `SUP-2` | `COVERED` |
| **L2** Flat fee **per decision** | The build meters **per order ingested**. Different primitive, and §2.3 marks the range unsettled and the licence in conflict. See §5 | `STL-3` | `CONFLICTS` |
| **L3** The shipper filter — sheds the decision, or has overflow already on gig | GTM. Correctly absent from the build | — | `NOT-ENGINEERING` |
| **L3** Price ladder $1.00 → $1.50 → $3.00 → $4.00 | See §5.1 and §9. The bottom rung and the top rung are both in question | §2.3 | `CONFLICTS` |
| **L4** Density inside a radius | See §5.4 — the analysis is assigned to the wrong person | — | `CONFLICTS` |
| **L5** Qualified, eligible, dense, windowed volume | `SUP-3` eligibility engine, `SUP-4` payload dataset, `SUP-5` operator adapter | Phase 5 | `COVERED` |
| **L5** "76% eligibility for your service" | A **per-dock** statistic. Uncomputable until `IDN-1..4` resolves what a dock is. See §5.3 | Phase 1 | `RESHAPED` |
| **L5** Measured false-positive rate | Carried as **Gap 5** in `ROADMAP_1.5.md` §1 (*"requires #2 running"*, partly engineering) and in Rich's ledger — but **no build item produces it.** `SUP-3` decides eligibility; nothing scores how often it is wrong | Needs `SUP-6` | `RESHAPED` |

---

## 3. Corrections to the framework

Five. Three are factual, two are ownership.

### 3.1 The price ladder's bottom rung contradicts the 10 September decision

The framework has **$1.00 design partner, paid in data rights** — i.e. no licence. Matan on 10 September: *"License is the floor. Fulfillment is the wedge"*, sized at ~$130–150/month. On 13 September he reopened savings share on top.

The build roadmap said the same thing as the framework, and §2.3 now marks it `CONFLICTS` rather than settled. **Both documents inherited the same error from the same place.** It is one decision, it belongs to Matan, and it is the subject of the memo sent to him today.

### 3.2 "Batching — p50/p90 prediction; gradient model, CatBoost or XGBoost"

Two errors in one line.

- **p50/p90 quantile prediction is dwell, not batching.** Dwell is `M1` — how long a stop takes, p50 for planning and p90 for what we can promise. **Batch value is computed, not predicted**: solve the route with the order and without it, take the difference. Predicting it is strictly worse than computing it, and the framework's own Part 3 correctly lists "batch value — the threshold below which a route is not worth certifying" as a separate open item. The two lines are describing different things under one heading.
- **CatBoost is vetoed.** Matan, 13 Sep, on learning it is a Yandex project: *"I can't. No, we can't, sorry."* The framework carries it as `[WORKING, pending external review]`; it is not pending, it is decided. The substitute named on the call was XGBoost, which is also wrong — see `MODEL_AND_DATA_BRIEF.md` §3. **The answer is LightGBM**, which `lmx-dwell/` already runs, so there is no rework.

**Replacement line:** *Dwell — LightGBM quantile, p50 and p90. Batch value — computed by the solver, not a model.*

### 3.3 Entity resolution is absent, and L5 depends on it

The framework has no identity or entity-resolution workstream. It is the largest `NEW` block in the build roadmap (`IDN-1..4`, Phase 1) and the framework's own headline number needs it: "76% eligibility in X place" is a statement about **docks**, and the design partner's export has 230 customer IDs → 224 names → an unknown smaller number of physical docks, including five records for one body shop and one record whose city, state and zip are literally `N/A`.

This is not pedantry. A per-dock statistic computed before identity holds still has already put a figure in investor materials that had to be withdrawn.

### 3.4 Dwell is "the first factor to solve" and cannot be measured today

The framework is right that dwell drives everything downstream, and the source is Sourabh on 13 September — *"I want my model to improve dwell. And the data that I'm feeding is Alan's data."* So this is a self-correction, not a disagreement with Rich.

The problem: **the design partner's dispatch export is minute-resolution, so 65.5% of stops compute to zero dwell.** The one second-precision export shows those same stops really took 3–50 seconds. No filter recovers that — only a new sensor does, which is `DRV-1`/`DRV-2` and the entirety of Phase 1's risk. And `DRV-2` is blocked on Phase **0.4 and 0.8**: the drivers belong to the customer, so driver-app access and background-location consent have to be negotiated into the pilot terms — and the iOS "always" tier still needs an App Store justification and a real developer account.

Rich's P0 says "capture instrumented from order one," which is exactly right in intent. It just cannot start until someone signs the consent clause.

### 3.5 The density threshold is assigned to the wrong person

Part 8 assigns *"density threshold per modality"* to Sourabh, and Part 1 calls it *"arguably the highest-value analysis in the whole roadmap."* It is high-value and it is not an analysis.

**It is their number, not ours.** A drone's threshold is set by the operator's own micro-hub overhead — Bobby has already said 30–40 micro-hubs per city, ~15 minutes citywide. An AV's threshold is not a density at all: it is our parcel fee weighed against a robotaxi fare, which the framework's own Part 6 already says. Modelling it from our side produces a number the counterparty will replace with theirs in the first conversation.

**So it is a phone call, and it is the same phone call as Part 8's "whether an operator terms conversation is pulled forward pre-raise."** That item is assigned to Richard, dated *this week*, and closes build Gap 4 (Phase 0.3) at the same time. Doing it converts the roadmap's largest assumption into its strongest evidence, which the framework itself says in the Phase 4 structural note. **Recommend merging the two items and dropping the analysis.**

---

## 4. The proof ledger — where each proof actually gets produced

The best-aligned part of the framework. One correction and one gap.

| Claim | Rich's phase | Produced by | Build phase | Note |
|---|---|---|---|---|
| The decision saves money on the shipper's own fleet | P1 | `EXP-1..3` + `STL-1..2` | **2** | Aligned. `EXP-1` must be **in the contract before the code** |
| Commingling across shippers beats solo routing | P2 | `TEN-4` | **4** | Gated on 0.2, the signed data-rights and pooling clause |
| Shippers will pay separately for the decision | P1→P3 | Phase 0.1 — quote a price to a named buyer | **0** | Needs no engineering at all. Three weeks, or the problem is not the product |
| Density is reachable inside a catchment | P3 | — | **none** | `NOT-ENGINEERING`. See §7 |
| The eligibility corpus exists nowhere else | P2–P4 | `SUP-3`, `SUP-4` | **5** | Rich dates this P2–P4; the build puts the corpus at Phase 5. **The framework is ahead of the build here** |
| Autonomy operators will take qualified B2B volume | P4 | `SUP-5` | **5** | Aligned. And it costs a call — see §3.5 |

**The missing row.** The framework names a *measured false-positive rate* in both L5 and Phase 4, and no build item produces one. `SUP-3` decides eligibility; nothing scores how often it is wrong. That needs a `SUP-6`, and it is the number an operator will actually ask for — a false positive is a truck rolled for nothing on their side, not ours.

---

## 5. Decisions — the two lists merged

| Decision | Rich's owner | Build doc | Reconciled owner | Note |
|---|---|---|---|---|
| Density threshold per modality | Sourabh | — | **Rich** | §3.5 — a call, not an analysis; merge with the operator-terms item |
| Van count at launch, gig bridge trigger | Rich | — | Rich | `NOT-ENGINEERING` |
| Control-arm study design | Sourabh / Rich | `EXP-1` | **Sourabh designs; Rich + counsel contract it (0.2), Matan negotiates driver access (0.4)** | The arm has to be in the pilot terms before it can be built |
| Price point, design-partner and cost-neutral tiers | Matan | §2.3 | Matan | Memo sent 14 Sep. Now includes the licence and savings share |
| Launch geography and the stated reason | All three | — | All three | Open in both. Matan asked *"why are we not launching in Dallas?"* on 13 Sep |
| Operator terms conversation pulled forward | Rich, **this week** | Phase 0.3 | Rich | **Highest-leverage item in either document** |
| Round size and the milestone it buys | Matan | Gap 8 | Matan | Downstream of everything above |
| **Fee unit** — per order / decision / drop | Part 7, open | §2, **decided** | Matan | The two documents disagree on whether this is settled. §2 says per order ingested; §2.3 reopens the licence alongside it |
| **Outcome liability** | — | 0.7, open | Rich | In the build doc, absent from the framework. Blocks `DEC-1` credit percentages |
| **Background location tier** | — | 0.8, open | Sourabh + Matan | In the build doc, absent from the framework. **Phase 1 is blocked on it** |

---

## 6. In the framework, no engineering home — correctly

Listed so nobody goes looking for a ticket that should not exist.

- **The shipper filter and account arithmetic** (L3, workstream 4). Sales capacity, targeting, spend.
- **Phase 3 density** entirely. It is an account-acquisition phase. The build roadmap has no representation of it and should not.
- **Van count and the gig → DSP bridge** (workstream 5).
- **The three audiences, three messages** (workstream 4). This is §1.1's two lenses seen from the commercial side — the framework splits customer / sophisticated customer / investor, the build roadmap splits investor lens / GTM lens. Same distinction, and the framework's version is better because it separates the two customer registers.
- **Use of funds** (Part 5) and **round size**.

---

## 7. In the build roadmap, absent from the framework

Where the seed narrative would be stronger for carrying them.

| Item | Why it matters to the round |
|---|---|
| `IDN-1..4` entity resolution | §3.3. The framework's own eligibility number depends on it |
| `EXP-1..3` control arm | The framework has the control arm as a *study*; it is also a **runtime component** — arm assignment at intake, immutable, with an integrity monitor. It cannot be added after the fact |
| `REC-5` data dictionary | Three sources currently report three different stop counts for the same window. One page, signed by all three. Every number in the framework inherits this |
| Phase 0.4 driver-app consent | Blocks the whole measurement instrument, and it is a **contract term**, not a build task |
| `PRD-8` cross-tenant transfer evaluation | The only evidence that settles "it compounds." The framework asserts the corpus is unique; `PRD-8` is what makes it a measured claim rather than an assertion |
| The 65.5% zero-dwell finding | The single most important known defect in the data the framework plans to train on |

---

## 8. One new disagreement, worth settling before the deck is rebuilt

Part 6 asks *"are we taking enough?"* — framed as a worry that $3 a drop against $5–10 saved is too little, with dispatch platforms and Uber taking far more.

**On the only real dataset the worry points the other way, and by more than the framework allows for.** Two corrections have to be made to the denominator first.

**Correction one: $181,000 is the best case, not the saving.** `claude/09-united-baseline-vs-lmx-os.md` gives the range explicitly. The **floor is $109,000 (20%)** — batching to six stops per route, which is software already written. The **best case is $181,000 (34%)** — batching *plus* pre-staging the next load, which requires the design partner to change how his warehouse stages parts. That document's own caveat: *"The floor is software we have written… a customer commitment, not a product feature. Both belong in the deck; only the first is a product claim."*

**Correction two: the drop count is unreconciled.** Matan quotes ~40,000 drops/year (200/day); `MODEL_AND_DATA_BRIEF.md` derives ~35,000 (141 stops/day × 250 days). Those are two different definitions of a stop and nobody has reconciled them — which is exactly the defect `REC-5` exists to fix, and §7 warns that every number inherits it. **Both bases are shown below rather than picked.**

**Price it per delivery and the drop-count argument disappears.** Doc 09 gives the measured cost per delivery in every scenario, so no annual total and no stop definition is needed:

| Scenario | LMX cost/drop | Our fee | Shipper pays | Against $14.90 today |
|---|---|---|---|---|
| Floor, $1.50 | $12.22 | $1.50 | $13.72 | saves $1.18 |
| **Floor, $3.00** | $12.22 | $3.00 | **$15.22** | **costs him $0.32 more** |
| Floor, $4.00 | $12.22 | $4.00 | $16.22 | costs him $1.32 more |
| Best case, $3.00 | $10.18 | $3.00 | $13.18 | saves $1.72 |
| Best case, $4.00 | $10.18 | $4.00 | $14.18 | saves $0.72 |

**The finding.** Our software alone takes the design partner from $14.90 to $12.22 — **$2.68 of room.** A $3.00 fee is larger than the room. At the floor, taking $3.00 leaves him worse off than doing nothing, and the framework's $4.00 top rung leaves him $1.32 worse off. The fee only clears if he adopts pre-staging, which is him changing how his warehouse picks — not anything we build.

So Part 6's question has the right subject and the wrong sign. The $5–10 band is Matan's *autonomy-era* figure from 13 Sep (cost today $12–16, autonomy $1–5); it is not what is available now, and using it makes $3 look modest when against today's floor it looks aggressive.

**Three consequences.**

1. **The price ladder needs re-deriving from the floor**, not from the best case. Whether the answer is a lower fee or a longer runway to $3.00, it should not be discovered by a shipper doing this division in a meeting — and he can, because we hand him the number.
2. **Pre-staging becomes a commercial term, not advice.** If the $3.00 fee depends on it, it belongs in the pilot agreement alongside the control arm and the data-rights clause.
3. **A savings share on top is arithmetically unavailable at the floor.** Matan proposed 30% on 13 Sep and Rich 20%; on gross savings at the midpoint that would take the total to ~91% of the *best case* and well past 100% of the floor. This is the strongest argument in the memo sent to Matan on 14 Sep, and it is stronger than the version in that memo, which used the best case as if it were the saving.

## 9. What to change, and where

| Change | Document | Size |
|---|---|---|
| Rename P0–P4 to S0–S4 | Framework | One word per heading |
| Replace the CatBoost / p50-p90-batching line | Framework, workstream 2 | One line |
| Add an identity / entity-resolution workstream | Framework, Part 3 | A paragraph |
| Note that dwell is unmeasurable until `DRV-1` and 0.4 | Framework, P0 | A sentence |
| Move the density threshold to Rich, merge with the operator call | Framework, Part 8 | One row |
| Restate the price ladder against the 10 and 13 Sep positions | Framework, L3 | Pending Matan's answer |
| Replace the $5–10 saving with the floor/best-case range and the capture table | Framework, Part 6 | A table |
| Reconcile the drop count — 200/day vs 141/day — into `REC-5` | Build roadmap, Phase 1 | Already an item; make the two bases explicit |
| Add `SUP-6` — eligibility false-positive rate | Build roadmap, Phase 5 | One row |
| Add the 3.6% fleet-cap monthly metric, with an owner | Either | One row |

---

*Sources: LMX 1.5 · Seed Roadmap — Framework (Rich, 14 Sep 2026) · `docs/ROADMAP_1.5.md` v1.2 · `docs/MODEL_AND_DATA_BRIEF.md` v1.2 · `claude/14-founder-sync-context.md` · syncs of 9–13 September.*
