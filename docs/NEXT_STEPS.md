# LMX OS — Next Steps Tracker

Living punch list. Update the Status column as items move — this file is
meant to be edited directly (by hand or by Claude Code), not regenerated.

Last synced with `docs/ARCHITECTURE.md`: see that file for the full technical
detail behind each item below.

## Engineering

| # | Item | Why it matters | Status |
|---|---|---|---|
| 1 | Stand up real Postgres + Redis, run the migration, add integration tests against a live DB | Every test today runs offline (fakeredis + pure functions) — zero integration coverage against a real database. Biggest testing gap in the codebase. | Not started |
| 2 | Confirm the real Epicor payload shape with the first design-partner client, update `EpicorAdapter` | Field names (`OrderNum`, `ShipToNum`, etc.) are a guess, not verified against a real tenant. Peer review calls Epicor config drift "the most common cause of Phase 1 slippage." | Not started |
| 3 | Check the batch-hold "4-question decision logic" and SLA hold-window minutes against the Source of Truth Index (Google Drive) | Both were reconstructed from the peer-review summary, not the canonical doc, which wasn't reachable while building. Isolated in `app/batch_queue/queue.py` and `app/sla/engine.py` so fixing is contained. | Not started |
| 4 | Provision a Google Cloud service account (`roles/cloudoptimization.user`), set `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT_ID`, run one real `optimizeTours` call | The real client is now implemented (`app/optimizer/google_routes_client.py`) but has never been exercised against the live API — request/response mapping and the per-tier skip-penalty values are unverified. | In progress — implemented, not yet verified live |
| 5 | Build the nightly pattern-detection job that populates `proposed_rules` (component 6) | Schema exists (`stop_flags`, `proposed_rules`, `active_rules`); no job writes to it yet. | Done — `app/learning_loop/`, endpoint `/learning-loop/{hub_id}/run-nightly-job`. Flag-type naming convention (`hold_window_too_short`/`_too_long`) still needs sign-off from whoever builds the driver app. |
| 6 | Decide event-bus vs. manual trigger for the Dispatch Optimizer *and* the Learning Loop's nightly job | Design doc calls for re-optimizing on "every meaningful event" (order released, driver status change, stop completed), and the nightly job needs an actual schedule. | Partially done — Dispatch Optimizer: done. `app/events/bus.py` + `app/optimizer/event_trigger.py` wire real events: order ingestion publishes `order_held`, driver status changes publish `driver_status_changed`. `stop_completed` has no producer yet (component 7 / driver app not built). Bus is in-process only — a real gap if the app ever runs as more than one instance. Learning Loop nightly job: still not started, remains manual-only (`/learning-loop/{hub_id}/run-nightly-job`) — needs a real scheduler. |

## Business / Org

These aren't code tasks, but they gate what the code above is even for —
worth tracking alongside engineering, not separately.

| # | Item | Why it matters | Status |
|---|---|---|---|
| 7 | Hire the senior backend engineer | Peer review names this the critical path — "do not hire down." | Not started |
| 8 | Sign the first client contract (before or alongside the hire above) | Without it, there's no real data to shadow-test Hub 1 against, and the 2.5 DPH figure stays a model assumption instead of something proven live. | Not started |

## How to use this file

- Update Status in place: `Not started` → `In progress` → `Done`.
- If an item spawns sub-tasks, add a nested list under that row rather than
  creating a new top-level row — keep this file scannable in one pass.
- When an engineering item is closed, cross-check whether the corresponding
  caveat in `docs/ARCHITECTURE.md` ("stubbed" / "best-effort interpretation"
  sections) should be updated or removed.
