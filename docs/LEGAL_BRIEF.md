# Legal brief — the two documents behind the signup checkbox

> **Written by engineering. Not legal advice, and not reviewed by a lawyer.**
>
> This is the covering memo for two drafts, and its job is to make counsel's turnaround
> short: every clause below exists because something in the running system depends on
> it, and this document says what. Counsel should feel free to restructure entirely.
> What matters is that the final documents answer these questions, because the software
> already behaves as if they do.

**The drafts.** `app/legal/content/terms.md` and `app/legal/content/privacy.md`. They
are the served copies — the portal renders them at `/terms` and `/privacy`, and the
signup checkbox links to them — so a redraft goes in those files and nowhere else.

**They are marked `status: draft`, and that closes the front door.** `POST
/public/signup` returns 503 while either document is a draft. This used to be a warning
in three docstrings; it is now a runtime guard, because a signup writes
`clients.terms_accepted_version` and a version of an unapproved document records assent
to nothing — which is worse than no record, since it looks like one.

---

## What changed in the code, and why it had to

Three defects in one flow, all now fixed. Worth reading because they change what the
acceptance record is worth in a dispute.

| Was | Now |
|---|---|
| `TERMS_VERSION = 'v1'` lived in `SignupPage.tsx` and was **sent to the server**, which stored whatever arrived. The only evidence of what an applicant agreed to was written by the applicant's browser. | The version is declared in the document's own front matter. `app/legal/documents.py` is the only place it is read from, and the endpoint writes the server's value. |
| Nothing compared the submitted version to the current one. A form left open across a terms change would silently record assent to text the applicant never saw. | A mismatch is a 409 and nothing is written. The applicant reloads and reads the new version. |
| Nothing checked a document existed. The checkbox named two documents in plain text with nowhere to go and read them. | Both are served and linked. A draft renders with a banner saying it is a draft and applies to nobody. |
| Nothing deleted anything, ever. | `POST /internal/retention/prune` runs three sweeps daily — location trails, message and call metadata, and declined applications — against the periods the privacy policy states. Two image categories remain, and are bracketed in the policy so it cannot be published while they are unenforced. |

`grep -n '\[[A-Z]' app/legal/content/*.md` is the complete list of holes in the drafts.
Each one is a bracketed block of capitals saying what it is waiting on, and a test
refuses to let a document be published with one still in it.

---

## Decisions we need, in the order they block things

### 1. The insurance position — blocks clause 9 of the terms

Clause 9 is drafted as a shape with four numbers missing, because they cannot be
invented: they are whatever cover is actually in place.

| Placeholder | What it needs |
|---|---|
| `CAP PER CONSIGNMENT` | Cargo cover per shipment |
| `AGGREGATE CAP` | Annual aggregate |
| `CLAIM WINDOW` | How long a client has to notify us of loss or damage |
| `DECLARED VALUE LIMIT` | The value above which we will not carry without agreeing in writing |

This is roadmap `R1` and it is the longest lead time of anything here — a broker
conversation, not a drafting session. **Start it first.** Nothing else on this list has
an external dependency measured in weeks.

### 2. Operational-data and training rights — clause 8 of the terms

The clause the roadmap flags hardest (`W7`): training rights, cross-customer
aggregation, and anonymisation terms belong in customer #1's contract *before the first
delivery*, not in a later amendment. Renegotiating this with a live customer is a much
worse conversation than having it now.

The draft claims, in plain terms:

- The operational record of *how a delivery was performed* is ours.
- We analyse and train models across all the work we carry, not only one client's.
- Anything beyond running a client's own account is aggregated and de-identified.
- We do not use a client's prices, customer lists or volumes to a competitor's benefit.

That last sentence is a commitment we have to be able to keep, and it is the one worth
arguing about. Counsel needs to confirm the first three are enforceable as written and
that the carve-out is the right shape.

### 3. Retention periods — the privacy policy states these as facts

Every number below is **proposed by engineering, not decided.** The middle column is
what actually happens today, which is the part that matters: a policy stating a period
nothing enforces is the same class of defect as a proof-of-delivery requirement nobody
checks.

| Data | Proposed | Enforced today? |
|---|---|---|
| Driver location trail | 90 days | **Yes** — `settings.location_ping_retention_days` |
| SMS and call records | 2 years | **Yes** — `settings.communication_retention_days`. Metadata only; call content is never recorded |
| Declined applications | 12 months | **Yes** — `settings.declined_application_retention_days`. Deletes the application and its inactive login |
| Recipient tracking links | Dead ~24h after delivery | **Yes** — `settings.tracking_link_grace_hours` |
| Delivery and billing records (incl. recipient name and address) | Account life + 7 years | No mechanism, and none needed — nothing deletes them |
| Proof-of-delivery photos and signatures | **Undecided** | **No.** Object storage — a bucket lifecycle rule, not an application loop. **Outstanding, and it needs your number** |
| Driver licence and insurance images | **Undecided** | **No.** Same. **Outstanding** |

