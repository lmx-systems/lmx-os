# LMX Link — Order Intake Engineering and Design Plan

**Status:** Internal engineering and design plan. Not investor-facing. Not customer-facing.
**Date:** 2026-08-06. **Revised 2026-08-31** — money-movement scope added (new Part 3), roadmap and cost updated, §2.3 exclusion partially reversed.
**Author:** Sourabh (CTO)
**Decisions taken at kickoff:** design the order contract against all three demand paths from day one · first release is a complete thin slice (intake → dispatch → driver → status back) · separate roadmap document, one shared codebase · not gated on the Dispatch field-test gates G1–G4.
**Companion docs:** `LMX_OS_Technical_Design_2.docx` (Component 1 — Order Ingestion, the optimizer, the driver app) · `claude/05_AGGREGATOR_CHANNEL_THESIS.md` (the aggregator path, one adapter on this contract) · `claude/04_AUTONOMY_INTEGRATION_PLAN.md` (modality fields on the order object) · `claude/08_FINANCE_OPERATING_PLAN.md` (the bought finance stack this hands off to) · `claude/09_AUGUST_2026_BUSINESS_PLAN_RESET.md` (the tiered pricing change that forced Part 3).

**Revision note (2026-08-31):** Parts renumbered. The former Part 3 (Roadmap) is now Part 4; the former Part 4 (Risks) is now Part 5. New Part 3 covers money movement.

---

## 0. Naming — decided

**The intake track is named LMX Link.** Decided 2026-08-06. The working title "LMX Lite" is retired and should not be used again, internally or externally: "Lite" reads as a software tier, implies a Pro version exists, and invites a conversation about fees, seats and features that contradicts LMX's locked position as an operator that never sells or licenses software.

| Audience | What this is called |
|---|---|
| Internal engineering, this doc, the backlog | **LMX Link** |
| A customer | Nothing. It has no name. It is *"how you send us orders"* — a step in onboarding, never a product or a tier |
| An investor | Nothing separately named. It is order ingestion, Component 1 of LMX OS |

**Terms retired with this decision:** "LMX Lite", "LMX OS Lite", "Lite intake".

**Considered and rejected:** *LMX Relay* — the strongest freight-native option, rejected because Home Depot already uses "Relay" for its overnight trailer programme. *LMX Bridge*, *LMX Connect* — both read as integration middleware. *LMX Dispatch* — collides with Dispatch, a network LMX may transact with. *LMX Desk*, *LMX Send* — both viable; held in reserve if Link ever needs to become customer-visible.

Everything below uses **LMX Link** for the intake work.

---

## Part 1 — The architecture

### 1.1 The one principle

**One canonical order object. Many source adapters. Many status sinks. The core never knows where an order came from.**

```
  SOURCES                    CONTRACT                 CORE (unchanged)              SINKS
  ─────────                  ────────                 ────────────────              ─────
  Web intake form  ─┐                              ┌─ SLA engine            ┌─ Status link (web)
  CSV / email       ├─►  LMX Order Object  ──────► ├─ Batch-hold queue   ──►├─ Webhook callback
  REST webhook      │      (normalize +            ├─ Fleet optimizer       ├─ Aggregator status push
  Aggregator push   │       enrich + geocode)      ├─ Rating engine  (new)  ├─ Shop SMS (Twilio)
  Epicor / MAM      │                              ├─ Driver app + POD      ├─ Client dashboard
  EDI 204 (bought) ─┘                              └─ Annotation capture    └─ Priced statement (new)
```

If an adapter ever needs a change inside the SLA engine, hold queue, or optimizer, **the contract is wrong** — fix the contract, not the core. That rule is the whole design.

### 1.2 The LMX Order Object v1 — designed against all three paths

This is the gating artifact. Nothing else starts until it is signed off.

