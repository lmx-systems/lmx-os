# LMX OS — Orchestrator Dashboard

Internal tool for hub staff: live fleet state, the batch-hold queue, order
counts by status, and manual triggers for the Dispatch Optimizer and
Learning Loop nightly job. See `../docs/ARCHITECTURE.md` (Orchestrator
dashboard section) for the full picture, including the current lack of API
authentication — **do not point this at anything but a local/private
backend until that's addressed.**

## Local development

```bash
cp .env.example .env   # defaults to http://localhost:8000
npm install
npm run dev             # http://localhost:5173
```

Requires the backend (`../app`) running separately — see the repo root
README for that.

## Build

```bash
npm run build      # type-checks + builds to dist/
npm run lint        # oxlint
```

## Docker

Built as part of the root `docker-compose.yml` (`dashboard` service, port
5173). Note: the API base URL is baked in at image build time (Vite env
vars aren't available at runtime in a static build) — see the comment in
`Dockerfile` if you need to point at a different API URL.
