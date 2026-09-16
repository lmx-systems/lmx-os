# Roadmap reconciliation — 1.0 → 1.5

**13 September 2026.** Every lettered item in `docs/ROADMAP.md` tagged against `docs/ROADMAP_1.5.md`.

Scope: **132 lettered items** across 14 prefixes. Roughly **60 are already done** and are not re-planned here — they are listed by ID in §6 so nobody re-opens them.

**Tags:** `CARRIED` still valid, maps to a 1.5 feature · `SUPERSEDED` the 1.5 version replaces it in a different shape · `RETIRED` built for a business that no longer exists · `BLOCKED` waiting on a decision, not on engineering.

---

## 1. Four findings worth reading before the tables

**(1) 1.5 needs two things the 1.0 roadmap never contained.** Across 132 items there is **no control arm** and **no entity resolution**. Not open, not deferred — absent. Those are `EXP-1..3` and `IDN-1..4`, and they are the only genuinely new engineering 1.5 requires. Everything else is carry, reshape or retire.

**(2) An open cofounder question was closed by the reposition, and nobody wrote it down.** The 3 August offsite recorded: *"Open cofounder question: does G replace the distributor path, run beside it, or bridge to it? Nothing assumes an answer."* LMX 1.5 answers it — the distributor path won, and capacity is now bought and passed through at cost rather than earned by LMX drivers holding personal gig accounts. **The entire 13-item G-series is retired as a demand path.** Four of its components survive as supply-side plumbing. This should be recorded as a decision, not absorbed silently.

**(3) Three items have been buildable since July and are exactly what 1.5 needs most.**

| Item | Doc's own words | Maps to |
|---|---|---|
| **W9** shadow-mode comparison engine + cutover scorecard | *"fully specified and buildable now"* | `DEC-0` + `STL-1` + `EXP-0` |
| **I3** broaden the annotation vocabulary — parking difficulty, gate/access codes, dock quirks | *"the one remaining pre-pilot item here… buildable since July"* | `IDN-4` |
| **E1** verify the optimizer against a live Google project | *"one command away"* | `DEC-3` |

**(4) The data-rights gap has been known since July.** `W7` — *"training-data rights in the customer contract… model-training, cross-customer aggregation and anonymization rights before first delivery"* — is tagged *"Legal work, gates B2, not engineering."* It is the same item as Phase 0.2, which gates all of Phase 4. It has been open for two months.

---

## 2. The mapping — every 1.5 feature to its 1.0 ancestor