| Group | Fields | Design note |
|---|---|---|
| **Identity** | `lmx_order_id`, `source_system`, `source_order_ref`, `client_id`, `received_at` | `source_system` is the only place the origin is ever recorded |
| **Origin** | `pickup_location_id` or ad-hoc `pickup_address`, `pickup_contact`, `ready_at`, `pickup_window_start/end` | Ad-hoc pickup is what makes a retail store origin work without pre-registration |
| **Destination** | `drop_address_raw`, `drop_lat/lng`, `drop_contact`, `access_notes`, `delivery_window_start/end` | Geocode once on first order per address, cache and reuse (existing Component 1 behaviour) |
| **Commitment** | `sla_owner` (`LMX` \| `EXTERNAL`), `sla_tier`, `external_window_start/end`, `promised_at` | **The single most important field in the design — see 1.3** |
| **Payload** | `line_items[]` (description, qty, `size_class`, weight, dims), `total_weight`, `required_vehicle_class` | Feeds capacity constraints and, later, autonomy eligibility |
| **Modality** | `modality_eligible[]`, `modality_assigned` | The two fields already agreed for the Phase 1 data model in the autonomy plan. Carried now, used later |
| **Proof** | `pod_photo_count_required`, `pod_photo_subjects[]`, `signature_required` | Configurable per source. Dispatch mandates four photos with specified subjects; the Design Partner may want one. The driver app reads this, it is never hardcoded |
| **Status** | `current_status`, `status_history[]` | One state machine, shared by every sink |
| **Economics** | `revenue_basis` (`per_drop` \| `per_mile` \| `contract`), `quoted_amount`, `cost_actuals` | **Extended 2026-08-31** — see 1.5 |

### 1.3 `sla_owner` — the field that lets all three paths coexist

Every source falls into one of two commitment models, and conflating them is the failure mode that would force a fork later.

| `sla_owner` | Who promised the customer | What the SLA engine does | Applies to |
|---|---|---|---|
| **`LMX`** | LMX did | Classifies urgency from customer profile, part type and deadline; sets the tier; owns the clock | Web form, CSV, REST webhook, Epicor/MAM — **the core LMX model** |
| **`EXTERNAL`** | Someone else did | Does **not** classify. Accepts the given window as a hard constraint and enforces it | Aggregator relay (Dispatch, Curri), enterprise API/EDI where the retailer sets the window |

The batch-hold queue works identically in both cases — it holds against whatever deadline is on the object. That is precisely why the aggregator arbitrage in doc 05 requires no new optimizer logic.

`sla_owner` also governs pricing. Where `sla_owner = LMX`, the rating engine sets the price. Where `sla_owner = EXTERNAL`, the payout is given by the source and the rating engine records rather than computes it.

### 1.4 The status state machine — one, shared by every sink

`RECEIVED → ACCEPTED → HELD → ASSIGNED → EN_ROUTE_PICKUP → PICKED_UP → EN_ROUTE_DROP → DELIVERED`
Exception branches: `EXCEPTION_RAISED`, `RETURNED_TO_HUB`, `CANCELLED`.

Each sink adapter maps this to whatever vocabulary its consumer speaks. **Status write-back gets equal engineering weight to intake.** A carrier that takes orders and goes quiet is not a carrier — it is a favour. This is the half that gets cut under pressure and must not be.

`DELIVERED` is also the billable event. `EXCEPTION_RAISED`, `RETURNED_TO_HUB` and `CANCELLED` each carry a billing consequence defined in Part 3.

### 1.5 Economics fields — extended 2026-08-31

The original three fields are insufficient once pricing is distance-tiered. Revised set:

| Field | Purpose |
|---|---|
| `revenue_basis` | `per_drop` \| `per_mile` \| `contract` \| `external` |
| `rate_card_id`, `rate_version_id` | **Which version of which card priced this drop.** Makes an invoice reproducible against the rate in effect on the delivery date |
| `zone` | Derived at intake from road distance |
| `billable_distance_mi` | Road distance pickup → drop, stored, not recomputed later |
| `quoted_amount` | Price shown at intake |
| `discount_applied`, `discount_reason` | Volume tier, if any |
| `adjustments[]` | Each with `type`, `amount`, `reason_code`, `applied_by`, `applied_at` |
| `sla_credit_amount`, `sla_credit_reason` | Populated automatically on a missed window |
| `net_billable_amount` | Quoted, less credits and adjustments |
| `cost_actuals` | `driver_cost`, `mileage_cost`, `fuel`, `tolls`, `partner_cost` — each tagged `hub_id`, `vehicle_id` |
| `billing_period` | Assigned by the cut-off rule, not derived at query time |

---