The first four run daily via `POST /internal/retention/prune`, which reports what it
deleted per category rather than returning `ok` — "the sweep ran" and "the sweep deleted
the rows it should have" are different claims.

The two outstanding rows are now **bracketed placeholders in the policy itself**, so the
publish-time check refuses the document until either a lifecycle rule exists or the
sentence is softened. That was deliberate: a policy stating a period nothing enforces is
worse than a policy that says less.

Three of these numbers now have to agree with code — the settings named above and the
sentences in the policy move together, or the document lies. What we need from you is the
**two undecided image-retention periods**, because they cannot be guessed:

**The proof-of-delivery period interacts with the claim window in decision 1.** Deleting
the photograph of a delivery before the client can still claim for it would be an
own-goal, so **whatever the claim window is, proof retention must be longer.** That makes
these two questions one question, and the insurance conversation answers it first.

### 4. Sub-processors, contact details, governing law

Smaller, but each is a bracketed hole in a document nobody can publish around:

- The sub-processor list and where they process. Section 6 of the privacy policy
  describes them by role — hosting, SMS, mapping and routing, payroll, email, file
  storage — because two of the six are not provisioned yet (Twilio and Rippling), so
  naming them would be premature. It needs to name them before it goes live.
- A privacy contact address and email.
- Governing law and venue for the terms.
- State-specific privacy rights and response deadlines.

### 5. Whether we take payment — changes clause 5

Already on the cofounder list. The terms currently describe what is true: we invoice,
we collect cash at the door on the client's behalf, and we are not a party to their
transaction with their customer. If we start taking card payments that clause is wrong
and a payments-processor relationship joins the sub-processor list.

---

## Clause map — what in the system depends on each one

### Terms

| Clause | What depends on it |
|---|---|
| 2 — Requesting an account | Signup creates a `pending` client that cannot order. We need the right to decline without giving a reason, and to withdraw approval |
| 3 — What LMX does | LMX is the carrier, not a broker or a software vendor. A positioning decision (`docs/LMX_LINK_PLAN.md` §0); getting it wrong here undermines it everywhere else |
| 4 — Orders, collection and delivery | The system commits to a collect-by time and shows an *estimated* delivery time. The distinction has to survive into the contract or the estimate becomes a promise. Also covers configurable proof of delivery |
| 5 — Prices and payment | Rates are set per tier at approval; there is no self-serve pricing. Covers cash collected at the door |
| 6 — Service levels and credits | A missed collection commitment automatically credits the invoice. That is a contractual remedy the system performs without being asked, so it has to be a contractual remedy |
| 7 — Your customers' information | We hold recipient names, addresses, phones and notes on the client's behalf. Incorporates the privacy policy by reference |
| 8 — Operational data | `W7`. Decision 2 above |
| 9 — Liability | `R1`. Decision 1 above |
| 10 — Suspension and ending | `clients.signup_status` supports withdrawal of approval |
| 11 — Changes | `terms_accepted_version` and `terms_accepted_at` are recorded per client, so versioned re-acceptance is supportable. **The re-acceptance flow is not built** — today a version bump would close signup to new applicants until they accept, but would not prompt existing clients |

### Privacy policy

Structured by whose data it is, because the three groups reach us completely
differently and have different rights: businesses we deliver for, people receiving a
delivery, and drivers. A recipient never agreed to anything with us — their details
arrived because a distributor sent them — and section 8 says so, routing their requests
back through the sender where that is the honest answer.

The inventory in the policy was written from the schema, not from memory. Everything it
lists is a column that exists.

---

## To publish

1. Counsel returns both documents.
2. Paste the final text into `app/legal/content/terms.md` and `privacy.md`, keeping the
   front-matter block.
3. Fill every `[PENDING …]` placeholder. A test fails if a published document still has
   one.
4. Set `status: published` and `effective: YYYY-MM-DD` in both. A published document
   with no effective date refuses to load — the date is what a dispute turns on.
5. Bump `version` if the text changed materially since anything was recorded against it.
6. Update `tests/test_legal_documents.py::test_shipped_documents_are_still_drafts`,
   which exists to make sure step 4 is deliberate.
7. Schedule `POST /internal/retention/prune` daily (EventBridge, same pattern as the
   webhook sweep in `docs/ALERTING.md`). **Do this before publishing, not after** — the
   policy states a retention period from the moment it is in force.
8. Leave `allow_unpublished_terms` unset. Publishing is how the door opens; the flag is
   a demo affordance that logs a warning on every signup it lets through.

Steps 2–6 are one commit. Everything expensive is in the four decisions above.
