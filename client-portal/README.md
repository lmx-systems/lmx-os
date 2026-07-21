# LMX Client Portal

Client-facing web app (Phase 8, see `../docs/ROADMAP.md`) - separate from
the internal `../dashboard/` since the audience, auth, and data scope all
differ. One login per client company (not per-user), showing that
client's order history and account details. Billing is placeholder/minimal
today (`fee_cents` per order) pending a full billing system.

Talks to the backend's `/client/*` API (`../app/api/client_routes.py`),
authenticated with a JWT from `/client/auth/login` - a separate auth
domain from the ops-dashboard login `../dashboard/` uses, since this has
its own real per-client auth (`../app/client_auth/`).

New client companies are onboarded by LMX ops via `POST /admin/clients`
(`../app/api/admin_routes.py`), not through this app - there's no
self-service signup.

## Local development

```bash
cp .env.example .env   # defaults to http://localhost:8000
npm install
npm run dev             # http://localhost:5174
```

Requires the backend (`../app`) running separately, and at least one
client onboarded via `POST /admin/clients` to log in with.

## Build

```bash
npm run build      # type-checks + builds to dist/
npm run lint        # oxlint
```

## Docker

Built as part of the root `docker-compose.yml` (`client-portal` service,
port 5174) - same pattern as `dashboard`, on a different host port so both
can run at once. Note: the API base URL is baked in at image build time
(Vite env vars aren't available at runtime in a static build) - see the
comment in `Dockerfile` if you need to point at a different API URL.