## Part 2 — The design plan

### 2.1 Who actually uses each surface

| Surface | Real user | Context of use | Design consequence |
|---|---|---|---|
| **Intake form** | A parts counter person or warehouse dispatcher | Standing, phone or tablet, customer on the line, under time pressure | Mobile-first. Sub-60-second entry. No mouse assumptions |
| **Status link** | The same person, plus their manager | Checking "where is it" while a customer waits | No login. One link. Loads in under two seconds |
| **Driver app** | LMX driver | In a van, gloves, poor signal | Already specified in Phase 1. Only change: read POD requirements from the order object |
| **CSV / manifest drop** | A back-office person or a scheduled export | Once or twice a day, batch | Forgiving parser. Never silently drop a row |

### 2.2 Intake form — design principles

1. **No account creation.** A per-client magic link. Account creation at a parts counter is where adoption dies.
2. **Address first, everything else optional.** Autocomplete on the drop address; every other field has a sensible default.
3. **Remember every shop.** Second order to the same shop is two taps. Most distributors deliver to the same 40–80 shops forever.
4. **Deadline as a choice, not a datetime picker.** "Now / within the hour / today / tomorrow" maps to SLA tiers. Nobody at a counter operates a calendar widget.
5. **Bulk paste.** A dispatcher with six orders pastes six lines. Parse them, show what was understood, let them fix it.
6. **Confirmation shows the commitment**, not a spinner. "Picked up by 2:40, delivered by 3:25." That is what makes it feel like a carrier.
7. **Never block on a missing field.** Take the order, flag the gap to the LMX orchestrator, chase it out of band. An order refused at intake is a lost customer.
8. **Price is shown, never negotiated.** Added 2026-08-31. Under tiered pricing the confirmation shows the price alongside the commitment. It is displayed as a fact, not an editable field. Any override is an orchestrator action with an audit trail, never a counter-staff action.

### 2.3 What is deliberately *not* in v1

**Revised 2026-08-31.** The row on pricing and billing is partially reversed — see the note below the table.

| Excluded | Why | When |
|---|---|---|
| Client login and full dashboard | The status link covers the real need at pilot scale | Phase 1 proper, as already planned |
| Multi-client commingling | Needs more than one live client on the front door | Phase 2, as planned |
| Per-client SLA terms configuration | Hardcode the Design Partner's terms; the data model already supports more | Phase 1 proper |
| **Invoice generation, collection, dunning, ledger** | Commodity. Bought, per the locked build-vs-buy decision | Never built |
| ~~Pricing, invoicing, billing~~ | ~~Contract-level, handled outside the system at this scale~~ | **Partially reversed — see below** |
| EDI | Buy it when a signed enterprise deal exists. Never build | On demand |

> **Decision change, 2026-08-31.** The original exclusion assumed one flat rate per client, where "handled outside the system" was true. The 2026-08-29 pricing decision replaces that with five distance zones and three volume-discount tiers, which means **every drop needs a price computed from its distance at intake**. No human at a parts counter does that arithmetic. Rating and statement generation therefore move into v1 as milestone T2.5. Invoice documents, collection, dunning and the general ledger remain excluded permanently — they are the commodity layer the locked build-vs-buy decision says to buy. Logged in `02_DECISION_LOG.md`.

---

## Part 3 — Money movement in LMX OS

*New, 2026-08-31. Arises from three inputs: the tiered pricing decision (`09`, §3), the finance-stack architecture (`08`, Part 3), and the already-logged open item "LMX OS gains authority over order acceptance and dynamic pricing — engineering scope change, Sourabh to size."*

### 3.1 The boundary — one line

> **LMX OS builds the number. Bought systems do everything that happens to the number afterwards.**

```
   LMX OS  (BUILD)                     │   Bought  (BUY)
   ────────────────────────────────────┼──────────────────────────────────
   order → delivery → SLA record       │
        → rated at intake              │
        → credits and adjustments      │
        → PRICED STATEMENT  ───────────┼──→ invoice → cash → general ledger
                                       │    QuickBooks · Bill.com · ADP/Gusto
                                       │    · ACH rails · controller
```

This is not a new decision. It is the July locked build-vs-buy rule applied to money: **buy the commodity layer, build Layer 2 intelligence.** Rating against an SLA is Layer 2. Producing a PDF and chasing a payment is commodity.

