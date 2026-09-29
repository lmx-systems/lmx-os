# LMX 1.5 — Feature schema for M1, M1b and M2

**v1.1 · 28 September 2026 · Sourabh**

*v1.1 corrects §0. It said only `M1` existed as code; `ml/m2/` — feature module, baseline, challenger, splits, calibration and leakage gates — had existed since `2ccb6ce` (#50), before v1.0 was written. Nothing else changes.*

**v1.0 · 21 September 2026 · Sourabh**

Written in response to an external review request for column-level definitions. Companion to `MODEL_AND_DATA_BRIEF.md` §3 and §5.

**Definitions only — no rows, no values, no customer identifiers.** Cardinality counts are computed from the design partner export (`customerTiming`, 2 Jan – 6 Apr 2026, 6,715 stops) and released as counts, not as data.

---

## 0. Scope — what is code and what is specification

The reviewer's read of the shape is correct: of the five predictors, three are trained models (`M1`, `M1b`, `M2`), `M3` and `M4` are exact computations by design, and `M5` is rules-first with learned support.

One correction that matters before the algorithm review: **no model here is trained on real labels except `M1`.** Distinguish the harness from the model — `M2` has most of one and cannot use it yet.

| | State | Where |
|---|---|---|
| `M1` dwell | **Built and trained.** Features, causal construction, chronological and cold-start splits, shrunk baseline | `lmx-dwell/dwell/` |
| `M1b` censored dwell | **Specified, not built.** No feature module, no training code | — |
| `M2` true urgency | **Harness built, model untrainable.** Leakage-safe feature module, shrinkage baseline and a challenger that must beat it, splits, isotonic calibration and leakage gates all exist. What does not exist is a label | `ml/m2/features.py`, `ml/m2/models.py`, `ml/m2/splits.py`, `ml/m2/calibration.py`, `ml/m2/gates.py` |

**This row said "specified, not built" until 28 September and was wrong when written** — `ml/m2/` landed in `2ccb6ce` (#50), before this document was. The claim it was reaching for is the one below, and that one holds.

`M2` cannot be **trained** before `EXP-1`, because its label does not exist yet (`MODEL_AND_DATA_BRIEF.md` §5) — `M2`'s label is an observed consequence, and observing one needs a control arm. So the harness sits complete and unfed, which is the right order: the leakage gates are what make the first trained model trustworthy, and building them after the model is how a leaky one ships. Any feature list for `M1b` below is design intent and is marked as such; `M2`'s is now code, and where the two disagree the code wins.

---

## 1. `M1` — dwell, per dock

**Target.** `dwell_min` — minutes at the location. Derived: `dwell_sec / 60`, where `dwell_sec = departed − arrived`. Two quantile models are fitted, α=0.5 and α=0.9.

**Grain.** One row per stop. The source export is at invoice grain (7,392 rows) and is deduplicated to stop grain (6,715 rows).

### 1.1 Categorical features

| Column | Definition | Type | Raw / derived | Formula |
|---|---|---|---|---|
| `receiver_id` | The delivery account the stop was made to | categorical (string) | **Raw** | Source column `UDID` |
| `driver_id` | Driver who made the stop | categorical (string) | **Raw** | Source column `Driver` |
| `node_class` | What kind of place the dock is — repair shop, parts store, dealer parts, body shop, warehouse, transfer, municipal, unknown | categorical (string) | Derived | Map from `receiver_id` via the `IDN-3` classifier; unmapped becomes `unknown` |
| `dow` | Day of week | categorical (string of int) | Derived | `arrived.dayofweek` |
| `hour_bucket` | Coarse time of day | categorical (string) | Derived | `cut(hour, bins=[-1, 8, 11, 14, 16, 24])` → early / morning / midday / afternoon / late |

### 1.2 Numeric features

| Column | Definition | Type | Unit | Raw / derived | Formula |
|---|---|---|---|---|---|
| `hour` | Hour of arrival | int | hour of day, 0–23 | Derived | `arrived.hour` |
| `stop_seq` | Position of this stop in the route | int | index | **Raw** | Source column `Stop #` |
| `stops_on_route` | How many stops the route contained | int | count | Derived | `nunique(stop_seq)` per `route_id` |
| `minutes_into_route` | Elapsed time since the route's first arrival | float | minutes | Derived | `(arrived − min(arrived) per route_id) / 60` |
| `invoice_count` | Invoices handed over at this stop | int | count | **Raw, absent** | Not present in this export |
| `invoice_value` | Commercial value of the stop | float | USD | **Raw, absent** | Not present in this export |
| `line_count` | Line items at the stop | int | count | **Raw, absent** | Not present in this export |
| `recv_prior_n` | Prior visits to this receiver before this stop | int | count | Derived, causal | `groupby(receiver_id).cumcount()` |
| `recv_prior_mean_dwell` | Mean dwell at this receiver, prior stops only | float | minutes | Derived, causal | `shift(1).expanding().mean()` per receiver |
| `recv_prior_p90_dwell` | 90th-percentile dwell at this receiver, prior only | float | minutes | Derived, causal | `shift(1).expanding().quantile(0.9)` per receiver |
| `recv_days_since_last` | Days since the previous visit to this receiver | float | days | Derived, causal | `date − shift(1) date` per receiver |
| `drv_prior_mean_dwell` | Mean dwell for this driver, prior stops only | float | minutes | Derived, causal | `shift(1).expanding().mean()` per driver |
| `is_first_stop` | Stop is the route's first | bool | 0/1 | Derived | `stop_seq == min(stop_seq)` per route |
| `is_last_stop` | Stop is the route's last | bool | 0/1 | Derived | `stop_seq == max(stop_seq)` per route |
| `is_repeat_visit_today` | Second or later visit to this receiver the same day | bool | 0/1 | Derived | `groupby(receiver_id, date).cumcount() > 0` |

**Every historical feature is an expanding window shifted by one.** That is the causal-encoding discipline described in `MODEL_AND_DATA_BRIEF.md` §7 rule (1): a feature for a stop may only use information that existed before that stop.

---

## 2. Cardinality — the CatBoost-versus-LightGBM crux

Computed on the design partner export, 6,715 stops.

| Categorical | Distinct levels | Median rows per level | Max rows | Levels with < 5 rows |
|---|---|---|---|---|
| `receiver_id` | **229** | 8 | 371 | 87 (38%) |
| `driver_id` | 13 | 399 | 1,399 | 0 |
| `node_class` | **1** | 6,715 | 6,715 | 0 |
| `dow` | 5 | 1,392 | 1,434 | 0 |
| `hour_bucket` | 5 | 961 | 2,694 | 0 |

`receiver_id` in detail, since it is the feature the library argument rests on:

| Threshold | Receivers below it | Share of receivers | Share of rows |
|---|---|---|---|
| < 2 stops | 45 | 20% | 1% |
| < 5 stops | 87 | 38% | 2% |
| < 10 stops | 122 | 53% | 6% |
| < 15 stops | 140 | 61% | 9% |
| < 30 stops | 166 | 72% | 17% |

> **This weakens an argument in the brief, and the weakening should be recorded rather than smoothed over.** `MODEL_AND_DATA_BRIEF.md` §3 justifies wanting CatBoost on the basis of "thousands of categories, few rows each." The measured figure is **229 levels at a median of 8 rows**, with the long tail of sparse receivers carrying only 2% of rows. Ordered target statistics earn their keep at thousands of levels; at 229 they are a refinement, not "the whole ballgame." The practical reading: the CatBoost veto costs less than the brief claims, and the hand-rolled causal priors already in `features.py` are closer to sufficient than argued. **The open question is no longer the library — it is whether those priors are shrunk**, because 38% of receivers speak from fewer than five observations.

Note also that `receiver_id` counts **accounts, not physical docks.** Resolving accounts to docks is `IDN-1..4`; until that classification is complete, 229 is an upper bound on distinct docks, not the dock count.

---

## 3. Four things the schema exposes that the brief does not

**(a) `node_class` is currently constant.** It resolves to a single value — `unknown` — because the dwell pipeline is invoked without a node-class map. The hierarchical-pooling and cold-start story in §7 rule (5) depends on a dock inheriting a prior from its class. **Today that feature carries no information.** The `IDN-3` classifier exists (`app/identity/node_class.py`) and is not wired into `lmx-dwell`.

**(b) The three order-size columns are 100% null.** `invoice_count`, `invoice_value` and `line_count` are in the feature list and absent from the export. Any claim that dwell is conditioned on order size is untested.

**(c) There is no censoring in this dataset.** Of 7,398 rows, 7,392 have both an arrival and a departure; **zero stops are arrived-but-not-departed.** `M1b` assumes real censoring, and the design partner export contains none. The brief states the national distributor's logs are full of abandoned stops — that dataset is not in this repository, so `M1b`'s assumption is currently unverified against data we hold.

**(d) The α=0.5 target is degenerate here.** Minute-rounding sends 65.5% of stops to zero dwell, so the median of `dwell_min` **is zero**. A p50 quantile model fitted on this export has nothing to learn. Only the p90 (9 minutes) is non-degenerate. This is the precision problem in §9 restated as a modelling consequence: `DRV-1` is not an improvement to M1, it is a precondition for half of it.

**One data-quality flag while we are here.** `minutes_into_route` reaches a maximum of 107,827 minutes — about 75 days — which is not a route. Route grouping needs a look before any model trains on it.

---

## 4. `M1b` and `M2` — design intent only

**`M1b` censored dwell.** Intended to consume the same feature set as `M1`, plus a censoring indicator and a lower bound on the unobserved dwell. Neither the indicator nor the bound exists in the current export (see 3c). The algorithm choice (`survival:aft`) cannot be reviewed against data until a dataset with real censoring is loaded.

**`M2` true urgency.** Target: P(a consequence occurs | this order is late), where a consequence is an escalation call, a credit issued, a part returned, an order cancelled, a competitor-sourced replacement, or a reorder gap — or silence. Candidate features are the `M1` set plus order value, promise-versus-actual delta, receiver order frequency, and part class. **None of this is built, because the label does not exist.** It requires `EXP-1`, the randomised hold arm, which is a contract clause before it is code.

---

## 5. What can and cannot be shared

Everything in this document is definitional. Column names, formulas, types, units and distinct-level counts carry no customer information and are freely shareable.

Not shareable without the customer's agreement: the values themselves — receiver identifiers, names, addresses, invoice values, and anything that resolves to a named business.

If a reviewer needs to see distributions rather than counts, the practical route is a synthetic or hashed extract preserving the shape of each column, which we can produce on request.
