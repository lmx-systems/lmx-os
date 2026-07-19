# LMX OS — Next Steps Tracker

Living punch list. Update the Status column as items move — this file is
meant to be edited directly (by hand or by Claude Code), not regenerated.

Last synced with `docs/ARCHITECTURE.md`: see that file for the full technical
detail behind each item below.

## Engineering

| # | Item | Why it matters | Status |
|---|---|---|---|
| 1 | Stand up real Postgres + Redis, run the migration, add integration tests against a live DB | Every test today runs offline (fakeredis + pure functions) — zero integration coverage against a real database. Biggest testing gap in the codebase. | Done — `tests/integration/` (migration, ingestion, fleet state, full pipeline), wired into CI with real service containers + a skip-detection safeguard. Caught and fixed a real timezone bug in `Order`/`Stop` models (see `docs/ARCHITECTURE.md`). |
| 2 | Confirm the real Epicor payload shape with the first design-partner client, update `EpicorAdapter` | Field names (`OrderNum`, `ShipToNum`, etc.) are a guess, not verified against a real tenant. Peer review calls Epicor config drift "the most common cause of Phase 1 slippage." | Not started — `demo/` now has a placeholder sample payload + runnable seed/send scripts for demoing the pipeline (e.g. to investors) while a real design-partner integration is still pending. Not a substitute for verifying the real shape. |
| 3 | Check the batch-hold "4-question decision logic" and SLA hold-window minutes against the Source of Truth Index (Google Drive) | Both were reconstructed from the peer-review summary, not the canonical doc, which wasn't reachable while building. Isolated in `app/batch_queue/queue.py` and `app/sla/engine.py` so fixing is contained. | Not started |
| 4 | Provision a Google Cloud service account (`roles/cloudoptimization.user`), set `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT_ID`, run one real `optimizeTours` call | The real client is now implemented (`app/optimizer/google_routes_client.py`) but has never been exercised against the live API — request/response mapping and the per-tier skip-penalty values are unverified. | In progress — implemented, not yet verified live |
| 5 | Build the nightly pattern-detection job that populates `proposed_rules` (component 6) | Schema exists (`stop_flags`, `proposed_rules`, `active_rules`); no job writes to it yet. | Done — `app/learning_loop/`, endpoint `/learning-loop/{hub_id}/run-nightly-job`. Flag-type naming convention (`hold_window_too_short`/`_too_long`) still needs sign-off from whoever builds the driver app. |
| 6 | Decide event-bus vs. manual trigger for the Dispatch Optimizer *and* the Learning Loop's nightly job | Design doc calls for re-optimizing on "every meaningful event" (order released, driver status change, stop completed), and the nightly job needs an actual schedule. | Partially done — Dispatch Optimizer: done. `app/events/bus.py` + `app/optimizer/event_trigger.py` wire real events: order ingestion publishes `order_held`, driver status changes publish `driver_status_changed`. `stop_completed` has no producer yet (component 7 / driver app not built). Bus is in-process only — a real gap if the app ever runs as more than one instance. Learning Loop nightly job: still not started, remains manual-only (`/learning-loop/{hub_id}/run-nightly-job`) — needs a real scheduler. |
| 9 | **Add real authentication/authorization to the API** | Every endpoint is wide open. Was a standing gap; now urgent because `dashboard/` gives it a clickable UI — anyone who can reach the API can see all fleet/order data and trigger optimizer or learning-loop cycles for any hub. CORS restricts which browser origins can call in, which is not access control. | Partially done — `app/security.py`'s `SharedSecretAuthMiddleware` gates every endpoint except `/health` and API docs behind an `X-API-Key` header (`API_SHARED_SECRET` env var; unset = open, same as before). Fine for an internal tool behind its own network controls; a client-facing dashboard or driver app still needs real per-user auth, not a shared secret. |
| 10 | Orchestrator dashboard (component 7, partial) | Internal Vite/React/TS/Tailwind SPA (`dashboard/`) — fleet overview, hold queue, order summary, manual optimizer/learning-loop triggers. Client dashboard, driver mobile app, and shop SMS (the rest of component 7) are separate, larger builds, not started. | Done — redesigned as a KPI-first console (approved mockup, then implemented). See `docs/ARCHITECTURE.md`'s "Orchestrator dashboard" section for gaps: no hub-list endpoint, build-time API URL, no shop name/cluster-mate on held orders, no driver display name, no server-side last-cycle telemetry. |
| 11 | Enrich `HeldOrderView` with shop name + cluster-mate ids; add `name` to `DriverState`; add a "last cycle" snapshot endpoint | Follow-ups the dashboard redesign surfaced — the mockup assumed this data, the current API doesn't return it (see item 10's gaps in `docs/ARCHITECTURE.md`). Not blocking, but the dashboard is visibly working around each one. | Done — all three closed and verified (78 tests passing, live demo re-run confirmed driver name + last-cycle telemetry). Dashboard updated to use all three. |
| 12 | Driver app Phase 1: real driver auth, job-offer/accept model, Route/Stop API, React Native core loop | The wireframe spec (`LMX Driver App Wireframes.dc.html`) needed a backend that barely existed — no per-driver auth, no accept/decline concept, zero Route/Stop endpoints despite the tables existing, and the customer/dropoff side of an order was never modeled at all. See `docs/ARCHITECTURE.md`'s "Driver app" section for the full gap analysis and what's simplified in v1. | Done — `app/driver_auth/`, `app/api/driver_routes.py`, `app/models/route_offer.py`, `driver-app/` (Expo/React Native, screens 1a-1m). 87 backend tests passing (up from 78); `tsc --noEmit` clean on the app. Screens 1n-1r (earnings, messaging, full profile) are Phase 2/3, not started — no backend exists for a payout ledger or real Twilio SMS/masked calling yet, and both are sized as their own projects, not extensions of this pass. |

## Business / Org

These aren't code tasks, but they gate what the code above is even for —
worth tracking alongside engineering, not separately.

| # | Item | Why it matters | Status |
|---|---|---|---|
| 7 | Hire the senior backend engineer | Peer review names this the critical path — "do not hire down." | Not started |
| 8 | Sign the first client contract (before or alongside the hire above) | Without it, there's no real data to shadow-test Hub 1 against, and the 2.5 DPH figure stays a model assumption instead of something proven live. **Not a blocker for the rest of the engineering list** — only item 2 (and possibly item 3, if the Source of Truth Index specifically needs a design partner rather than just Google Drive access) actually depends on a signed client. Everything else above can and should proceed in parallel using placeholder/demo data (see `demo/`). | Not started |

## How to use this file

- Update Status in place: `Not started` → `In progress` → `Done`.
- If an item spawns sub-tasks, add a nested list under that row rather than
  creating a new top-level row — keep this file scannable in one pass.
- When an engineering item is closed, cross-check whether the corresponding
  caveat in `docs/ARCHITECTURE.md` ("stubbed" / "best-effort interpretation"
  sections) should be updated or removed.