| 1.5 | Feature | From 1.0 | Note |
|---|---|---|---|
| **IDN-1..3** | Canonical location registry, alias map, node class | — | **No ancestor.** New. |
| **IDN-4** | Receiver profile store | `I3` | I3 is this item, unbuilt since July |
| **ING-1** | ERP field-map verification | `E3` | Direct carry |
| **ING-2** | Idempotent intake on the fee unit | `L16` | Idempotency exists on the caller's ref; re-key to the billing unit |
| **ING-3** | Replay and backfill | — | New |
| **ING-4** | Historical export loaders | — | `lmx-dwell/` already does it; promote |
| **REC-1** | Immutable decision log | `W9`, `I1` | I1 (ground-truth capture) is done and is the foundation |
| **REC-2** | Execution trace | `F1`, `I1` | Done as *position pings*; needs stop events |
| **REC-3** | Outcome ledger | `I1`, `R5` | Partially there |
| **REC-4** | Linkage flag engine | — | New |
| **REC-5** | Data dictionary | — | New, and one page |
| **DRV-1** | Geofence arrive/depart | — | **No ancestor.** `F1` is 30s position pings |
| **DRV-2** | Background location | `A6` | A6 flagged the App Store justification; the decision was never made |
| **DRV-3..5** | Warehouse geofence, stop outbox, battery | `A0` | Outbox exists — extend it |
| **DRV-6** | Exception capture | `A0` | Done — verify reason codes |
| **DEC-0** | Shadow-mode runner | `W9`, `F14` | **The highest-value open 1.0 item under 1.5** |
| **EXP-1..3** | Control arm, exploration policy, integrity | — | **No ancestor anywhere in 132 items** |
| **PRD-1** | Batch value by node class | `I5` | Calibration, reframed |
| **PRD-2** | Trip cost vs order value | `F7` | Cost-per-drop trend was the one F7 gap left open |
| **PRD-3..4** | Baseline + evaluation harness | — | `lmx-dwell/` — promote |
| **PRD-5..7** | Dwell predictor, conformal, censoring | `I6` | I6 named "learned service times" and is gated on data |
| **PRD-8** | Cross-tenant transfer evaluation | — | New |
| **STL-1** | Savings statement | `W9`, `I4` | W9's scorecard is the internal half |
| **STL-2** | Baseline change control | `D3`(decision) | The shadow→cutover bar is the precedent |
| **STL-3** | Metering on the fee unit | `C3`, `F5` | Billing exists; re-key to per order ingested |
| **DEC-1** | SLA engine live | `E5`, `E11` | Engine done; **credit percentages blocked on the liability decision** |
| **DEC-2** | Batch-hold live | `E4`, `E10` | Logic verified; HOT_SHOT placeholders open |
| **DEC-3** | Optimiser live | `E1` | *"One command away"* |
| **DEC-4** | In-flight insertion | `F6`, `E12` | F6 explicitly depends on E1 |
| **DEC-5** | Fallback router | — | New |
| **DEC-6** | Fleet state | `E12` | Done |
| **CON-1** | Dispatcher board | `D1`, `D2`, `F2` | Done |
| **CON-2..3** | Override + reason code → training data | `I2` | Rule promotion exists; override capture does not |
| **CON-4** | Exception queue | `R5` | Done |
| **NTF-1** | Notification throttle | `C2` | Shipped |
| **TEN-1** | Three-level tenancy | `C4` | Client users exist; tenancy scoping does not |
| **TEN-2..5** | Pooling consent, leak tests, cross-tenant batching, audit | `W7` | W7 is the contract half; the code half is new |
| **ONB-1..3** | Feed onboarding, mapping, telemetry | `L19`, `ingestion registry` | Adapter registry is the foundation |
| **SUP-1** | Partner abstraction | `P1`, `P2` | Direct carry — rename `human_lmx_driver` |
| **SUP-2** | Pass-through accounting | `P5` | Partner settlement, reshaped to at-cost |
| **SUP-3** | Modality eligibility | `P3` | Direct carry |
| **SUP-4** | Payload-eligibility dataset | — | New |
| **SUP-5** | Autonomy adapter | `P2`, `P4` | Carry |

---

## 3. RETIRED — built for a business that no longer exists

Under 1.5 the customer keeps their drivers and LMX's own fleet is capped at 3.6% of flow. That orphans the following. **Retired means stop planning against it — not delete the code.**

| ID | Item | Why |
|---|---|---|
| **G1–G2, G5–G6, G8–G11, G13** | Gig demand path: notification intake, share-sheet vision extraction, per-job vehicle pinning, cross-platform itinerary, sibling refs, retention firewall, non-circumvention register, dual completion, platform standing | Premise was LMX drivers holding personal accounts on Curri/Dispatch/Roadie. Under 1.5 capacity is bought and passed through at cost. |
| **A10** | 1099 contractor onboarding / worker-autonomy counsel | No 1099 fleet at 3.6% cap. Reopen if the cap changes. |
| **A11** | Gig per-delivery pay model, `GigPayout`, `PayoutProvider` | LMX does not pay gig drivers; it buys capacity from platforms that do. |
| **F12** | Network/territory optimisation | Explicitly *"relevant once LMX runs multiple hubs."* There are no hubs. |
| **F8** | White-label / multi-brand portal theming | Franchise-model feature |
| **W8** | Epicor staging-module qualification | Already dropped by the July 28 decision log when W10 chose LMX-printed labels |

**Retained from the G-series as supply plumbing, not demand:** `G3` (GigJob model → generalise to a capacity job), `G4` (accept-gate → becomes buy-or-not), `G7` (deadhead cost model → carrier cost comparison), `G12` (density instrumentation). All four are already built.

