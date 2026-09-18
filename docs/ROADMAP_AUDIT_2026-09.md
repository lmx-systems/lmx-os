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
| `REC-2` consequences | `BUILT, ONE GAP` | `record_consequence` has no caller. No consequence is ever recorded, and `close_consequence_windows` has no scheduler |
| `REC-3` outcome ledger | `BUILT` | `record_delivery_outcome` has no caller. `app/api/driver_routes.py` completes a delivery, advances the order, pays the driver, adjusts the vehicle load — and records no outcome. **The ledger is empty in production** |
| `REC-4` linkage flags | `BUILT` | All three detectors work and are reachable only through `run_linkage_detectors`, which nothing calls. **No linkage flag has ever been raised** |

The chain the central claim depends on is *decision → outcome → consequence*.
The first link is live. The second and third are inert.

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
| `IDN-4` receiver profile store | `BUILT` | `refresh_dwell_statistics` has no caller — **dwell statistics are never refreshed, and `M1` is specified to read them** |

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

1. **`REC-3`** — one call at delivery completion. It is the smallest change here
   and it starts the ledger the measurement needs.
2. **`REC-2`'s consequences** — needs a scheduler, which is the same shape as
   `close_consequence_windows` already expects.
3. **`IDN-4`'s `refresh_dwell_statistics`** — `M1` cannot train on statistics
   nothing refreshes.
4. **`REC-4`** — a runner for the three detectors.
5. **`IDN-2`** — the review queue needs a producer before the ~230 can be
   confirmed.

`ING-4` is separate and already reopened: it is blocked on `REC-5`'s definition
of a stop.
