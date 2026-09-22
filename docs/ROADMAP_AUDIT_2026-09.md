# Roadmap audit — September 2026

**Scope.** Every row in `docs/ROADMAP_1.5.md` marked `BUILT`, checked against the
code rather than against a PR title. 43 rows, 37 file paths, 281 public
functions.

**Why now.** Six items in a row turned up work marked done that was not wired to
anything — the most recent three found by building on top of it, which is the
expensive way to find out. This audit stops guessing and measures.

---

## The finding

**Nothing named in a `BUILT` row is fiction.** Every file exists, every module
is merged, every test passes. That was worth establishing and it is not the
problem.

**The problem is wiring.** 28 public functions in `app/` are called by nothing
in the product — not by a request path, not by a background task, not by a
script. They are reachable only from tests.

They are not scattered. They are two layers.

### The record layer is written by one thing

`REC-1`'s decision log is live: `app/optimizer/service.py` calls
`record_decision` on every cycle, and since `AGT-4` it carries the reason for
every hold. That link works.

The three things meant to be measured *against* it do not exist in production:

| Item | Status claimed | What is actually true |
|---|---|---|
| `REC-2` consequences | `BUILT, ONE GAP` | ~~`record_consequence` has no caller.~~ **Fixed** — a judging surface first, then the nightly close. Wiring the scheduler alone would have labelled every late delivery `silence` |
| `REC-3` outcome ledger | `BUILT` | ~~`record_delivery_outcome` has no caller — the ledger was empty in production.~~ **Fixed in the same pass:** `record_delivery_outcomes` now runs inside `complete_stop`'s transaction on every dropoff, and `snapshot_that_assigned` fills the `REC-1` link that was null on every row |
| `REC-4` linkage flags | `BUILT` | ~~`run_linkage_detectors` has no caller.~~ **Fixed** — nightly, with `GET /operations/linkage-flags` reading them into the console |

The chain the central claim depends on is *decision → outcome → consequence*.
The first link was live and the rest were inert. **All three are wired now** — see
each row — which was the point of the audit rather than a separate project.

The one production writer of `outcome_ledger` is `record_arm_abstention`, called
from intake for control-arm orders — and no client has contracted an arm, so
that path is inert too.

### The identity layer is reached through one function

Production code imports `app/identity/` in exactly one place:

```
app/ingestion/service.py:32:  from app.identity import receiver_key_for
```

`receiver_key_for` is a pure string normaliser used by `EXP-2`'s stratification.
Resolution, merging, node-class labelling and the profile store are not called
from any path an order passes through.

| Item | Status claimed | What is actually true |
|---|---|---|
| `IDN-2` merge review queue | `BUILT` | ~~Nothing calls `propose_merge`; the queue is permanently empty.~~ **Fixed** — filled nightly, worked through `GET /operations/merge-proposals`. Needed `IDN-1` first: no links, no near-duplicates to find |
| `IDN-3` node-class labelling | `BUILT, DONE-WHEN UNMET` | `set_node_class` is not called from any live path. The 36.2% unlabelled figure the row already carries is a floor, not a snapshot |
| `IDN-4` receiver profile store | `BUILT` | ~~`refresh_dwell_statistics` has no caller.~~ **Fixed** — nightly, per hub. Fixing it first required `IDN-1`: nothing had ever set `Shop.location_id` outside a one-off script, so a refresh would have covered a frozen subset of docks and looked from every angle like it worked |

### Smaller ones

- `REC-1`'s `replay_inputs` has no production caller. The log is written; nothing
  replays it.
- `STL-2`'s `require_agreed` is called by nothing, including
  `scripts/sign_basis.py`. Nothing refuses an unsigned basis.
- `record_driver_day_cost` is named only inside a docstring — in
  `scripts/settle_month.py`'s own note about orphaned modules.

### Two of them are mine, from this week

`backfill_orders` (`ING-3`) is blocked on `ING-4`'s adapter and says so.
`labelled_overrides` (`CON-3`) collects a training set nothing reads yet. Both
are listed rather than excused.

---

## What changed as a result

**`tests/test_no_new_orphans.py`.** The check is now a test, not a habit. It
walks the AST of every module in `app/` and `scripts/`, collects the names each
one actually references, and fails when a public function is reachable from
nothing. The 28 above are an allowlist, each entry carrying its roadmap ID and
why — and a second test fails when an entry stops being an orphan, so the list
cannot become a place names go to be forgotten.

