# LMX Link — Order Intake Engineering and Design Plan

**Status:** Internal engineering and design plan. Not investor-facing. Not customer-facing.
**Date:** 2026-08-06
**Author:** Sourabh (CTO)
**Decisions taken at kickoff:** design the order contract against all three demand paths from day one · first release is a complete thin slice (intake → dispatch → driver → status back) · separate roadmap document, one shared codebase · not gated on the Dispatch field-test gates G1–G4.
**Companion docs:** `LMX_OS_Technical_Design_2.docx` (Component 1 — Order Ingestion, the optimizer, the driver app) · `claude/05_AGGREGATOR_CHANNEL_THESIS.md` (the aggregator path, which is one adapter on this contract) · `claude/04_AUTONOMY_INTEGRATION_PLAN.md` (modality fields carried on the order object).

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
  Aggregator push   │       enrich + geocode)      ├─ Driver app + POD      ├─ Shop SMS (Twilio)
  Epicor / MAM      │                              └─ Annotation capture    └─ Client dashboard
  EDI 204 (bought) ─┘
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
| **Economics** | `revenue_basis` (`per_drop` \| `per_mile` \| `contract`), `quoted_amount`, `cost_actuals` | Lets per-drop and per-mile revenue coexist in one margin report |

### 1.3 `sla_owner` — the field that lets all three paths coexist

Every source falls into one of two commitment models, and conflating them is the failure mode that would force a fork later.

| `sla_owner` | Who promised the customer | What the SLA engine does | Applies to |
|---|---|---|---|
| **`LMX`** | LMX did | Classifies urgency from customer profile, part type and deadline; sets the tier; owns the clock | Web form, CSV, REST webhook, Epicor/MAM — **the core LMX model** |
| **`EXTERNAL`** | Someone else did | Does **not** classify. Accepts the given window as a hard constraint and enforces it | Aggregator relay (Dispatch, Curri), enterprise API/EDI where the retailer sets the window |

The batch-hold queue works identically in both cases — it holds against whatever deadline is on the object. That is precisely why the aggregator arbitrage in doc 05 requires no new optimizer logic.

### 1.4 The status state machine — one, shared by every sink

`RECEIVED → ACCEPTED → HELD → ASSIGNED → EN_ROUTE_PICKUP → PICKED_UP → EN_ROUTE_DROP → DELIVERED`
Exception branches: `EXCEPTION_RAISED`, `RETURNED_TO_HUB`, `CANCELLED`.

Each sink adapter maps this to whatever vocabulary its consumer speaks. **Status write-back gets equal engineering weight to intake.** A carrier that takes orders and goes quiet is not a carrier — it is a favour. This is the half that gets cut under pressure and must not be.

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

### 2.3 What is deliberately *not* in v1

| Excluded | Why | When |
|---|---|---|
| Client login and full dashboard | The status link covers the real need at pilot scale | Phase 1 proper, as already planned |
| Multi-client commingling | Needs more than one live client on the front door | Phase 2, as planned |
| Per-client SLA terms configuration | Hardcode the Design Partner's terms; the data model already supports more | Phase 1 proper |
| Pricing, invoicing, billing | Contract-level, handled outside the system at this scale | Phase 2 |
| EDI | Buy it when a signed enterprise deal exists. Never build | On demand |

---

## Part 3 — Roadmap

**Track name:** LMX Link. **Shared codebase, shared backlog, separate milestone tracking.**

### 3.1 Sequence

| Wk | Milestone | Deliverable | Exit criterion |
|---|---|---|---|
| **0–1** | **T0 — Contract** | LMX Order Object v1 schema, status state machine, adapter interface, sink interface. Reviewed against all three demand paths in one session | Schema signed off by Sourabh. **Hard gate — nothing else starts** |
| **1–3** | **T1 — First adapter + first sink** | Web intake form, magic-link auth, address autocomplete and cache, shop memory, bulk paste. Public status link | A dispatcher enters a real order on a phone in under 60 seconds and can watch it |
| **2–4** | **T2 — Wire to the core** | Normalizer + geocoding enrichment. Orders flow into the existing hold queue and optimizer. Driver app reads POD requirements from the object | An order entered on the form appears on a driver's route, batched by the optimizer |
| **3–4** | **T3 — Second adapter** | CSV / email manifest / SFTP drop, with a row-level error report | A 40-row manifest imports, with bad rows reported and good rows dispatched |
| **4–5** | **T4 — Live pilot** | Design Partner overflow volume runs through LMX Link end to end | 50 real orders delivered through the front door, status written back on every one |
| **5–6** | **T5 — Third adapter** | Generic REST webhook, API key auth, public integration docs, webhook status callback | An external system POSTs an order and receives status callbacks without LMX assistance |

