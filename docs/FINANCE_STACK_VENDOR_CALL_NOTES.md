# LMX — Finance Stack Vendor Call: Naveh (Param) — Discussion Notes

**Status:** Internal call record. Not investor-facing.
**Date of call:** 2026-08-28
**Attendees:** Sourabh Miglani (CTO, LMX); Param (Naveh)
**Purpose of call:** Explore whether Naveh could stand up LMX's financial back office — accounts payable, receivables, worker payments, taxes — and understand how their systems would interact with LMX OS.

**Note:** This document records the conversation, an evaluation of Param, and the suggestions he made. Decisions, architecture, risks and next steps arising from the call are held separately in `08_FINANCE_OPERATING_PLAN.md`.

---

## 1. Who we met

| | |
|---|---|
| **Name** | Param |
| **Company** | Naveh — a venture operating under a parent finance-services firm. Parent company name was not clearly captured on the call and remains unconfirmed |
| **Location** | New Delhi, India |
| **Experience** | ~14 years in finance and accounting. IBM (5 years), American Express (2 years), other companies (~7 years). Senior controller at the parent firm before Naveh |
| **Stated expertise** | US GAAP, month-end close, US taxation and compliance, setting up finance systems for early-stage companies and guiding them to a streamlined process |
| **Industries worked in** | E-commerce, logistics, retail, SaaS |
| **Public presence** | Naveh's website is a placeholder page. No public product information, team, pricing or customer references were found |

---

## 2. How the conversation ran

Param opened by asking for our forecast — expected expenses, revenue, number of invoices, number of customers — on the basis that system recommendations should follow from volume. He did not pitch before asking.

We described LMX: a delivery operator charging per completed drop against a committed delivery-time guarantee, with a small number of clients, high underlying transaction volume, a fleet, and a mix of employed and contract drivers.

He then walked through his recommended stack in sequence — payables, accounting platform, payment receipt, invoicing — and stopped at invoicing to ask how our own system works. The second half of the call was about the interface between LMX OS and the accounting stack.

Sourabh raised the build-versus-buy question directly, naming the founding team's Amazon bias toward building rather than licensing. Param's response stayed within his own competence: he advised removing manual intervention and linking systems where possible, and offered to produce a process map showing the ideal flow with LMX OS slotted into it. He did not attempt to argue architecture beyond that.

The call ended with Param raising cash application — the need for unique identifiers so a received payment can be matched to the right invoice, customer and project — and offering to define what information he would need for a clean accounting process.

---

## 3. Topics covered — and topics not reached

| Covered | Not reached |
|---|---|
| Accounts payable and vendor payments | Driver and gig-worker payments |
| Accounting platform selection | Worker classification (W-2 vs 1099) |
| Payment receipt and invoicing architecture | Multi-state payroll and registrations |
| Integration approach and cadence | Sales-tax treatment of delivery services |
| Timeline and process mapping | Working capital timing |
| Cash application and identifiers | Fee and pricing — no number was given |

The call ran out of time before the second column. Param acknowledged this and proposed a follow-up session to work through what information he would need.

---

## 4. Suggestions Param made

### 4.1 Accounts payable

- Under roughly ten vendor invoices a month, manage manually: maintain an open vendor list of outstanding payments, share it with the client for approval, then set payments up on the bank for the client to log in and approve.
- Above that threshold, use Bill.com. His reasoning: a strong approval matrix, a dedicated intake email address that can be shared with vendors so invoices arrive directly, automatic routing for approval based on an agreed authority matrix, automatic payment on the due date, and a linked bank account for auto-debit. He noted it handles credit notes and payments cleanly and is what most companies he works with use.

### 4.2 Accounting platform

- QuickBooks Online, on the basis that LMX is starting from scratch and QuickBooks integrates with most of the other platforms he would recommend, including Bill.com.

### 4.3 Payment receipt and invoicing

- Stripe for receiving customer payments and, if needed, raising invoices.
- A third-party connector, at small cost, to sync Stripe activity into QuickBooks so that high transaction counts flow through to the books and can be reconciled monthly.
- QuickBooks native invoicing only if invoice volume is low.
- **Recognised that high-volume invoicing must originate in LMX OS.** He asked directly how customers register, where the contract is raised, and what triggers invoicing — and did not propose replacing any of it.

### 4.4 Integration between LMX OS and the accounting stack

- Preference for a direct integration with regular syncing — daily, or every few hours — over relying on a manual monthly report.
- Rationale given: less manual effort, fewer hours consumed, easier reconciliation.
- Alternative accepted: a periodic report (weekly or monthly) listing invoices with the service period and what each invoice relates to, sufficient for revenue recognition and booking.

### 4.5 Cash application