### 3.2 Two hard rules

| Rule | Why |
|---|---|
| **LMX OS never moves money.** It computes amounts and emits records. Bank rails, payouts and collection all execute in bought systems | Keeps LMX OS out of scope for money-transmission questions, and keeps a software bug from becoming a missed driver payment |
| **Card and bank feeds go straight to the accounting system, never through LMX OS** | Fuel cards, tolls, spend cards and bank feeds have pre-built connectors to QuickBooks. Routing them through LMX OS would be building an importer nobody needs. The exception is per-drop fuel and toll cost captured on the order for cost-per-drop, which is an operational measure, not an accounting feed |

### 3.3 Full feature inventory

Every money-related capability LMX needs, where it lives, and when it lands. **T2.5** and **T6** are milestones in Part 4. "Bought" means it is never built.

#### A — Rating and quoting *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| A1 | Rate card as **versioned, effective-dated data** — zones, prices, discount tiers. Never hardcoded | **T2.5** |
| A2 | Road distance pickup → drop, computed once at intake and stored as `billable_distance_mi` | **T2.5** |
| A3 | Zone assignment from distance | **T2.5** |
| A4 | Volume discount tiers — standing route, committed programme, dedicated route | **T2.5** |
| A5 | Quote at intake: populate `quoted_amount`, `rate_card_id`, `rate_version_id`, `zone` | **T2.5** |
| A6 | Per-client rate card override | T6 |
| A7 | Surcharge library — after-hours, oversize / vehicle class, multi-piece, return leg | T6 |
| A8 | Minimum charge and monthly minimums | T6 |
| A9 | Margin-aware accept / reject, off the five inputs in the 2026-08-27 strategy decision | Phase 2 |

#### B — Billable events and adjustments *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| B1 | `DELIVERED` generates a billable event | **T2.5** |
| B2 | **SLA breach generates a credit automatically**, with reason code, read from the per-client SLA terms data model | **T2.5** |
| B3 | Basic exception adjustments — wait time, failed delivery, cancelled after dispatch | **T2.5** |
| B4 | Manual adjustment with `applied_by`, timestamp and reason code | **T2.5** |
| B5 | `RETURNED_TO_HUB` and core / warranty return-leg billing | T6 |
| B6 | Redelivery charging rules | T6 |

**B2 is not optional.** The PR/FAQ sells a committed SLA. A credit a client has to chase is worse than a promise never made.

#### C — Statement and export *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| C1 | Billing cut-off and timezone rules — UTC plus local stored, `billing_period` assigned deterministically | **T2.5** |
| C2 | Monthly statement per client, with per-store / sub-account breakdown | **T2.5** |
| C3 | Statement export to a fixed schema (the data contract in `08` Part 5) | **T2.5** |
| C4 | **Reproducibility** — re-running a closed period returns identical figures | **T2.5** |
| C5 | Unique transaction identifiers for cash application, and reconciliation keys back to delivery event IDs | **T2.5** |
| C6 | Weekly and custom billing cycles | T6 |
| C7 | API endpoint replacing the file export | Phase 2, at ~20 clients |

#### D — Cost capture *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| D1 | `cost_actuals` on every order — driver cost, mileage, fuel, tolls | **T2.5** |
| D2 | Every cost tagged `hub_id` and `vehicle_id` | **T2.5** |
| D3 | Multi-client cost allocation across a commingled run | T6, with commingling |
| D4 | Cost-per-drop feed to Spartan, by hub, client, zone and modality | T6 |

**D1 and D2 are the reason cost per drop is computable at all.** They are cheap alongside the rating work and expensive to retrofit — the same argument that made the multi-client SLA data model a locked decision in July.

#### E — Worker payouts *(LMX OS computes, bought systems pay)*

| # | Feature | Phase |
|---|---|---|
| E1 | Worker record — employment type (W-2 / 1099), home state, pay basis | T6 |
| E2 | Driver pay calculation — per-drop, per-hour or hybrid, with surge | T6 |
| E3 | Gig-overflow payout per job, for the fourth modality adopted 2026-08-27 | T6 |
| E4 | Payout file export to the payroll provider | T6 |
| E5 | Reimbursements and expense pass-through | T6 |
| E6 | Fast or daily pay rail | Phase 3, only if driver retention requires it |
| — | **Payroll execution, tax withholding, filings, 1099s** | **Bought** — ADP / Gusto, per the locked build-vs-buy decision |

