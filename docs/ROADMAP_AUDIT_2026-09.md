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
