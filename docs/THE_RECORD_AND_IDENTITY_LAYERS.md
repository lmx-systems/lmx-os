# The record and identity layers, as they actually are

**What this is.** `docs/ROADMAP_AUDIT_2026-09.md` says what was broken in
September 2026 and why. This says what is true afterwards: what writes, what
reads, what runs at 2am, what a person still has to do, and which numbers came
from the design partner's real data rather than from a fixture.

Written because the alternative was that this only existed in one session's
working memory. Every claim below is checkable against a named file or a test.

---

## 1. The chain, end to end

**Decision → outcome → consequence.** Three links, each recorded by a different
writer, and until September only the first of them ran.

```
intake            ingest_lmx_order                app/ingestion/service.py
  ├─ dock          link_shop_to_dock (IDN-1)      → Shop.location_id
  ├─ arm           assign_arm (EXP-1)             → experiment_assignments
  └─ price         _price_order                   → Order.fee_cents

dispatch cycle    run_cycle                       app/optimizer/service.py
  └─ decision      record_decision (REC-1)        → decision_snapshots
                                                     .hold_decisions (AGT-4)

delivery          complete_stop                   app/api/driver_routes.py
  └─ outcome       record_delivery_outcomes (REC-3) → outcome_ledger
                     ├─ commitment resolved at delivery, not later
                     └─ snapshot_that_assigned → decision_snapshot_id

nightly, 2am      LearningLoopScheduler           app/learning_loop/scheduler.py
  ├─ rules         run_nightly_job                → proposed_rules
  ├─ dwell         refresh_hub_dwell_statistics   → receiver_profiles (IDN-4)
  ├─ silence       close_consequence_windows      → outcome_ledger (REC-2)
  ├─ flags         run_linkage_detectors          → linkage_flags (REC-4)
  ├─ classes       classify_unlabelled_locations  → locations (IDN-3)
  └─ merges        propose_duplicate_locations    → location_merges (IDN-2)
```

`tests/integration/test_the_record_chain.py` runs one order through all of it
and asserts each writer as it fires. It was checked by breaking three links in
turn and confirming it failed at the right step — a chain test that passes on
its first run is worth doubting.

### The nightly tick

Per hub, at each hub's own local 2am, claimed with a Redis day key. Six jobs,
**six independent `try` blocks**: a dwell refresh must not cost a hub its rule
proposals, and a consequence close must not cost it the linkage flags.

`propose_duplicate_locations` is the exception — it is claimed once a day across
*all* hubs, because the comparison is deliberately global. The same physical dock
can be reached from two hubs, and that pair is the most valuable merge to catch.

Two of them run outside the hub-closed branch on purpose. A hub that was shut
today still has yesterday's stops to compute a percentile over; the pattern
detector's reason for skipping does not apply.

---

## 2. What a person has to do

Four queues. None of them existed in September; each is the human half of a
mechanism that was already built and called by nothing.

| Queue | Endpoint | Who | Why a person |
|---|---|---|---|
| **Exceptions** | `/operations/exceptions` | any ops | What to do now, sorted by the clock |
| **Late orders to judge** | `/operations/late-orders` | any ops | What happened after a late delivery — only someone who took the call knows |
| **Same place?** | `/operations/merge-proposals` | read: any ops · decide: admin | Whether two records name one dock |
| **What kind of place?** | `/operations/unlabelled-docks` | any ops | 66 accounts have no industry word; no rule can read a family name |

**The judging surface had to exist before the scheduler.**
`close_consequence_windows` records *silence* for every late order nobody judged.
Wired first, it would have labelled every late delivery as "nothing happened" —
false labels, in an append-only ledger, indistinguishable from true ones by the
time anyone trained on them. `TestTheScheduleWouldHaveLied` pins that down.

**Admin only where a decision rewrites shared state.** Confirming a merge moves
every per-dock statistic. Recording a consequence, overriding a hold, and
labelling a dock are all open to any ops session: the person who took the angry
phone call or has been to the door is the one who knows, and making those
admin-only would put the knowledge and the permission in different people.