#### F — Partner settlement *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| F1 | Autonomy partner cost per trip or per vehicle-hour, recorded on the order | Phase 3 |
| F2 | Reconcile a partner's invoice against LMX delivery records | Phase 3 |
| F3 | Fallback-van substitution cost attribution when a partner cannot deliver | Phase 3 |

Gated on a signed autonomy partner. Nothing here starts before one exists.

#### G — Tax *(mostly bought)*

| # | Feature | Phase |
|---|---|---|
| G1 | Taxability flag by state carried on the statement line | **T2.5** — flag only, no calculation |
| — | Tax determination, calculation, filing, nexus | **Bought** — provider plus a licensed US practitioner |
| — | 1099 threshold tracking | **Bought** — payroll provider |

#### H — Controls and audit *(LMX OS)*

| # | Feature | Phase |
|---|---|---|
| H1 | Immutable pricing audit trail — which rate version priced which drop, on which date | **T2.5** (falls out of A1) |
| H2 | Approver role required on manual adjustments and price overrides | **T2.5** |
| H3 | Statement reproducibility as an audit control | **T2.5** (= C4) |
| H4 | LMX holds all funds; no vendor custody | Standing architectural rule |

#### I — Bought, never built

General ledger · accounts payable · invoice document generation and delivery · dunning and collection · ACH and payment rails · payroll execution · fuel, toll and spend card feeds · bank feeds · tax filing.

### 3.4 What T2.5 deliberately does not do

Doc 09 gives LMX OS authority to decide **whether to accept an order**, off five inputs: SLA window, fleet location and capacity, distance and density impact, required margin, and modality. **T2.5 builds only the pricing input.** Acceptance logic, margin thresholds and capacity-aware routing are Phase 2 (A9). Stating this here so the milestone does not grow into the full decision engine.

---

## Part 4 — Roadmap

**Track name:** LMX Link. **Shared codebase, shared backlog, separate milestone tracking.**

### 4.1 Sequence

| Wk | Milestone | Deliverable | Exit criterion |
|---|---|---|---|
| **0–1** | **T0 — Contract** | LMX Order Object v1 schema **including the extended economics fields in 1.5**, status state machine, adapter interface, sink interface. Reviewed against all three demand paths in one session | Schema signed off by Sourabh. **Hard gate — nothing else starts** |
| **1–3** | **T1 — First adapter + first sink** | Web intake form, magic-link auth, address autocomplete and cache, shop memory, bulk paste. Public status link | A dispatcher enters a real order on a phone in under 60 seconds and can watch it |
| **2–4** | **T2 — Wire to the core** | Normalizer + geocoding enrichment. Orders flow into the existing hold queue and optimizer. Driver app reads POD requirements from the object | An order entered on the form appears on a driver's route, batched by the optimizer |
| **3–4** | **T3 — Second adapter** | CSV / email manifest / SFTP drop, with a row-level error report | A 40-row manifest imports, with bad rows reported and good rows dispatched |
| **4–5** | **T2.5 — Rating & Statement** *(new)* | Features **A1–A5, B1–B4, C1–C5, D1–D2, G1, H1–H3**. Rate card as versioned data, quote at intake, automatic SLA credits, cost capture, monthly priced statement export | A drop entered on the form carries a correct zone price at intake. A full month exports as one priced, credit-netted statement that reconciles to the delivery records, and re-running the period returns identical figures |
| **5–6** | **T4 — Live pilot** | Design Partner overflow volume runs through LMX Link end to end, **priced** | 50 real orders delivered through the front door, status written back on every one, every one carrying a price and a cost |
| **6–7** | **T5 — Third adapter** | Generic REST webhook, API key auth, public integration docs, webhook status callback | An external system POSTs an order and receives status callbacks without LMX assistance |
| **Phase 2** | **T6 — Money Movement** | Features **A6–A8, B5–B6, C6, D3–D4, E1–E5**. Per-client cards, surcharges, commingled cost allocation, Spartan feed, driver and gig payout calculation and export | A commingled run allocates cost across three clients. A payout file imports cleanly into the payroll provider |