This is the same move `tests/test_architecture_boundaries.py` made for the
core/edge rule: a convention nobody can quietly break.

**`tests/test_no_unreachable_routes.py`** closes the hole that one leaves. The
orphan check skips decorated functions — a FastAPI route has no Python caller by
design — so an endpoint with tests and no console caller is invisible to it,
which is the *exact* shape of the defect. This one checks all nine routers
against all three front ends and found nine more, including two thirds of `W1`'s
returns flow. It matches the last literal segment of a path rather than whole
paths, for reasons the end of this file sets out at length: the whole-path
version produced four false positives and was abandoned.

**Roadmap statuses corrected** for `REC-2`, `REC-3`, `REC-4`, `IDN-2`, `IDN-4`
and `ING-4`. None becomes `NEW` — the code is real and tested. They become
`BUILT, NOT WIRED`, which is a different and more useful thing to read.

---

## Reading it back

Wiring four writers created a new gap: nothing read any of them. A writer that
silently stops looks exactly like a quiet week, and the only way to tell was to
query the database by hand.

`app/reporting/record_health.py` and `GET /operations/record-health` report the
record on itself — each writer's row count **and when it last wrote**, because
zero rows with no timestamp is a writer that never ran and zero rows with a
timestamp from March is one that stopped. Plus the on-time rate from `REC-3`'s
ledger with a Wilson interval, the share of outcomes citing the decision that
assigned them, and `label_counts`' progress against the brief's 500–1,000 band.

Computed from the ledger and never from `orders` — a reader that fell back to
recomputing would keep showing a healthy number after the ledger stopped being
written, which is the one failure it exists to catch. A test asserts the module
does not import `Order`.

---

## What this does not say

It does not say the modules are wrong. They are tested, and several were
reviewed carefully. It says a module with tests and no caller is not a shipped
feature, and that counting it as one is how `REC-3` came to be marked done while
its table is empty.

It also does not catch everything. The check is about functions; a status nothing
writes (`OrderStatus.queued`, found during `CON-2`) or an adapter registered
nowhere (`ING-4`) is the same failure in a shape this test cannot see. Those
remain a reading problem.

---

## Suggested order

1. ~~**`REC-3`**~~ — **done.** One call at delivery completion, plus
   `snapshot_that_assigned` so `REC-1` and `REC-3` are actually joinable.
2. ~~**`REC-2`'s consequences**~~ — **done**, and the order mattered: the
   judging surface had to exist before the nightly close, or every late
   delivery would have been labelled `silence`.
3. ~~**`IDN-4`'s `refresh_dwell_statistics`**~~ — **done**, and it turned up a
   deeper one: `Shop.location_id` was set by nothing but a one-off script, so
   shops created by ordinary intake had no dock at all. Both are wired now.
4. ~~**`REC-4`**~~ — **done**, runner and reader together.
5. ~~**`IDN-2`**~~ — **done.** Producer and reviewer together.

**Every finding in this audit is now closed.** What remains on
`tests/test_no_new_orphans.py`'s allowlist is four deliberate holds, each with
its reason: two paths into the merge table that §2.2(c) gates behind the
founding set, a per-order consequence label nothing reads back yet, and a
superseded cost function.

`ING-4` is separate, and was reopened here in error — see `ROADMAP_1.5.md`. Its
done-when asked for something the data cannot support.

---

## The dispatch layer, audited the same way — 21 September

`batch_queue`, `optimizer`, `sla`, `fleet_state`, `delivery`, `compliance`. The
six CORE packages. Function-level orphans there were already covered by
`tests/test_no_new_orphans.py`, so this looked for the shapes that check cannot
see — the ones §"What this does not say" named.

**The dispatch layer is markedly cleaner than the record layer was**, and that is
worth saying rather than manufacturing findings to justify the exercise. Three
checks came back empty or near-empty:

| Check | Result |
|---|---|
| Module-level vocabulary constants nothing references | 163 checked, **0 dead** |
| Enum members nothing references | 2 candidates, **both false positives** — `SLATier` is a column type written as strings; `StopFailureReason` is a request enum whose values the exception queue consumes |
| Settings nothing consults | 55 checked, 1 false positive (`dashboard_cors_origins` is read through a derived property), **2 genuinely dead** — `epicor_base_url` and `epicor_api_key`, for a client that makes no calls |