---

## 3. What the console reads back

`/operations/record-health` reports the record on itself, because **a writer that
silently stops looks exactly like a quiet week.**

- Each writer's row count **and when it last wrote** — zero rows with no
  timestamp is a writer that never ran; zero rows with a timestamp from March is
  one that stopped. Both are zero.
- On-time rate from the ledger, with a **Wilson** interval. At 3/3 a Wald
  interval sits at [100%, 100%] and claims certainty after three deliveries.
- The share of outcomes citing the decision that assigned them. Expected below
  100% — an offer-built route has no recorded cycle to cite. **A fall is the
  signal, not the level.**
- Consequence labels against the brief's 500–1,000 band, reported as a band.
- Dwell coverage: ours, inherited, thin, unknown.

**It never recomputes from `orders`.** A reader that fell back to recomputing
would keep showing a healthy on-time rate after the ledger stopped being written
— the one failure it exists to catch. A test asserts the module does not import
`Order`.

---

## 4. Numbers from the real data

Everything here came from running against the design partner's export, not from
a fixture. Aggregates only — `CLAUDE.md`'s naming rule covers logs as well as
documents.

| | |
|---|---|
| Accounts in the whole-book export | 230 distinct `receiver_id` values — **229 of them real.** The 230th is the empty string, and 68 stops carry it. `AGT-1`'s row is named for "the 230 account IDs" and one of them is not an account |
| Docks after resolution | 217 — **one** account had no usable address; the rest of the drop from 230 is address normalisation collapsing near-identical rows, which is `IDN-2`'s job to confirm |
| Receivers in the second-precision export | 142 |
| **Identifier overlap between the two exports** | **142 of 142** — same id space, which is what makes the `Shop.external_ref` join work |
| Docks given an inherited dwell | 140, 0 unmatched |
| Period covered | January–April 2026 |
| Median dwell across docks | 116s (range 2s–1,045s) |
| **Median observations per dock** | **4** |
| Node class unlabelled | **28.7%** by the current rules, target under 2% |

Two findings that only a real run could produce:

**The median dock has four observations.** A floor of 10 samples would keep 29%
of them. So a coverage count alone reads as far stronger evidence than it is, and
thin figures are flagged rather than withheld — the alternative at those docks is
no figure at all.

**`weight` is populated on 96% of rows and non-zero on 0%.** Exactly the
distinction `Coverage` exists to draw, doing its job.

**A caution on reading the database for these.** The seeded database still
reports 36.4% unlabelled, because the seed ran before the rule additions and
nothing re-classified afterwards — see §6. The 28.7% is what the current rules
produce over the same account book. Two figures, both true, different dates.

---

## 5. Rules that are not obvious from the code

**Inherited dwell never touches our own columns.** Three reasons, any one
sufficient: the nightly refresh would erase it by 2am; they are different
measurements by different drivers; and the brief's *"whether the learning
transfers is measured, not assumed"* applies in spirit. `dwell_estimate()` picks
one and says which — **it never blends**, because a weighted average of our two
deliveries and somebody else's four hundred is a number with no owner.

**A wrong node class is worse than `unknown`.** Hold-window policy is
node-class-specific to an extreme degree — +3–7% at high-frequency shops against
+195–271% at warehouse and transfer nodes. `transportation`/`trucking` was
deliberately left unclassified: the name does not say whether it is a fleet
needing parts or a terminal.

**Backfilled history is never priced, never given an arm, never dispatched.**
Pricing puts it back in `generate_invoice`'s selection and bills a customer
twice; an arm on an order delivered weeks ago contaminates `EXP-1` permanently,
because `experiment_assignments` is append-only.

**Replay returns the existing row untouched.** The unique index stops the second
row and says nothing about the first — a replay that refreshed `requested_at` or
re-priced would leave one row, correctly, while changing facts a settled
statement rested on.