---

## 4. SUPERSEDED — same problem, different shape

| ID | 1.0 shape | 1.5 shape |
|---|---|---|
| **E9** | Validate 2.5 deliveries per driver-hour | **Measured cost-per-drop delta against a control arm** (`EXP-1`, `STL-1`). DPH is a 1.0 denominator; the 1.5 claim is a cost delta |
| **R6** | Hub closure / holiday calendar | **Location closure calendar.** The model is right, the entity is wrong — rename hub → location. This is the case where a driver left a package outside a shop that closes on Mondays |
| **D1** | List-hubs endpoint | List-locations |
| **W2** | COD collection, cash custody, disputes | **Dormant.** LMX no longer stands between the distributor and their shop's money. Built and tested; leave it, do not extend |
| **F1** | Live driver location (30s pings) | **Stop events** (`DRV-1`). Pings stay for the ops map; they are not the measurement |
| **I8** | Manual capture of non-default training situations | Folded into `EXP-2` exploration policy — the same problem solved by randomisation rather than a checklist |

---

## 5. CARRIED and BLOCKED — the actual open list

**Carried, engineering:**
`E1` → DEC-3 · `E3` → ING-1 · `E10` → DEC-2 · `F6` → DEC-4 · `F14`+`W9` → DEC-0 · `I3` → IDN-4 · `I5` → PRD-1 · `I6` → PRD-5 · `P1`–`P5`,`P7` → SUP-1..5 · `S2`,`S3`,`S6` (infra, unchanged by the reposition) · `A1`,`A6` → DRV-2 · `C3` → STL-3 · `F10`,`F11` (deferred, unchanged)

**Carried, not engineering:**
`W7` → Phase 0.2 data-rights clause · `L8` → publish terms · `R1` insurance (downscoped to 3.6% fleet) · `R2` MVR screening (same) · `R3` privacy policy · `B1` senior backend hire · `B2` first contract → Phase 0.1 · `B4` Rippling (downscoped) · `B5` Twilio

**Blocked on a decision, not on work:**

| ID | Blocked on | Owner |
|---|---|---|
| `E11` SLA credit percentages | **Do we carry outcome liability?** If no, credits do not exist | Rich |
| `A9` state overtime rules | Launch location, and whether the 3.6% fleet is W2 | Sourabh + Matan |
| `DRV-2` background location | App Store justification + consent copy in the pilot terms | Sourabh + Matan |
| `I7` ops copilot | Nothing — just not gated work yet | — |
| `L24` referral programme | `L8`, and whether order flow is bought or earned | Matan |

---

## 6. Already done — do not re-plan

Confirmed shipped in `docs/ROADMAP.md`. Listed so no one re-opens them:

`G3 G4 G7 G12 · B3 · E4 E5 E6 E7 E8 E12 E13 · S1 S4 S5 S7 S8 S9 · D1 D2 D3 · A0 A2 A3 A4 A5 A7 A8 · C1 C2 C4 C5 · W1 W2 W3 W4 W5 W6 W10 W11 · R4 R5 R6 · F1 F2 F3 F4 F5 F7 F13 · I1 I2 I4 · T1 · L1–L7 L9–L23`

**Partial, finished in part:** `S2 S3 S6 A1 A6 C3 A9 A11 R3 L8` and Phase 7 W2 payroll.

**Phase 8 is marked ✅ DONE in its entirety.**

---

## 7. Document jurisdiction

| Document | Job |
|---|---|
| **`docs/ROADMAP_1.5.md`** | **The spine.** What is being built, in what order, gated on what. Authority for anything current. |
| `docs/ROADMAP.md` | **The item store and the archaeology.** Item detail, and the three founder decision logs that explain why things are the way they are. Authority for item history and for anything this reconciliation does not touch. |
| **This file** | The bridge. Authority for whether a 1.0 item is still live. |

The decision logs in `ROADMAP.md` (28 July, 3 August, 6–7 August) remain binding except where §3 retires the item they concern. They should not be moved or summarised — several record reversals in full, deliberately.