### 3.2 Gated work — not on this timeline

| Work | Gate | Owner of the gate |
|---|---|---|
| Aggregator adapter (Dispatch / Curri) | G1–G4 in doc 05 must pass on Rich's field data | Sourabh, on the data |
| EDI 204 / 214 / 210 via broker | A signed enterprise agreement exists. Buy, never build | Matan |
| Epicor / MAM connectors | Unchanged — Phase 1 as already planned | Sourabh |

### 3.3 Cost, stated honestly

The 3 engineer-week box named in doc 05 was for the aggregator adapter alone. **The full thin slice chosen at kickoff is roughly 7–9 engineer-weeks.**

| Component | Estimate |
|---|---|
| Contract design and review | 0.5 |
| Web intake form + magic link + status link | 2.5 |
| Normalizer, geocoding enrichment, core wiring | 1.5 |
| CSV / manifest adapter | 1.0 |
| Driver app POD configurability | 0.5 |
| Generic REST webhook + docs | 1.0 |
| Testing, pilot support, iteration | 1.5 |
| **Total** | **8.5 engineer-weeks** |

**The resourcing question this raises needs Matan.** Engineering hires land around month 5–6. If LMX Link starts before then, it is largely the CTO's hands, which is roughly two months of Sourabh not doing the rest of the CTO job during the Hub 1 build. That is a real trade and should be made explicitly, not by drift.

Three ways to resolve it, for Matan's call:

| Option | Pros | Cons |
|---|---|---|
| **CTO builds it now** | Fastest. No hiring dependency. Deep knowledge of the contract sits with the person who designed it | ~2 months of CTO attention during the Hub 1 build. Bus factor of one on the most reused component in the system |
| **Contract a front-end engineer for T1 and T3** | Preserves CTO time. The form and CSV parser are well-specified, self-contained work | Onboarding cost. Quality risk on the customer-facing surface. Cash out the door earlier than modelled |
| **Pull the first Full-Stack hire forward** | Permanent capacity, builds institutional knowledge | Payroll starts earlier than the financial model assumes. Needs a model update |

### 3.4 Success metrics

| Metric | Target | Why it is the right measure |
|---|---|---|
| Time from "new customer says yes" to first order delivered | **Same day**, zero customer IT involvement | This is the entire point of LMX Link |
| Order entry time, second order onward | **Under 30 seconds** | Counter staff abandon anything slower |
| Orders requiring manual orchestrator correction | **Under 5%** | Measures whether the normalizer actually works |
| Status write-back latency, event to visible | **Under 30 seconds** | What makes LMX read as a carrier rather than a favour |
| Adapter changes requiring core code changes | **Zero** | The architecture succeeded or it did not |

---

## Part 4 — Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope creep from "design for all three."** Designing the contract for three paths quietly becomes building three adapters in sprint one | High | The contract is designed once against all three. Adapters ship in the T1/T3/T5 sequence. Aggregator and EDI stay gated |
| **The form gets used as an excuse to delay the Epicor connector** | Medium | Epicor remains Phase 1 committed. LMX Link widens the funnel; it does not replace the integration the core market needs |
| **CTO attention displaced during Hub 1 build** | High | Explicit resourcing decision in 3.3 — Matan's call, this week |
| **Status write-back cut under schedule pressure** | Medium–High | It is an exit criterion on T1 and T4, not a follow-up ticket |
| **A product-tier name leaks into a customer conversation and reframes LMX as software** | Low, now mitigated | Settled in Part 0. LMX Link is internal only; the customer-facing framing is "how you send us orders," never a named product |
| **Contract v1 proves wrong and forces a migration** | Medium | The `sla_owner` split and per-source POD config are the two places this most likely breaks. Both are deliberately designed for now rather than retrofitted |

---

## Action items

| What | Who | By when |
|---|---|---|
| ~~Name the track~~ — **DONE 2026-08-06: LMX Link.** Retire "LMX Lite" everywhere | Sourabh | Complete |
| Note the LMX Link naming decision to Matan and Rich so it is used consistently | Sourabh | This week |
| Resource decision — CTO builds, contractor, or pull the Full-Stack hire forward (3.3) | **Matan** | This week — blocks the T0 start date |
| Draft LMX Order Object v1 and the status state machine, reviewed against all three demand paths | Sourabh | 1 week |
| Confirm the reading of "design for all three" — contract designed for three, adapters sequenced | Sourabh + Matan | This week |
| Update the financial model if the Full-Stack hire moves forward | Matan + Sourabh | On resourcing decision |
| Hold the aggregator adapter until G1–G4 pass | Sourabh | Standing |
| Log the LMX Link track and its scope boundary in `02_DECISION_LOG.md` | Sourabh | On resourcing decision |