**A consequence of `other` must carry a note.** An `other` with nothing written
is an override with no reason wearing the costume of one, and it is what a
hurried dispatcher reaches for if it is allowed.

---

## 6. A gap found while writing this

`classify_unlabelled_locations` ran from two seed scripts and nowhere else. So a
dock created by ordinary intake **never got a node class at all**, and improving
the naming rules never reached docks already in the table. Both failures were
silent: the coverage figure simply stayed where the last seed left it, which is
why the database still reports 36.4% while the current rules produce 28.7%.

It is now on the nightly tick. It only touches docks with no label, so a
dispatcher's correction survives it.

This is the seventh instance of the same pattern in one month — a mechanism that
works, is tested, and is called by nothing on the live path. The orphan test did
not catch it, because the function *did* have callers, just not ones that run in
production. **A caller in a seed script is not a caller.**

**The check now knows the difference.** `scripts/` is partitioned into ten
operational scripts — things an operator runs to run the business — and twelve
one-off, analysis and document-rendering tools. Only the first count as callers,
and a new script must be classified or the suite fails, the same discipline
`test_architecture_boundaries.py` applies to packages under `app/`.

Tightening it surfaced eight more functions. Seven are the baseline-analysis
pipeline and the historical export reader, all correctly offline — calling a CSV
reader from a request path is what the architecture boundary exists to prevent.
The eighth was real: **`measure_geofence_calibration`**, the thing that turns
`GEOFENCE_RADIUS_M = 75` from a guess into a measurement, was called by a
dev-world seed script and nothing else. It is now reported in
`/operations/record-health`.

---

## 7. What is still not wired, and why

`tests/test_no_new_orphans.py` fails when a public function in `app/` is
reachable from nothing, and a second test fails when an allowlisted entry stops
being an orphan — so the list below cannot rot. Fifteen entries, each a stated
hold rather than an oversight.

| Item | What | Why it is held |
|---|---|---|
| `REC-1` | `replay_inputs` | The log is written; nothing in the product replays it |
| `REC-2` | `consequence_label`, `record_driver_day_cost` | No per-order reader; the second is superseded |
| `REC-3` | `current_outcome`, `supersede_outcome` | A correction path with no operator surface |
| `IDN-2` | `propose_merge`, `merge_locations` | Auto-merge is gated behind confirming the founding set (§2.2c) |
| `IDN-4` | `set_access`, `set_autonomy_fit`, `set_receiving_hours` | Stated facts; nothing asks a receiver yet |
| `EXP-1` | `arm_for_order` | Inert until a client contracts an arm |
| `ING-3` | `backfill_orders` | No order-level history exists to import |
| `CON-3` | `labelled_overrides` | The training-set export has no reader yet |

`require_agreed` was on this list and is not any more: `scripts/settle_month.py`
now refuses to write a statement PDF on a basis that is unsigned or no longer
reproduces, and `--draft` marks the page instead of bypassing the check.

---

## 8. What is blocked on something outside the code

- **`ING-3`** needs an order-level export from the incumbent. Both existing
  exports describe *stops* with no order reference or street address — no
  engineering turns them into orders. A commercial ask.
- **`IDN-3`** needs a person to work the labelling queue, or the target moves.
  66 accounts, and no rule can read a family name.
- **`REC-5`** — one definition of a stop — is unbuilt and needs three
  signatures. Three sources still report three different counts.
- **`PRD-7`** needs `brew install libomp` on the build machine.
- **`DEC-3`** needs a live Google project.

---

## 9. What `AGT-1`'s harness found before anybody labelled anything

`ml/agt1/` is the resolution bake-off: the deterministic resolver against an
agent, scored on hand-labelled truth. **No truth file exists yet** and the agent
cannot run, so nothing below is a score. These are properties of the incumbent
that building the harness made visible, and each is checkable with
`python scripts/agt1_bakeoff.py score`.

### Merges are transitive and nobody was closing them