### Two findings with teeth

~~**`Hub.state_code` is set by nothing**~~ — **fixed.** Not `app/`, not
`scripts/`, not tests. It is read once, by `app/payroll/hours.py`, to select an
overtime rule. Harmless *today* only because `STATE_OVERTIME_RULES` is
deliberately empty; the trap springs the day somebody writes a California
daily-overtime rule, registers it, tests it in isolation and ships it — and it
never fires, because no hub carries `"CA"`.

**A correction to an earlier draft of this note**, which said the docstring "says
nothing about the key being unset". `overtime_rules.py`'s docstring does not, but
the *column's own comment* states it exactly: *"no Hub creation/edit API or UI
exists yet (hubs are seed/DB-provisioned only)"*. This was a documented gap that
nothing tracked, not an undiscovered one — a distinction worth keeping, because
the fix for the two is different.

`POST /admin/hubs`, `PATCH /admin/hubs/{id}` and a console panel now exist, so
the column can be set at creation or afterwards. A third instance of the same
shape as the driver finding: **nothing created a `Hub` either.** The state is
validated against a real list of codes rather than a length check — `"XX"`
passes a length check, and so does a transposed `"AR"` for `"AZ"`, and the
consequence of a wrong one is an overtime rule that does not apply or one that
does.

The panel reports **which rule is in force**, not just the code, because "no
state set" and "a state with no rule registered" produce identical payroll and
only one of them is somebody's oversight. Registering a rule is still not
offered anywhere: that needs a written legal opinion and a business decision
(`docs/PAYROLL_STATE_OT_RESEARCH.md`), and a dropdown implying otherwise would
invite somebody to guess at overtime law.

~~**Nothing creates a `Driver`.**~~ **Fixed.** No endpoint and no script created
one: a driver row could only be made by a hand-written insert, while
`driver_routes.py`'s OTP path says in its own comment that *"drivers are
provisioned by ops, not self-registered"* — describing an intention rather than
a route. `POST /admin/drivers` and a console form now do it, with
`vehicle_capacity_units` **required rather than defaulted**: the column defaults
to 1 and the optimizer's capacity check reads it, so a driver provisioned without
thinking about it gets one order at a time — the safe direction and the wrong
answer.

Building it turned up a second thing. **`drivers.phone` had no unique
constraint**, and the OTP path consumes its lookup with `scalar_one_or_none`,
which *raises* on two rows — so a duplicate number would lock **both** drivers
out of the app with a 500 rather than one of them with an error. Migration `0063`
makes it unique at the database, because the endpoint is not the only writer and
on today's evidence is not even the usual one.

Minor: `StopFlag.created_by_driver_id` is neither written nor read, so a flag
does not record who raised it.

### The check that found them

`tests/test_no_write_only_columns.py`. **A column read and never written is worse
than a dead one** — dead is merely unused, while read-and-never-written is a
permanent silent default that looks like a value somebody chose. Thirteen
allowlisted, each with its reason, and two companion tests so the list cannot
outlive the gaps.

Two earlier versions were regex and got it wrong in both directions, reporting
`pin_verification_attempts += 1` and `basis.lmx_signed_by, ... = ...` as never
written. It walks the syntax tree for that reason, and was checked by removing
`state_code` from the allowlist and confirming it surfaces.


---

## The client-facing layer, audited the same way — 21 September

`billing`, `client_auth`, `tracking`, `returns`, `webhooks`, `legal`, `settle`,
the client API and the portal. Different risk profile from dispatch: a silent
default here is visible to somebody outside the company.

Four checks, three clean:

| Check | Result |
|---|---|
| Client endpoints the portal never calls | 23 checked, **0 uncalled** |
| Webhook event vocabulary vs what is emitted | One type, hardcoded — **nothing to drift** |
| `PUBLIC_LABELS` (what an integration sees) | All 13 statuses covered |
| Customer-facing tracking labels | **One missing, and it mattered** |

### A recipient was told a finished delivery was still coming

`OrderStatus.returned` is terminal and is produced by
`app/delivery/resolution.py`. It had **no entry** in `tracking/service.py`'s
`_RECIPIENT_STATUS`, so it fell through to the fallback: *"In progress. Your
delivery is being handled."*

