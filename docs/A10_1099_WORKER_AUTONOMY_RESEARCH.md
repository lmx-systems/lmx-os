# 1099 contractor onboarding: worker-autonomy research for docs/ROADMAP.md A10

## What this is

Phase 2 of the W2 → 1099 → gig driver-classification rollout (`docs/NEXT_STEPS.md`
item 22; Phase 3, gig, is already built — `docs/ROADMAP.md` A11). Before any
driver can be onboarded as a 1099 independent contractor rather than a W2
employee, the product needs to actually look like one drivers have real
autonomy over, not just a label change on `Driver.employment_type`. This is a
legal question, not an engineering one — this document is research and an
honest inventory, not a decision, and nothing in this pass changes the
offer/scheduling model itself. Doing so before the legal question is answered
would be presuming the answer, the same category of premature decision
`docs/PAYROLL_STATE_OT_RESEARCH.md` already flagged for picking a specific
state's overtime multiplier before that was confirmed.

## Background on worker classification (not verified against current statute, not legal advice)

Worker classification tests generally look at how much control a company
exercises versus how much genuine independence a worker has. Several
frameworks exist and which one applies depends on jurisdiction and context:

- **Federal common-law "right to control" test** — behavioral control,
  financial control, and the relationship's nature (permanency, whether the
  work is central to the business).
- **IRS 20-factor-style guidance** — a similar multi-factor analysis used for
  federal tax withholding purposes.
- **California's ABC test** (Dynamex, codified by AB5) — the strictest common
  framework: a worker is presumed an employee unless the hiring entity proves
  all three of (A) freedom from control and direction, (B) work outside the
  hiring entity's usual course of business, and (C) the worker is customarily
  engaged in an independently established trade. Directly relevant here
  because app-based delivery/rideshare platforms are exactly who this test
  was litigated over — California voters later carved out a
  Prop 22-style exception specifically for app-based drivers in that state,
  with its own distinct conditions (a real, current-law question for counsel,
  not settled by this document).
- **Other states** have their own tests and their own gig-specific carve-outs
  or lack thereof — this varies by state the same way `docs/PAYROLL_STATE_OT_RESEARCH.md`'s
  overtime rules do, and needs the same per-state legal check before assuming
  one national answer applies everywhere LMX operates.

None of these are resolved here. What they share, informally: does the
worker have a genuine, exercised choice about whether and when to work, and
is the company's control mostly over the *result* rather than the *method*?

## Honest inventory: what the product actually does today

Verified directly against the code, not inferred:

**Factors that plausibly favor contractor status, already true today:**
- **Vehicle is driver-owned.** `Driver.vehicle_type`/`plate_number` are
  self-reported during onboarding (`app/models/driver.py`) — there is no
  company-vehicle or company-equipment concept anywhere in the codebase.
- **No exclusivity.** Nothing in the codebase restricts a driver from working
  for another platform or company simultaneously — this would be a contract
  term, not a code gate, and no such gate exists.
- **No minimum-hours or forced schedule.** `update_my_availability`
  (`app/api/driver_routes.py`) accepts any status change at any time — the
  only gate is a document-expiry compliance check, not a scheduling one.
  There is no quota, minimum-shift, or required-availability concept
  anywhere in the codebase.
- **Declining an offer has zero tracked consequence.** `decline_offer`
  requeues the order and returns the driver to available — there is no
  decline count, score, or penalty field anywhere in `app/models/`,
  `app/schemas/`, or `app/api/`. A driver who declines everything all day
  faces no recorded consequence today.

**The factor the roadmap item specifically flags:**
- **A driver never sees more than one job at a time.** The optimizer removes
  a driver from the available pool the instant it creates an offer for them
  (`app/optimizer/service.py`), specifically to prevent a second overlapping
  offer before the first is answered. There is no path anywhere in the
  codebase where a driver is shown multiple concurrent jobs and picks among
  them — it's accept-this-one-or-decline-and-wait-for-the-next, not a menu of
  choices. This is the one piece of the "single-push-offer" framing that's
  concretely true and worth a real legal read on whether it matters.

**Onboarding itself:**
- Drivers are provisioned by ops, not self-registered (`request_otp`'s own
  comment: "Drivers are provisioned by ops, not self-registered"). Whether
  that itself matters for classification (versus just being an onboarding
  workflow question, orthogonal to autonomy-while-working) is also a
  question for counsel, not resolved here.

The picture this paints: several genuinely favorable factors already exist
and require no code change to be true. The specific, concrete gap is
narrower than "the whole model is wrong" — it's centered on whether
one-offer-at-a-time, with no visibility into what else might be available,
reads as enough real choice.

## What's explicitly not decided or built in this pass

No code changes ship with this research. In particular, **not** built:
a multi-concurrent-offer/job-marketplace model, any change to how
`decline_offer` or `update_my_availability` behave, or any 1099-specific
onboarding flow. Any of these would be guessing at what the legal answer
requires — exactly the trap this document is trying to avoid. If counsel
concludes the single-push-offer model needs to change, that's real,
scoped engineering work for a future pass, informed by their specific
answer rather than a guess at it.

## Recommended next steps (not engineering)

1. Get a real, current employment-law read — starting with the federal
   test and California's ABC test/Prop 22-style carve-out given the direct
   litigation history for app-based delivery platforms, then whichever
   other states LMX actually operates in (same "which states are we
   actually in" question `docs/PAYROLL_STATE_OT_RESEARCH.md` raised for
   overtime — `Hub.state_code` is the same field that would answer it here
   too).
2. Specifically ask: does the single-push-offer model (no visibility into
   concurrent jobs, accept-or-decline-and-wait) meaningfully undercut
   contractor status given everything else already in place (driver-owned
   vehicle, no exclusivity, no minimum hours, no decline penalty)? Or do the
   existing favorable factors, taken together, already clear the bar?
3. Only once that's answered: scope whatever product change (if any) is
   actually required, and build it against the real requirement instead of
   a guess.