`propose_duplicate_locations` proposes pairs and a person confirms them one at a
time. Nothing in that loop ever asks what the *set* of confirmed pairs implies.
Closed transitively, the incumbent's 97 proposals over the real book produce
**174 docks from 229 accounts** — and its largest implied dock holds **14**.

That fourteen is a municipal chain. `1960/0-A*` is a **catch-all suffix bucket**
holding unrelated one-off accounts, so the rule *"same account root and branch,
differing suffix → HIGH"* — the distributor's own statement that two records are
one place, and correct nearly everywhere — links a DPW to a county department to
a business with nothing to do with either. Each link is defensible on its own
evidence. The closure is a dock with four organisations in it.

**Why nothing downstream would have caught it.** A reviewer sees one pair. The
pair is fine. Fourteen fine pairs later there is one dock accumulating four
organisations' dwell, and the seam is exactly what a merge is designed to erase.

### `repair` is missing from `_COMMON_TOKENS`

The second-largest implied dock holds **nine repair shops in seven towns**.
`_COMMON_TOKENS` lists `auto`, `automotive`, `parts`, `garage`, `motor`, `car`,
`truck` — and not `repair`, which is the commonest trade word in a body-shop
book. So `distinctive_tokens("T & J AUTO REPAIR")` is `{"repair"}`: the initials
are one character and dropped, `auto` is common, and the one surviving token is
the trade itself. Every *"X & Y AUTO REPAIR"* therefore has **1.00
distinctive-word overlap** with every other, which is the exact signal the
function's docstring says it exists to avoid — *"two body shops both called
'... Auto Parts Inc' overlap heavily on common words and share nothing that
identifies them. This measures the part that does."*

Twenty-one pairs reach the review queue this way today. It never auto-merges, so
the cost is a queue two-thirds noise rather than a corrupted dock — which is the
failure mode `account_signals.py` already names as the reason a queue gets
cleared rather than read.

### Neither was fixed *in* `AGT-1`, and both are fixed now

Changing the contestant inside the bake-off, before the harness has scored
anything, is the anchoring failure `gold.py` refuses in a different costume. So
the fixes are a separate change, measurable against the incumbent as it shipped.

**What changed.** `repair`, `repairs`, `body`, `collision`, `care`, `dba`,
`county`, `township`, `boro`, `borough` and `dpw` joined `_COMMON_TOKENS`,
ranked by how many accounts in the real book carry them — the discipline `IDN-3`
used to exhaust its rules, rather than a guess. And a pair sharing a stem whose
two names share **no word at all** is now `REVIEW` with a reason naming the
possibility, not `HIGH`: demoted rather than refused, because the stem is real
evidence and a business does get renamed.

| | before | after |
|---|---|---|
| Proposals over the whole book | 97 | **73** |
| of which `WEAK` | 32 | **11** |
| Implied docks | 174 | **182** |
| Second-largest implied dock | 9 repair shops in 7 towns | **gone** |
| Proposals no block would reach | 20 | **0** |
| Pairs to hand-label | 1,876 | **1,715** |

That last pair of rows is a second-order effect worth naming: the escaping
proposals *were* the repair-shop pairs. With the trade word out of the way, the
blocking and the resolver agree about which pairs are worth looking at.

### What the demotion does not fix, and what does

**The 14-account chain survives**, because a tier is advice and an edge is an
edge: `REVIEW` still proposes the pair, and the transitive closure does not read
tiers. The closure figure measures *"if every proposal were confirmed"*, and
nothing here auto-merges — so the real exposure was never the number, it was a
reviewer confirming a chain pair by pair without being able to see it.

That is fixed where it lives, in the queue. `merge_scale` reports how many shops
sit behind each side and how many docks each has already absorbed, and
`GET /operations/merge-proposals` carries it. A proposal where either side is
already a group is labelled **extends a chain** in the console, with the count
of records the click would make one place. A merge erases its own seam — that is
what it is for — so there is exactly one moment the scale can be shown, and it
is before the click.

