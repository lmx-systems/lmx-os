# LMX OS

The operating system for an LMX hub: it takes an order from wherever it arrived,
classifies its urgency, holds it just long enough to pair it with nearby work,
assigns it to a driver, tracks the delivery, tells the client what happened, and
bills for it.

LMX is an operator priced per drop, not a software vendor — LMX OS is built for
LMX and is never licensed or sold. See `CLAUDE.md` for the rules that govern this
repository.

**`docs/ROADMAP.md` is the source of truth for what is built.** This file describes
how to run it; the roadmap says what "it" currently is.

## The one principle

**One canonical order object. Many source adapters. Many status sinks. The core
never knows where an order came from.**

If an adapter needs a change inside the SLA engine, hold queue, or optimizer, the
contract is wrong — fix the contract, not the core. This is enforced rather than
documented: `tests/test_architecture_boundaries.py` parses the tree and fails the
build if the dispatch engine imports an adapter, a client-facing package, or a
demand path. A new package under `app/` must be classified there too, so the
boundary cannot be dodged by adding a directory.

`sla_owner` on the order object is what lets three demand paths coexist without a
fork: `LMX` means we made the promise and the SLA engine owns the clock;
`EXTERNAL` means someone else did and their window is a hard constraint.

## Quickstart

```bash
cp .env.example .env           # fill in real values as they are provisioned
docker compose up --build      # postgres + redis + app (:8000) + dashboard (:5173) + client-portal (:5174)
docker compose exec app alembic upgrade head
```

| | |
|---|---|
| Health check | `curl http://localhost:8000/health` |
| Interactive API docs | http://localhost:8000/docs |
| Ops dashboard | http://localhost:5173 — pick a hub from the dropdown |
| Client portal | http://localhost:5174 — needs a client onboarded first, via the dashboard's "Onboard a new client" form or `POST /admin/clients` |

Ops endpoints need a real account: `scripts/create_ops_user.py --role admin|viewer`.

## Local development (without Docker)

```bash
pip install -r requirements-dev.txt
# needs local Postgres + Redis, or point .env at hosted instances
alembic upgrade head
uvicorn app.main:app --reload
```

Each front end runs the same way, in its own terminal:

```bash
cd dashboard        # or client-portal
cp .env.example .env
npm install
npm run dev         # :5173 for the dashboard, :5174 for the client portal
```

Driver app: see `driver-app/README.md` (Expo / React Native, run via the Expo dev
client).

## Tests

```bash
pytest tests/ -q                       # everything
pytest tests/integration/ -q           # the database-backed half
ruff check app tests                   # the lint gate CI runs
```

**The integration suite skips itself if Postgres or Redis is unreachable, and
pytest still exits 0.** That is deliberate — `pytest` stays fast for anyone who has
not set up local services — but it means a green run is not automatically a full
one. Two things guard against being fooled by it:

- A skipped run prints a red end-of-run banner naming what went uncovered. The test
  DSN derives from the same credentials `docker-compose.yml` uses, so
  `docker compose up -d postgres redis && pytest` connects without configuration.
- `LMX_REQUIRE_INTEGRATION=1 pytest` turns the skip into a failure. CI additionally
  greps its own output for the skip message and fails the build.

The integration fixtures drop and recreate the schema of whatever database they are
pointed at, then run `alembic upgrade head` for real — so the migration chain is
exercised end to end. They target `lmx_os_test`, never the development database.

## Repository layout