**T2.5 sits before T4 deliberately.** T4 is the live pilot with real Design Partner volume. Real drops need real prices. If rating lands after the pilot, the first live orders get priced by hand and reconciled manually for months.

### 4.2 Gated work — not on this timeline

| Work | Gate | Owner of the gate |
|---|---|---|
| Aggregator adapter (Dispatch / Curri) | G1–G4 in doc 05 must pass on Rich's field data | Sourabh, on the data |
| EDI 204 / 214 / 210 via broker | A signed enterprise agreement exists. Buy, never build | Matan |
| Epicor / MAM connectors | **Closed out 2026-08-27** — schema captured, direction not taken | — |
| **Rate card values** | The tiered card is **Proposed**, not adopted. T2.5 builds the engine; the numbers are configuration | **Rich + Matan** (commercial) |
| **Partner settlement (F1–F3)** | A signed autonomy partner exists | Matan |
| **Accept / reject engine (A9)** | Phase 2, and needs a margin threshold nobody has set | Sourabh + Matan |
| **Fast / daily driver pay (E6)** | Only if retention data shows it is needed | Rich |

**On the rate card:** because the card is still Proposed, T2.5 must be built **card-agnostic**. Zones, prices, thresholds and discount tiers are data with effective dates. When Rich and Matan settle the numbers, that is a configuration change, not an engineering change. This is the single most important design constraint on T2.5.

### 4.3 Cost, stated honestly

The 3 engineer-week box named in doc 05 was for the aggregator adapter alone.

| Component | Estimate |
|---|---|
| Contract design and review | 0.5 |
| Web intake form + magic link + status link | 2.5 |
| Normalizer, geocoding enrichment, core wiring | 1.5 |
| CSV / manifest adapter | 1.0 |
| Driver app POD configurability | 0.5 |
| **T2.5 — Rating & Statement** | **3.0** |
| Generic REST webhook + docs | 1.0 |
| Testing, pilot support, iteration | 1.5 |
| **Total (v1 track)** | **11.5 engineer-weeks** |
| T6 — Money Movement (Phase 2, not in the v1 total) | 3.5 |

**Estimate revised upward, honestly.** An earlier working figure of 2.0 weeks for T2.5 was set before the feature inventory in 3.3 existed. With cost capture, reproducibility and the audit trail included it is 3.0. The v1 track moves 8.5 → **11.5**.

**The resourcing question this raises needs Matan, and is now overdue.** It was assigned 2026-08-06 marked "this week — blocks the T0 start date," and is unresolved as of 2026-08-31. Engineering hires land around month 5–6 under the previous plan. If LMX Link starts before then it is largely the CTO's hands — now closer to three months than two.

Three ways to resolve it, for Matan's call:

| Option | Pros | Cons |
|---|---|---|
| **CTO builds it now** | Fastest. No hiring dependency. Deep knowledge of the contract sits with the person who designed it | ~3 months of CTO attention during the Hub 1 build. Bus factor of one on the most reused component in the system |
| **Contract a front-end engineer for T1 and T3** | Preserves CTO time. The form and CSV parser are well-specified, self-contained work | Onboarding cost. Quality risk on the customer-facing surface. Cash out earlier than modelled |
| **Pull the first Full-Stack hire forward** | Permanent capacity, builds institutional knowledge | Payroll starts earlier than modelled. Needs a model update |

**Budget note:** the ~$587K-through-Phase-2 figure previously used to frame this trade is **superseded** (`02_DECISION_LOG.md`, August 2026). The raise shape is itself an open question. Frame the trade in engineer-weeks and CTO attention, not against a budget number that no longer describes the company.

**What does not compete:** the one-driver, one-vehicle DPH proof recommended in `09` §6.3 is an operations experiment, not an engineering one. It should run in parallel and must not wait on LMX Link.

### 4.4 Success metrics

