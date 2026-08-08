# LMX Client Terms — v1 DRAFT

> **THIS IS A DRAFT FOR COUNSEL, NOT PUBLISHED TERMS.**
>
> It is written by engineering to answer one question precisely: *what is the
> signup form actually asking a client to agree to?* Every clause below exists
> because something in the system depends on it. It has had no legal review, and
> it must not be linked from the signup page or shown to a client until counsel
> has rewritten it.
>
> **Status:** blocking. `client-portal/src/components/SignupPage.tsx` records
> `terms_version: 'v1'` on every signup and `clients.terms_accepted_version`
> stores it. Right now that records assent to a document that does not exist.
>
> **Owner:** Sourabh to route to counsel · **Related:** `R3` (privacy &
> retention policy), `W7` (training-data rights), `R1` (insurance & liability)
> in `docs/ROADMAP.md`.

---

## Why each clause is here

Counsel should feel free to restructure entirely. What matters is that the final
document answers these, because the software already behaves as if it does.

| Clause | What in the system depends on it |
|---|---|
| 2 — Approval | Signup creates a `pending` client who cannot order. We need the right to decline without giving a reason |
| 3 — What LMX is | LMX is the carrier, not a broker or a software vendor. This is a positioning decision (`docs/LMX_LINK_PLAN.md` §0), and getting it wrong in the contract undermines it |
| 4 — Orders and commitments | The system quotes a collect-by time from SLA tiers and an *estimated* delivery time. The distinction has to survive into the contract or the estimate becomes a promise |
| 5 — Pricing | Rates are set per-tier at approval. There is no self-serve pricing |
| 6 — Client data | LMX stores customer names, delivery addresses and phone numbers on the client's behalf. `R3` |
| 7 — Operational data and training | **The one the roadmap is most explicit about.** `W7`: model-training rights, cross-customer aggregation and anonymization "belong in customer #1's contract before the first delivery, not in a future amendment" |
| 8 — Liability | Gated on `R1`. Counsel cannot finalise this before the insurance position is known |
| 9 — Suspension and termination | Approval can be withdrawn; `clients.signup_status` supports it |

---

## 1. These terms

These terms govern your use of LMX's delivery services and the LMX client
portal. By requesting an account you agree to them on behalf of your business.

## 2. Requesting an account

Requesting an account is an application, not an agreement to carry your
deliveries. LMX reviews each application and may approve or decline it at our
discretion, without giving reasons. You cannot place orders until your account is
approved, and approval may be withdrawn.

You are responsible for the accuracy of the information you give us, and for the
security of the logins you create. You must tell us promptly if a login should no
longer have access.

## 3. What LMX does

LMX is a delivery operator. We carry your goods using our own drivers and
vehicles, or capacity we arrange. We are not a software vendor, a broker, or a
marketplace, and nothing here licenses software to you — the portal is how you
send us work, not a product you are buying.

## 4. Orders, collection and delivery

When you place an order you tell us where to collect from, where it goes, and how
urgent it is. We confirm a **collection commitment** — a time by which we
undertake to collect.

Any delivery time we show is an **estimate**, based on distance and expected
travel conditions. It is not a commitment, and counsel should keep that
distinction explicit. *(Engineering note: this reflects reality. The estimate is
straight-line distance at an assumed average speed; there is no verified
travel-time model until the routing integration is live — `E1`.)*

We may decline or return an order that is unsafe, unlawful, improperly packaged,
or outside the area we serve.

## 5. Prices and payment

Prices are agreed with you when your account is approved and are charged per
delivery according to the urgency tier of each order. We may change prices on
notice. Payment terms are as stated on your invoices.

*(Engineering note: payment collection is not built. Invoices are produced; how
they are settled is currently outside the system. Counsel should describe what is
actually true today rather than an intended future state.)*

## 6. Your customers' information

To deliver for you we necessarily hold information about your customers — names,
delivery addresses, contact numbers and delivery notes. We use it to carry out
your deliveries and to operate and improve our service, and we do not sell it.

How long we keep it, and how someone asks for it to be deleted, is set out in our
privacy policy. **That policy does not exist yet (`R3`) and must before this
clause means anything.**

## 7. Operational data

We record how deliveries actually happen: timings, routes, distances, driver
annotations, exceptions and outcomes. This operational record is ours, and we use
it to run and improve our dispatch system — including analysis across all of the
work we carry, and training models on it.

We do this on data that describes *how the delivery went*, not on your commercial
information. Any use beyond your own account is aggregated and anonymized, so it
cannot be traced back to you or to your customers.

> **Counsel: this is the clause the roadmap flags hardest (`W7`).** The
> requirement recorded there is that training rights, cross-customer aggregation
> and anonymization terms be agreed *broad and upfront, before the first
> delivery*, rather than added by amendment later. The wording above is
> engineering's honest description of what the system does — it needs a lawyer's
> version.

## 8. Liability

*(To be drafted once the insurance position is settled — `R1`. This clause
cannot responsibly be written before we know what commercial auto, cargo and
general liability cover is actually in place. It should cover loss or damage in
transit, failed or late delivery, and an overall cap.)*

## 9. Suspension and ending the relationship

Either of us may end this arrangement on notice. We may suspend or withdraw
access immediately where there is non-payment, misuse, or a legal or safety
reason. Ending it does not affect orders already in progress or amounts already
owed.

## 10. Changes to these terms

We may update these terms. We record which version you accepted and when. Where a
change is material we will tell you before it takes effect.

*(Engineering note: `clients.terms_accepted_version` and `terms_accepted_at`
already record this per client, so a versioned re-acceptance flow is
supportable — it just isn't built.)*

---

## What has to happen before the signup page goes live

1. Counsel rewrites this into a real document.
2. The privacy policy (`R3`) exists and is linked.
3. Clause 8 is written against the actual insurance position (`R1`).
4. Clause 7 is reviewed specifically against `W7`.
5. The agreed version string replaces `TERMS_VERSION` in
   `client-portal/src/components/SignupPage.tsx`, and the checkbox links to the
   published document rather than naming it in plain text.