Permanently. A recipient whose parcel had gone back to the hub refreshed a page
that told them to keep waiting for something that was never coming. The other
two terminal states, `delivered` and `cancelled`, were both covered.

The sharpest part: `app/orders/state_machine.py`'s machine-facing map carried
`RETURNED_TO_HUB` all along and **annotates it "already terminal, already
notifies"**. The integration told the truth; the person did not.

Fixed, with a test that every status has its own words and no terminal state
reads as in progress. The fallback stays — a status added tomorrow without a
line should render *something*, and "in progress" is the right thing to say when
we genuinely do not know and the wrong thing when we do.

### Two statuses nothing can produce

`OrderStatus.classified` and `OrderStatus.accepted` are never assigned: intake
goes `received → held` directly, and an order is already `assigned` before a
driver accepts the offer. Both are harmless, because each shares its
customer-facing wording with a reachable neighbour — whoever wrote that map
anticipated overlapping states. Left alone; noted so the next person does not
read them as live.


---

## Re-audit after twenty-two changes — 21 September

Every `BUILT` row again, now that three invariants (`test_no_new_orphans.py`,
`test_no_write_only_columns.py`, `test_architecture_boundaries.py`) cover most
of what the first pass checked by hand. **70 assertions, all green on `main`.**
So this pass looked for what those tests structurally cannot see — and at my own
twenty-two changes rather than at the code I inherited.

### The orphan test skips decorated functions

Which means **an endpoint with no caller in the console is invisible to it** —
the exact `CON-4` shape.

> **Now closed by `tests/test_no_unreachable_routes.py`.** The read below was
> done by hand against `routes.py` and `admin_routes.py`; it is automated now,
> so the next one does not need somebody to go looking. **It found nine more,
> including a whole feature**: see *"What automating this found"* at the end of
> this file. Checking the dashboard against `routes.py` and
`admin_routes.py`:

| | |
|---|---|
| Ops routes | 33, of which **1 real orphan** (plus `/metrics`, which is Prometheus) |
| Admin routes | 27, of which 3 are script-driven and **5 are curl-only** |

**The one real orphan was mine.** `POST /operations/merges/{id}/revert`, added
last week. *"Every merge audited and reversible"* is a clause of `IDN-2`'s
done-when, and I built the revert while the console offered only *Same place*
and *Different* — nothing listed an applied merge for anybody to reverse.
Reversible-by-curl is a thin reading of it. Fixed: the panel now shows recently
merged pairs with an Undo.

The five curl-only admin routes — device revocation, hub closures, COD disputes,
gig density, gig jobs — are weaker than a missing writer, because an admin *can*
reach them. Recorded rather than fixed; building five surfaces unprompted would
be inventing work.

**Now built, and one of the five turned out not to be curl-only but unusable.**
`DELETE /admin/drivers/{id}/devices/{device_id}` describes itself as the *"driver
calls dispatch, ops revokes on their behalf — lost phone, no app access"* path,
and it takes a device id. The only list of device ids was
`GET /driver/me/devices`, which is **driver**-authenticated. So ops had to
already know an id they could only have got from the driver — who has lost the
phone. That is the one case the route exists for, and it needed a new endpoint
(`GET /admin/drivers/{id}/devices`), not a surface.

The other four became four panels rather than five, and none of them a new tab:

| Route | Where | Why there |
|---|---|---|
| Hub closures | folded into `HubSettingsPanel` | The same kind of standing hub fact as the state code — set once, rarely revisited, invisible until it matters |
| Device revocation | `DriverDevicesPanel`, beside driver onboarding | The other half of the same job |
| COD disputes | `CodDisputesPanel`, on the *To record* tab | Recording work: a repeat disputer is this month's conversation, not this minute's |
| Gig jobs + gig density | one `GigPathPanel` | Density summarises exactly those jobs; split apart a reader must join them by eye to know whether 12% sequenced is 3 of 25 or 300 of 2500 |

**The COD count is deliberately not in the tab badge**, and the reason is the
badge rule stated above read backwards. With no SMS provider configured *every*
dispute is un-escalated by definition, so the count would never fall — and a
badge that never falls is the *"tab nobody opens"* failure inverted. The panel
says so in words instead, once, which is what `CodDisputeReportView`'s own
comment argues: one deployment-wide fact rather than N per-account failures.