| Metric | Target | Why it is the right measure |
|---|---|---|
| Time from "new customer says yes" to first order delivered | **Same day**, zero customer IT involvement | This is the entire point of LMX Link |
| Order entry time, second order onward | **Under 30 seconds** | Counter staff abandon anything slower |
| Orders requiring manual orchestrator correction | **Under 5%** | Measures whether the normalizer actually works |
| Status write-back latency, event to visible | **Under 30 seconds** | What makes LMX read as a carrier rather than a favour |
| Adapter changes requiring core code changes | **Zero** | The architecture succeeded or it did not |
| **Delivered drops carrying a price at delivery time** | **100%** | A drop priced later is a drop priced by hand |
| **SLA credits applied without human intervention** | **100%** | The promise in the PR/FAQ is only real if this holds |
| **Manual price adjustments** | **Under 2% of drops** | Above that, the rate card is wrong, not the system |
| **Time to produce a client statement** | **Under 10 minutes** | If it takes a day, it will be skipped |
| **Closed-period reproducibility** | **Identical figures on re-run** | The audit control. Fails once, trust is gone |

---

## Part 5 — Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope creep from "design for all three."** Designing the contract for three paths quietly becomes building three adapters in sprint one | High | The contract is designed once against all three. Adapters ship in the T1/T3/T5 sequence. Aggregator and EDI stay gated |
| **CTO attention displaced during Hub 1 build** — now 11.5 engineer-weeks | High | Explicit resourcing decision in 4.3 — Matan's call, three weeks overdue |
| **T2.5 grows into a billing platform.** Invoice documents, dunning and ledger creep in because they feel adjacent | **High** | The §2.3 exclusion list is permanent for those items. Boundary restated in 3.1. Any ticket proposing invoice generation gets closed, not backlogged |
| **The rate card is not decided.** T2.5 has no numbers to build against | **High** | Build card-agnostic — 4.2. Engine ships on placeholder values; real numbers are configuration |
| **Cost capture (D1–D2) gets cut under schedule pressure** | **High** | Without it cost per drop is uncomputable and Spartan has no input. It is an exit criterion on T2.5, not a follow-up ticket |
| **Money movement creeps into LMX OS** — someone builds a payout rail or a bank integration | **High** | Hard rule in 3.2. LMX OS computes; bought systems move money |
| **Status write-back cut under schedule pressure** | Medium–High | Exit criterion on T1 and T4, not a follow-up ticket |
| **The form gets used as an excuse to delay integration work the core market needs** | Medium | LMX Link widens the funnel; it does not replace connector work |
| **Contract v1 proves wrong and forces a migration** | Medium | `sla_owner`, per-source POD config and the rate-version fields are the three places this most likely breaks. All three are designed for now rather than retrofitted |
| **Pricing shown at intake becomes a negotiation** with counter staff | Medium | Price is display-only on the form. Overrides are an orchestrator action with an audit trail — 2.2 principle 8 |
| **A product-tier name leaks into a customer conversation** and reframes LMX as software | Low, mitigated | Settled in Part 0 |

---

## Action items

| What | Who | By when |
|---|---|---|
| ~~Name the track~~ — **DONE 2026-08-06: LMX Link.** Retire "LMX Lite" everywhere | Sourabh | Complete |
| ~~Epicor / MAM connector direction~~ — **CLOSED 2026-08-27**, schema captured, direction not taken | — | Complete |
| **Resource decision — CTO builds, contractor, or pull the Full-Stack hire forward (4.3)** | **Matan** | **Overdue since 2026-08-06 — blocks T0** |
| **Decide the tiered rate card values** so T2.5 has real configuration | **Rich + Matan** | This week |
| Draft LMX Order Object v1 including the extended economics fields (1.5), reviewed against all three demand paths | Sourabh | 1 week |
| Write the T2.5 spec — rate card schema and versioning, zone assignment, credit rules, statement export fields | Sourabh | 1 week |
| Confirm the reading of "design for all three" — contract designed for three, adapters sequenced | Sourabh + Matan | This week |
| Trim `08` Part 6 so it points at T2.5 rather than naming a separate Phase 1 backlog | Sourabh | With the T2.5 spec |
| Confirm the DPH proof runs in parallel and is not waiting on LMX Link | **Rich** | This week |
| Hold the aggregator adapter until G1–G4 pass | Sourabh | Standing |
| Log the §2.3 decision change and the T2.5 scope in `02_DECISION_LOG.md` | Sourabh | This week |