- Raised at the close of the call: unique identifiers per transaction so that a received payment can be matched automatically to the correct invoice, customer and project. He proposed sitting down to define exactly what information is required for an efficient accounting process.

### 4.6 Timeline

- Setting up the tools themselves — Bill.com, Stripe, QuickBooks — **two days**.
- Designing the process, deciding what links to what and what information passes between systems — **one to two months**, depending on how frequently our internal platform changes.
- Initial processes in place within two to three months; scaled operation around three to four months.
- Offered to produce a **process map** describing the ideal process, with our internal platform fitted into it.

---

## 5. Observations on the suggestions

Recorded as observations, not decisions. The decisions are in `08_FINANCE_OPERATING_PLAN.md`.

| Suggestion | Observation |
|---|---|
| Bill.com above ten invoices a month | Standard and correct for an invoice-driven business. The ten-invoice framing assumes a software company. A van fleet's payables are dominated by **card-based** fuel, toll and maintenance spend, which Bill.com does not cover |
| QuickBooks Online | No dissent. Correct for our size. The unaddressed detail is chart-of-accounts design — whether cost can be attributed by hub and vehicle |
| Stripe for receiving payments | Worth challenging. Card rails at our invoicing volume carry meaningful processing cost, and wholesale distributors pay by ACH or cheque rather than card |
| Invoicing originates in LMX OS | Correct, and the most important thing he got right. He drew the boundary in the right place without being led to it |
| Daily or few-hourly sync | Does not fit our scale. With a handful of invoices a month, the manual booking effort is minutes and a custom integration would cost engineering weeks. Worth noting the recommendation also reduces his own manual hours |
| Cash-application identifiers | A genuine requirement we had not specified. This point alone justified the call |
| Two days for tools, 1–2 months for integration design | Credible. He revised his estimate downward when pressed, which is a point in his favour |
| Process map | The most valuable thing offered. Costs us nothing and forces the boundary to be drawn explicitly |

---

## 6. Facts disclosed during the call

| Fact | Note |
|---|---|
| LMX has a small number of live customers, a pool of gig workers, and an operating fleet, largely run manually today | Stated by Sourabh on the call |
| LMX OS holds the pricing model — flat rate or volume-based — selected by the CEO's team at client registration, and hardcoded to the account | Stated by Sourabh |
| Invoices are generated by a manual month-end job and emailed to registered addresses | Stated by Sourabh |
| Client structure is hierarchical — a corporate entity holds the contract, with store-level accounts and store-manager admins beneath it | Stated by Sourabh |
| Naveh operates under a parent finance-services firm | Stated by Param; parent name unconfirmed |
| Param is based in New Delhi | Stated by Param |

---

## 7. Evaluation of Param

### Scorecard

| Dimension | Rating | Basis |
|---|---|---|
| **Sequencing and method** | Strong | Asked for volume and forecast before recommending anything. Did not pitch first |
| **Recognising the boundary** | Strong | Asked twice, unprompted, how invoicing is triggered inside our platform, and never proposed replacing it |
| **Restraint** | Strong | Made no attempt to sell a billing system, which would have been the easy sell |
| **Accounting instinct** | Strong | Closed on cash application and unique identifiers — the right closing concern for a controller |
| **Honesty under pressure** | Good | Revised his timeline downward when the estimate was questioned, rather than defending it |
| **Fit to our specific business** | Untested | Every topic carrying real financial exposure for us — classification, driver payments, multi-state, sales tax — went undiscussed |
| **Depth on US tax positions** | Unknown | Claimed as a strength; never probed |
| **Production evidence** | Unknown | No references offered, no customers named, placeholder website |
| **Commercial terms** | Unknown | No fee discussed |

### Narrative read

Param is a credible, experienced controller. The strongest signal from the call was what he did **not** do: he had an obvious opening to sell us an invoicing or billing solution and instead asked how ours works and where his stack should pick up. Vendors who scope themselves down are rarer and more useful than vendors who say yes to everything.

His recommendations are conventional and, with two exceptions, correct. The exceptions — the ten-invoice framing for payables, and the push for a high-frequency sync — both come from pattern-matching to software startups rather than to a fleet operator. Neither is a competence problem; both are a familiarity problem, and both would likely correct themselves once he sees our actual expense profile.

The significant limitation is jurisdictional rather than personal. He is based in New Delhi. Bookkeeping, month-end close, reconciliation and payables can all be run competently from there. US tax positions, state registrations and worker-classification exposure sit in a different category and would need a licensed US practitioner regardless of how good he is. That question was not raised on this call.

The honest summary: **he was evaluated on the easy half of the problem and performed well on it.** The half that carries our real financial exposure remains untested.

---

## 8. Note on figures

Any volume or revenue figures discussed on the call were directional. The financial model remains the source of truth and should be checked before any figure derived from this conversation is used externally.