### A bug in the fix, caught by its own test

`include_applied: bool = Query(default=False)` hands a **`Query` object** to a
direct caller, and a `Query` object is truthy — so the default silently
inverted and applied merges appeared in the queue.

`app/api/client_routes.py` documents this exact trap and prescribes `Annotated`:
*"Every test in this repo calls endpoint functions directly, and with the older
form those callers receive `Query` objects instead of values."* I walked into it
anyway. Four more instances, all added by me in the last week, are converted
too — latent rather than broken, because every test passed the value
explicitly.

### What the invariants caught on their own

Across these changes the allowlists moved four times without my noticing first:
`record_delivery_outcome`, `classify_unlabelled_locations`,
`vehicle_capacity_units`, and `state_code`/`active`. Each time the companion
"the list must not outlive the gap" test failed and told me to remove an entry.
That is the mechanism working as intended — and it is the part of this audit
that will still be working in six months.

---

## What automating the endpoint read found

The hand read above checked two routers against the dashboard and found five
curl-only admin routes. `tests/test_no_unreachable_routes.py` checks all nine
routers against all three front ends, and found **nine more** that no front end
can reach — one of them a whole feature.

### `W1`'s returns flow shipped one third of its front end

`W1` reads *"Done (PRs #13–#16)"* and four slices are listed. The **client** half
is genuinely there — `client-portal/src/components/ReturnsPanel.tsx` lists what
is awaiting pickup and flags cores as ready. The **driver** half and the **ops**
half are not.

| | |
|---|---|
| `POST /driver/stops/{id}/collect-return` | the driver collects cores at the shop |
| `POST /driver/stops/{id}/return-not-ready` | they are not ready |
| `POST /driver/stops/{id}/return-to-shop` | they go back |
| `POST /admin/returns/{id}/mark-returned` | slice 3's *"ops manual mark"* |
| `POST /admin/returns/{id}/reschedule` | slice 4's `not_ready → ready` move |

So a counter person can say the cores are ready — and **the driver who arrives to
collect them has no button, and the operator who has to close the loop has no
list.** The feature is two thirds unreachable from the ends that do the work,
and the end that requests it works fine, which is exactly the configuration that
makes the gap invisible: the customer-facing half demos.

`GET /admin/hubs/{id}/returns` is reachable as of `CON-1`'s panel work, which is
the only reason it is not a sixth row.

### The rest

**`POST /driver/stops/{id}/scan-parcel`** — the app calls
`/driver/stops/{id}/scan`, which takes a *count*. The per-parcel route has no
screen, which means parcel-level scanning exists in the backend and has never
been used.

**`GET`/`PUT /admin/clients/{id}/sla-terms`** — what a client was promised, set
by `scripts/set_client_sla_terms.py`. An operator cannot read back what they
agreed to, which is the half that matters when a customer disputes a credit.

**`POST /driver/me/gig-jobs/evaluate`** — the app has no gig screen at all;
offers are answered on the platform's own app today.

### Why the check matches segments rather than paths

The obvious implementation — normalise both sides to `/admin/hubs/*/closures`
and compare — was written first and abandoned after **four** false positives,
each of which looked like a real finding:

- a character class that excluded `?` and `=`, so every path with a query string
  failed to match at all;
- `` `/admin/signups?status=${encodeURIComponent(status)}` ``, whose `${...}`
  contains parentheses;
- `` `${apiBaseUrl()}/driver/me/route-events` ``, which does not start with `/`;
- `` `...${status ? `?status=${x}` : ``}` ``, whose braces nest — a non-greedy
  `[^}]*` stops at the first `}` and glues `${status` onto the path. That call
  site had been written the same afternoon.

Each reported a route as unreachable that a front end plainly calls, and the run
ended on a count that could not be accounted for. **A check that cries wolf gets
allowlisted into uselessness**, so the mechanism has to be wrong in the safe
direction.

Matching the last literal segment is that mechanism. It never reconstructs a
path, so no interpolation shape can confuse it; it can miss a genuinely
unreachable route whose segment appears somewhere for another reason, which is
the harmless failure. **It is a floor, not a proof** — a route it passes is not
certainly reachable, and a route it fails is worth a person's attention.

Both halves were verified to bite before landing: removing an allowlist entry
fails the check, and an entry that has become reachable fails its companion.

