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
| `IDN-2` merge review queue | `BUILT` | Nothing calls `propose_merge`. The queue a human reviews is permanently empty, so "human-confirms the founding ~230" cannot happen |
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
5. **`IDN-2`** — the review queue needs a producer before the ~230 can be
   confirmed. **The last one still open.**

`ING-4` is separate and already reopened: it is blocked on `REC-5`'s definition
of a stop.