```
app/
  orders/         The canonical LMX order object, its status state machine, and the
                  sink fan-out. The contract every adapter maps into
  ingestion/      Source adapters (POS/DMS, CSV manifest, client API) + the one
                  ingestion path they all delegate to
  sla/            Dynamic SLA Engine - HOT_SHOT/T1/T2/T3 classification and hold windows
  batch_queue/    Batch-Hold Queue - clustering, and the decision of when to release
  fleet_state/    Fleet State Manager - Redis-backed driver state and location
  optimizer/      Dispatch Optimizer - Google Route Optimization client, plus a stub
  delivery/       What happens at the door: proof of delivery, COD, ETAs, en-route state
  compliance/     Whether a driver may go on shift at all (documents, expiry)
  returns/        Cores and returns as first-class reverse-leg work
  learning_loop/  Pattern detection over captured annotations -> proposed rules
  shadow/         Records what LMX OS *would* have decided, without acting (W9)
  gig_platform/   The gig demand path - job store, accept gate, economics, density
  billing/        Rate tables, invoices, SLA-breach credits
  payroll/        Hours, overtime, gig payouts
  geocoding/      Address resolution and cache (Nominatim or Google)
  messaging/      SMS, email, push, masked voice - each stubs when unconfigured
  webhooks/       Outbound status callbacks to client systems
  tracking/       The recipient-facing live tracking page
  legal/          The served terms and privacy policy, plus retention enforcement
  reporting/      Scorecards and operational reports
  client_api/     Per-client API keys for external order submission
  client_auth/    Client portal accounts, sessions, password reset
  ops_auth/       Internal ops accounts and roles (admin / viewer)
  driver_auth/    Driver OTP and device-bound sessions
  storage/        Presigned uploads for proof-of-delivery media
  health/         Liveness checks that alerting probes read
  events/         Distributed per-hub event bus (Redis) - triggers dispatch off real
                  events rather than polling
  schemas/        Pydantic contracts shared across the layers
  models/         Postgres schema (SQLAlchemy)
  api/            HTTP surface - admin, client, driver, public, webhook routes
  main.py         FastAPI app + startup/shutdown lifecycle
migrations/       Alembic migrations (hand-written, not autogenerated) under versions/
tests/            pytest suite; tests/integration/ needs real Postgres + Redis
scripts/          Operational one-offs - user creation, rate seeding, E1 verification
dashboard/        Internal orchestrator dashboard (Vite/React/TS/Tailwind)
client-portal/    Client-facing web app (Vite/React/TS/Tailwind)
driver-app/       Driver mobile app (Expo/React Native)
infra/            Production hosting (AWS, Terraform) - see infra/README.md
docs/             See below
```

## Documentation

| File | Role |
|---|---|
| `docs/ROADMAP.md` | The map — every open item, the decision logs, the phased plan. **Source of truth for status** |
| `docs/NEXT_STEPS.md` | Row-by-row punch list |
| `docs/ARCHITECTURE.md` | Technical handoff detail, stubs, best-effort interpretations |
| `docs/LMX_LINK_PLAN.md` | The order-intake track: contract, design principles, sequence |
| `docs/ORDER_API.md`, `docs/WEBHOOKS.md` | External-facing interfaces |
| `docs/LEGAL_BRIEF.md` | Counsel memo — what each clause depends on, and the publish checklist |
| `docs/ALERTING.md` | What pages someone, and why it is a health endpoint rather than metrics |
| `docs/E1_ROUTE_OPTIMIZATION_ACCESS.md` | The Google Cloud access request, ready to hand over |
| `docs/DOCUMENT_STYLE.md` | House style for generated documents |
| `docs/A10_1099_WORKER_AUTONOMY_RESEARCH.md`, `docs/PAYROLL_STATE_OT_RESEARCH.md` | Background for two questions that need counsel rather than code. Neither is legal advice |
| `CLAUDE.md` | Conventions and hard rules for anyone working here |

## What is deliberately not real yet

Several integrations are complete, tested code running against a stub because no
account exists yet — Twilio, Rippling, Stripe Connect, Expo push, S3, AWS. Each
degrades to a no-op rather than failing, and each is listed in `docs/ROADMAP.md`
with what it would take to make it real.

Two things worth knowing before pointing this at anything shared:

- **The 2.5 deliveries-per-hour figure is a model assumption, not a measurement.**
  It is provable only with real driver and order data at a running hub.
- **The Google Route Optimization client has never made a live call.** The dispatch
  path falls back to a stub solver. `docs/E1_ROUTE_OPTIMIZATION_ACCESS.md` is the
  request that unblocks it.

Rate cards, SLA delivery targets and credit percentages are in place as openly
labelled placeholders rather than agreed numbers. Anything named `PLACEHOLDER_` in
the code is exactly that, on purpose.

## Configuration

All tunables live in `app/config.py`, sourced from environment variables (see
`.env.example`). Nothing is hardcoded in business logic — `BATCH_HOLD_CLUSTER_RADIUS_MILES`,
`OPTIMIZER_CYCLE_BUDGET_SECONDS`, `DASHBOARD_CORS_ORIGINS` and every third-party
credential are env-driven.

`ENVIRONMENT` defaults to `production` deliberately, so a forgotten variable can
never ship forgeable default secrets; development, test and CI set `development`
explicitly.
