# LMX OS — Full-System Roadmap

Two things in one document: (1) every open item across the whole system —
not just the driver app — in one place, and (2) a phased plan to take LMX
OS from "code that works in a demo" to "running Hub 1 for real."

This supersedes the "Recommended next steps" list at the bottom of
`docs/ARCHITECTURE.md` (still there for historical context) and sits above
`docs/NEXT_STEPS.md`'s row-by-row punch list — that file is the detailed
backlog; this one is the map of how those rows fit into getting to launch.

## Part 1 — Every open item, in one place

Nothing below is new work discovered today — all of it was already called
out somewhere in `docs/ARCHITECTURE.md`, `docs/NEXT_STEPS.md`, or
`driver-app/README.md` as this got built. This just pulls it into one
list instead of leaving it scattered across three documents.

### Business / org (not code, but gates what the code is for)

| # | Item | Why it matters |
|---|---|---|
| B1 | Hire the senior backend engineer | Peer review names this the critical path — "do not hire down." Nothing below scales past one person without this. |
| B2 | Sign the first client contract | Unlocks real Epicor payload verification and the only real test of the 2.5 DPH assumption. Doesn't block most engineering work below, which can proceed on demo data. |
| ~~B3~~ | ~~Get access to the Source of Truth Index~~ | **Done** — the index (Google Drive) points to `LMX_OS_Tech_Strategy_and_Design.docx` and `LMX_OS_Architecture.docx` as the real technical spec. Both the batch-hold "4-question decision logic" and SLA hold-window minutes (E4/E5) have been checked against them and corrected. |
| B4 | Provision a real Rippling account/API credentials | **Rippling** was chosen (not ADP/Gusto — handles both W2 payroll and 1099 contractor payments on one platform, reusing the same integration across the worker-classification phases below). `app/payroll/`'s `PayrollProvider` interface and the real hours/overtime engineering behind it are built and tested against a stub (`docs/NEXT_STEPS.md` item 22) — no money moves until a real account exists and `RipplingPayrollProvider`'s endpoint shape (a best-effort guess, unverified) is confirmed against it. |
| B5 | Provision a real Twilio account + phone number | Every SMS today (OTP codes, masked customer/support messaging) runs through a stub that logs instead of sending. |

### Core backend — unverified or placeholder logic

| # | Item | Why it matters |
|---|---|---|
| E1 | Verify the Google Route Optimization client against a live Google Cloud project | Real client is built (`app/optimizer/google_routes_client.py`) but has never made one real `optimizeTours` call — the request/response mapping is unverified. |
| E2 | Tune `SLA_TIER_SKIP_PENALTY` values | **Investigated, still open** — checked against the same Source of Truth documents that resolved E4/E5 (`LMX_OS_Tech_Strategy_and_Design.docx`, `LMX_OS_Architecture.docx`) and the canonical Unit Economics doc (Data Room item 7): none specify a skip-penalty value or per-tier dispatch-priority ratio — Unit Economics covers van-vs-autonomy cost, not per-SLA-tier dispatch weighting. Unlike E4/E5, this is a pure solver-tuning constant (Google Route Optimization's `penaltyCost`), not a documented business rule — there's nothing to check it against until real per-tier route economics exist from an actual operating hub (same gate as E9's DPH validation, gated on B2). The current relative ordering (HOT_SHOT > T1 > T2 > T3) is already directionally correct per every doc's urgency hierarchy; only the exact magnitudes are unconfirmed and unconfirmable before then. (An older, non-canonical "Hub Unit Economics" doc with drone-based T1/T2/T3 pricing exists in Drive but isn't in the Source of Truth Index's artifact list and describes a since-superseded drone-hub concept — its tier semantics don't match this codebase's urgency tiers, so it isn't usable here.) |
| E3 | Confirm real Epicor payload field names | `OrderNum`/`ShipToNum`/etc. are a guess (`app/ingestion/adapters/epicor.py`), not checked against a real tenant. Peer review calls this the most common cause of Phase 1 slippage. |
| ~~E4~~ | ~~Verify the batch-hold "4-question decision logic" against the Source of Truth Index~~ | **Done** — confirmed against `LMX_OS_Tech_Strategy_and_Design.docx`. Questions 1 (SLA deadline) and 2 (0.8mi cluster radius) already matched. Question 4 didn't: the built version used a fabricated absolute hold-time cap instead of the real check (would dispatching now strand a more urgent, still-held order about to need this same scarce driver supply) — now replaced (`app/batch_queue/queue.py`'s `_would_conflict_with_a_more_urgent_order`). Question 3 (driver already heading this direction) still isn't implemented directly here — see `app/optimizer/service.py`'s mid-route insertion for the closest equivalent. |
| ~~E5~~ | ~~Recalibrate SLA hold-window minutes~~ | **Done** — confirmed spec values from the same doc: T1=8min, T2=90min, T3=1080min (18hrs), replacing a placeholder guess (T1=10, T2=45, T3=120) that was off by 2x on T2 and 9x on T3. Per-shop overrides already exist (`active_rules`), so further retuning against real Hub 1 data remains a data change, not a code change. |
| E6 | Confirm the Learning Loop's flag-type naming convention with a driver-app stakeholder | `HOLD_TOO_SHORT_FLAG`/`HOLD_TOO_LONG_FLAG` (`app/learning_loop/detection.py`) is a proposed contract, not one anyone outside this build has signed off on. |
| ~~E7~~ | ~~Wire a real scheduler for the Learning Loop's nightly job~~ | **Done** — `app/learning_loop/scheduler.py`'s `LearningLoopScheduler`, same "asyncio background loop + Redis distributed lock" shape as `app/events/bus.py`'s `HubEventBus`, started at app startup alongside it. Runs once a day, per hub, at that hub's own local 2am (`Hub.timezone`, via stdlib `zoneinfo`) — not one fixed UTC hour, so hubs in different timezones each get a real "nightly" run. `POST /learning-loop/{hub_id}/run-nightly-job` still exists for manual triggering/testing. |
| ~~E8~~ | ~~Move the event bus off in-process~~ | **Done** — `app/events/bus.py` now coordinates through Redis (a `dirty_hubs` set for idempotent cross-instance coalescing, a `SET NX EX` lock for mutual exclusion) instead of local asyncio state, with a fixed-interval poll loop started at app startup. Live-verified with two real, separate app containers sharing one Redis: an event published on instance A while instance A was paused (never got to run its own poll loop) was still picked up and completed by instance B; letting both race normally, exactly one of them ran a given hub's cycle, never both. |
| E9 | Validate the 2.5 deliveries-per-hour (DPH) figure | Called out by the peer review as a model assumption, not an established fact — only provable with real driver/order data at Hub 1 (gated on B2). |
| E10 | Tune HOT_SHOT's skip-penalty/hold-window placeholders | Phase 8 added `HOT_SHOT` ahead of T1 in `SLA_TIER_SKIP_PENALTY` and a 2-minute hold window (`app/sla/engine.py`, `app/optimizer/google_routes_client.py`). Hold window: same status as E5 was before it was resolved — but HOT_SHOT postdates the Source of Truth docs entirely (Phase 8 was added to this codebase after those were written), so there's no confirmed value to check it against; 2 minutes remains a reasoned guess. Skip-penalty: same "no spec exists, not calculable before real data" status as E2 above. |
| ~~E11~~ | ~~Real hours-worked/overtime calculation + admin payroll-run endpoint~~ | **Done** — `app/payroll/hours.py` replaces the old route-span earnings heuristic with real on-duty hours from a durable `driver_shift_events` log, plus federal 40hr/week overtime for `w2` drivers. New `POST /admin/payroll/{hub_id}/run`. Known gaps: no state-specific daily-OT rules, and a workweek split across two pay periods only sees hours visible in the period being computed — see `docs/NEXT_STEPS.md` item 22. |
| ~~E12~~ | ~~Real vehicle-capacity tracking for mid-route insertion~~ | **Done** — replaced the placeholder `MAX_STOPS_PER_ACTIVE_ROUTE` stop-count cap with real `DriverState.capacity_units - load_units` tracking, now actually incremented/decremented by `complete_stop`/`flag_stop_issue` — see `docs/NEXT_STEPS.md` item 21. |

### Security & production readiness

| # | Item | Why it matters |
|---|---|---|
| ~~S1~~ | ~~Real per-user authentication for the ops dashboard~~ | **Done** — `app/ops_auth/`'s `OpsUserAuthMiddleware` replaces the old shared `X-API-Key` with a real per-account Bearer JWT (same password+JWT shape as the client portal); `dashboard/` has a real login screen. `scripts/create_ops_user.py --role admin\|viewer` bootstraps accounts. Real role model now exists (migration `0012`): `admin` (everything) vs `viewer` (read-only) - `require_admin` gates the mutating endpoints (run-cycle, run-nightly-job, onboard a client, revoke a driver device, run payroll), and the dashboard hides those controls entirely for a viewer rather than showing them disabled. Live-verified with real admin and viewer accounts over real HTTP and in a real browser. Still just two roles, not a full permissions matrix - revisit if a reason for finer granularity ever shows up. |
| ~~S2~~ | ~~Secrets management~~ | **Partially done** — `app/secrets_provider.py`'s `SecretsProvider` abstraction (`EnvSecretsProvider` today, a real `AWSSecretsManagerProvider` ready but unexercised without a real AWS account) loads into `os.environ` before `Settings` is constructed in `app/config.py`, so a real vault's values take precedence over `.env` with zero changes needed anywhere else `settings.foo` is read (`os.environ.setdefault` never overrides an explicit env var). Same "unconfigured -> stub" status as Twilio/Rippling/Sentry, one level up. Real gap: which vault to actually adopt, when to migrate, and how rotation should work operationally are still open, deployment-platform-specific decisions (same nature as S3). |
| ~~S3~~ | ~~A real production hosting decision~~ | **Partially done** — `infra/` (AWS, Terraform): managed Postgres (RDS, automated backups, storage autoscaling) and Redis (ElastiCache), the app/dashboard/client-portal each as autoscaled ECS Fargate services behind one ALB, secrets in AWS Secrets Manager (the real account `app/secrets_provider.py`'s `AWSSecretsManagerProvider` was built for), a GitHub Actions deploy pipeline via OIDC (no stored AWS keys). `terraform validate`-clean but not yet applied against a real AWS account — same "real code, unexercised against a live account" status as Google Route Optimization/Rippling/Twilio. See `infra/README.md` for the real named gaps (no staging environment, no NAT Gateway, HTTPS needs a real owned domain first). |
| ~~S4~~ | ~~Observability~~ | **Partially done** — error tracking via Sentry (`app/logging_config.py`), same "unconfigured credential -> no-op" status as Twilio/Rippling until a real account/DSN exists. A structlog processor forwards warning/error/critical/exception-level events straight to Sentry, since this codebase's structlog setup never touches stdlib logging (Sentry's default `LoggingIntegration` hook would otherwise miss every "caught, logged, and intentionally swallowed" exception, e.g. `HubEventBus`'s handler-failure path) - so both unhandled exceptions (via the FastAPI/Starlette integrations) and deliberately-caught-and-logged ones reach Sentry. Metrics dashboards/alerting still not started. |
| ~~S5~~ | ~~General API rate limiting~~ | **Done** — `app/rate_limit.py`'s `GeneralRateLimitMiddleware`, a Redis counter+NX-TTL per client IP (deliberately generous - this system leans on client-side polling, see the module's own docstring), 429 + `Retry-After` once tripped, `/health`/docs paths exempt. Known limitation: keyed by the direct TCP peer, not `X-Forwarded-For` - correct only until a real reverse proxy sits in front (Phase 5's hosting decision). |
| ~~S6~~ | ~~A real security review~~ | **Partially done** — a self-review pass across auth, authorization, injection/input-validation, and secrets/CORS/infra. Fixed: driver OTP codes were unconditionally echoed in the API response regardless of Twilio configuration (`app/driver_auth/otp_store.py` — a hardcoded `sent_via_sms=False` meant this would have kept leaking even with real Twilio creds configured, since no real send was ever wired up either; now actually sends via `TwilioSmsClient` and only omits the code when that succeeds), the phone-number-existence check on `request-otp` was an unthrottled enumeration oracle (rate limit now charged before the DB lookup), two fleet-state-mutation endpoints were missing `require_admin` (a viewer could overwrite any driver's status/location), `docker-compose.yml`'s Postgres/Redis ports were published on every interface with a well-known default password, the app container ran as root, a few request bodies took unconstrained strings where a `Literal`/length bound was cheap and correct, and the Twilio webhook now warns loudly at boot if signature verification would be silently disabled in production. Real gap still open: the JWT-secret/webhook boot-time checks all key off `ENVIRONMENT != "development"`, so an operator who simply forgets to set `ENVIRONMENT` in production gets zero protection instead of the most — a cross-cutting fail-safe-default question worth a deliberate decision, not a change made unilaterally in this pass. No one outside this build has reviewed it yet either. |
| ~~S7~~ | ~~Twilio inbound-webhook signature verification~~ | **Done** — `app/messaging/twilio_signature.py` verifies `X-Twilio-Signature` (HMAC-SHA1 over the full URL + sorted POST params, keyed by `TWILIO_AUTH_TOKEN`), enforced only once that token is configured; `TWILIO_WEBHOOK_BASE_URL` overrides scheme+host for the eventual reverse-proxy case. |
| ~~S8~~ | ~~Rate-limit `POST /client/auth/login`~~ | **Done** — `app/client_auth/login_rate_limit.py`, same "counter + NX-guarded TTL" shape as driver OTP issuance; resets on a successful login. |
| ~~S9~~ | ~~Enforce `CLIENT_JWT_SECRET` ≠ `DRIVER_JWT_SECRET` at startup~~ | **Done** — `app/config.py`'s `assert_jwt_secrets_are_distinct()`, called from `app/main.py`'s lifespan alongside the two existing per-secret checks; refuses to start outside `development` if both are ever set to the same real value. |

### Orchestrator dashboard (internal, for hub staff)

| # | Item | Why it matters |
|---|---|---|
| ~~D1~~ | ~~Add a "list hubs" endpoint~~ | **Done** — `GET /hubs` (`app/api/routes.py`, `app/schemas/hub.py`'s `HubSummary`) backs a real dropdown in `dashboard/src/components/TopBar.tsx`; the old raw-UUID text field survives only as a fallback if that fetch fails or returns empty. The "Onboard a new client" form takes `hubId` from the same TopBar-selected value, so it inherited the fix with no separate change. |
| ~~D2~~ | ~~Stop baking the API URL in at Docker build time~~ | **Done** — `dashboard/docker/generate-env-config.sh` runs as an nginx entrypoint script at container start, writing `env-config.js` from the real `DASHBOARD_API_BASE_URL` env var; `src/lib/api.ts` reads `window.__RUNTIME_CONFIG__` first, falling back to the Vite build-time env var for local `npm run dev` only. Pointing the same image at a different API is now a restart, not a rebuild. `client-portal/` mirrors this exact pattern. |

### Cross-app / branding

| # | Item | Why it matters |
|---|---|---|
| ~~D3~~ | ~~Real brand assets + unified brand-green accent across dashboard, client portal, driver app~~ | **Done** — placeholder "L"/"LX" box logos and mismatched indigo/approximate-green accents replaced with the real LMX mark and the decided brand green (`#0A6644`) everywhere. See `docs/NEXT_STEPS.md` item 20. Known gaps: no vector master (SVG/AI) exists, so every asset is raster-derived; native icon/splash/adaptive-icon changes need a real device build (A6) to verify visually — Expo Go always shows its own icon regardless of `app.json`. |

### Driver app

| # | Item | Why it matters |
|---|---|---|
| ~~A0~~ | ~~Screen consolidation, flag-an-issue, offline write queue, device-bound biometric auth, live route-change push~~ | **Done**, ahead of this roadmap's original sequencing — matches a separate wireframe spec's design intent (consolidated screens, offline-first, device-bound re-entry instead of repeated OTP, live notification of mid-route changes) discovered mid-build. See `docs/NEXT_STEPS.md` item 19 for full detail. Real gap: this is foreground-only SSE (the driver has to have the app open), not true OS-level push — A1 below (job-offer push while backgrounded/killed) is still a distinct, unstarted gap. |
| ~~A1~~ | ~~Push notifications~~ | **Partially done** — `app/messaging/push_client.py` (Expo push, same "unconfigured -> stub" shape as Twilio) + `app/messaging/job_offer_notifications.py`, called from `app/optimizer/service.py` the moment a `RouteOffer` is actually committed, notify every registered, non-revoked device for that driver. `POST /driver/me/push-token` registers a device's Expo push token. Driver-app side: `notifications/registerForPushNotifications.ts` requests permission and registers the token after sign-in. Real gap: the driver app has no EAS project id configured yet (`app.json`), which `getExpoPushTokenAsync()` needs to mint a real token — registration deliberately no-ops until that exists (same status as A6, the app-store deployment pipeline, since both need a real EAS project). |
| ~~A2~~ | ~~Real camera/barcode scanning~~ | **Done** — `media/BarcodeScannerModal.tsx` wraps `expo-camera`'s real barcode scanner; a manual "can't scan? confirm manually" fallback stays in `ParcelScanPanel.tsx` for a damaged/unreadable barcode. No backend change needed — the contract only ever wanted a running `scanned_count`. |
| ~~A3~~ | ~~Real photo/signature capture + upload pipeline~~ | **Done** — `media/PhotoCaptureModal.tsx` (expo-camera) and `media/SignaturePadModal.tsx` (`react-native-signature-canvas`) capture for real; `app/storage/photo_upload_client.py` issues a presigned S3 upload URL (same "unconfigured -> stub" status as Twilio/Rippling/Expo push — the stub reissues this app's original `local-capture://` marker when no bucket is configured, so nothing downstream had to change to keep working). New `POST /driver/stops/{stop_id}/upload-url`; `CompleteStopBody.photo_url`/`signature_url` unchanged, now carrying a real URL instead of a client-fabricated one. Known scope boundary: capture/upload needs live connectivity (the offline outbox only ever queued plain JSON, never binary blobs) — arrive/scan/complete remain fully offline-safe regardless. |
| ~~A4~~ | ~~A real PIN-issuance/verification system~~ | **Done** — `app/messaging/delivery_pin.py` generates a real 4-digit PIN (`secrets.randbelow`) and texts it (same masked-SMS mechanics as `message_customer`, its own `channel="delivery_pin"` so it doesn't leak into the driver-facing customer thread) at `accept_offer` time, for every dropoff whose order has a `delivery_contact_phone`. `complete_stop` checks the driver's submitted PIN against `Stop.delivery_pin` for real, with a `Stop.pin_verification_attempts` lockout after `MAX_PIN_VERIFICATION_ATTEMPTS` (5) wrong tries. Driver-app side: PIN completion bypasses the offline outbox (unlike photo/signature, a wrong PIN is a real, meaningful failure, not something to fire-and-forget) and calls the API directly, surfacing a rejection inline so the driver can ask the customer again. No real gap — self-contained, no external account dependency. |
| ~~A5~~ | ~~Maps SDK / turn-by-turn navigation~~ | **Done** — `driver-app/src/utils/navigation.ts`'s `openTurnByTurnNavigation` hands off to the device's own native maps app (Apple/Google Maps on iOS, Google Maps' turn-by-turn intent on Android) instead of embedding a maps SDK/rendering a route in-app — both already do live turn-by-turn, voice guidance, and real-time rerouting far better than reimplementing that here would. No API key needed (plain URL schemes/intents, not the Google Maps SDK's embedded-view APIs `settings.google_maps_api_key` actually gates); falls back through Google Maps → Apple Maps → a plain `https://maps.google.com` web URL, so something always opens even with neither app installed. New "Navigate" button on `StopDetailScreen.tsx`, available for any non-terminal stop (pickup or dropoff). Live-verified in the iOS Simulator: `maps://` opens Apple Maps, an unavailable `comgooglemaps://` fails cleanly (confirming the fallback chain), and the web fallback opens Safari. |
| ~~A6~~ | ~~Mobile app store deployment pipeline~~ | **Partially done** — `driver-app/eas.json` (real development/preview/production build profiles + submit config), `eas-cli` as a devDependency, `.github/workflows/eas-build.yml` (manual dispatch or a `driver-app-vX` tag, gated on an `EXPO_TOKEN` repo secret). Same "real config, unexercised against a live account" status as `infra/` (S3) — no Expo account/project exists yet, so `app.json`'s `extra.eas.projectId` doesn't either. One `eas init` closes this and, as a side effect, unlocks A1's push notifications too (see `driver-app/README.md`'s EAS section) — no code change needed either way. |
| ~~A7~~ | ~~Masked voice calling~~ | **Done** — `app/messaging/voice_client.py` (same "unconfigured -> stub" shape as SMS) places a real Twilio Voice call to the *driver's own phone*; once they answer, `app/api/webhooks.py`'s `voice_connect` returns TwiML that `<Dial>`s the customer with LMX's shared number as caller ID — two bridged real phone calls, not in-app audio, so neither side ever sees the other's real number. New `app/models/call.py` (its own table, not folded into `Message` - a call has no body text and its own status lifecycle) logs each attempt; `voice_status` records the final Twilio status/duration via StatusCallback. Driver-app's "Call" button (`StopDetailScreen.tsx`) now calls `POST /driver/stops/{id}/call` for real instead of showing a dead-stub alert. No real gap - self-contained, same Twilio account as SMS. |
| ~~A8~~ | ~~Harden inbound-SMS reply matching~~ | **Done** — `_find_matching_thread` (`app/api/webhooks.py`) infers channel from `From` before matching (closes a cross-driver collision: every driver's support messages previously shared one `counterparty_phone`), prefers threads with no inbound reply already recorded, and requires a customer-channel stop to still be non-terminal. Real, flagged remaining gap: two genuinely concurrent, still-unanswered threads to the same number can't be told apart without a Twilio Proxy-style number pool or a reply reference code - now logged as ambiguous rather than silently guessed at. |
| ~~A9~~ | ~~Real earnings formula + payroll integration~~ | **Researched + engineering extension points done** — `docs/PAYROLL_STATE_OT_RESEARCH.md` covers why state-specific daily-OT rules (e.g. California's 8hr/day threshold) are genuinely a business/legal decision, not something to guess at in code, and what states are commonly cited (background only, not verified against statute text or implemented). Built ahead of that decision: `app/payroll/overtime_rules.py`'s pluggable `OvertimeRule` registry keyed by `Hub.state_code` (new, migration `0017`, nullable/unpopulated), and `app/payroll/hours.py`'s new `daily_hours_worked_from_shift_events` (per-day hour bucketing a daily-threshold rule needs, which nothing computed before). Every hub defaults to the existing federal-only rule — zero behavior change until a real state rule is researched and registered. Real remaining gaps, not code: which hubs are actually in which state (`Hub.state_code` needs populating — factual, but undone), real legal sign-off on which states' rules to turn on, and confirming the Rippling integration against a live account (same status as B4 elsewhere). |
| ~~A10~~ | ~~1099 contractor onboarding — resolve the worker-autonomy question~~ | **Researched** — `docs/A10_1099_WORKER_AUTONOMY_RESEARCH.md` covers general worker-classification frameworks (federal, California's ABC test/Prop 22-style carve-out given the direct litigation history for app-based delivery platforms — background only, not verified against current statute or legal advice) and a verified, honest inventory of today's actual model. The gap is narrower than it first reads: driver-owned vehicles, no exclusivity, no minimum hours, and zero tracked consequence for declining an offer are all already true today (verified directly against the code) and favor contractor status with no changes needed. The one concrete, flagged factor is that a driver never sees more than one job at a time — the optimizer removes them from the available pool the instant an offer is created, so there's no real menu of concurrent choices, just accept-or-wait-for-the-next. No product change was made this pass — building a multi-offer model (or anything else) without knowing whether it's actually required would be guessing at the legal answer, the same trap `docs/PAYROLL_STATE_OT_RESEARCH.md` already flagged for state overtime rules. Needs real employment-law counsel before 1099 onboarding ships, same as before — now with a precise, code-verified question to bring them instead of a vague one. |
| ~~A11~~ | ~~Gig per-delivery pay model~~ | **Pricing + payout core done** — `app/payroll/gig_pricing.py`'s `estimate_delivery_pay_cents` (explicitly-labeled placeholder base/per-mile/SLA-tier-bonus rates, same convention as `PLACEHOLDER_HOURLY_RATE_CENTS`) is real, shown on the offer itself (`JobOfferView.estimated_pay_cents`, gig drivers only — `OfferBanner.tsx`/`TodayRouteScreen.tsx`), and actually paid: `complete_stop` creates a real `GigPayout` row (own table, `unique(stop_id)` as an idempotency backstop) and calls a new `PayoutProvider` interface (`app/payroll/payout_provider.py`, `StripeConnectPayoutProvider`/`StubPayoutProvider`, same "unconfigured -> stub" shape as `PayrollProvider` — no Stripe account exists yet). New `Driver.stripe_connect_account_id` (migration `0018`) is where a payout would actually be sent; unset today, so every payout records as `skipped_no_payout_account` — owed, not silently dropped. `get_my_earnings`/admin's payroll run both updated so a gig driver's real per-delivery total replaces the old silent hours-x-rate fallback, and gig is correctly excluded from the hourly Rippling submission entirely (already paid instantly, not on a payroll cycle). Live-verified end-to-end over real HTTP. Real gaps still open, explicitly not attempted this pass: **self-serve onboarding** (drivers are ops-provisioned only today; no flow exists to link a Stripe Connect account) and **per-trip identity re-verification** (no selfie/liveness/KYC infrastructure exists anywhere) — both greenfield subsystems, not extensions of anything partial. |

### Whole components not started at all

C1 (client-facing dashboard) and C2 (shop SMS) — the two items that used
to be listed here — shipped in Phase 8 (see below). What's left in this
category:

| # | Item | Why it matters |
|---|---|---|
| ~~C3~~ | ~~A real billing/invoicing system~~ | **Partially done** — `app/billing/service.py`'s `generate_invoice()` sweeps a client's delivered, priced, not-yet-billed orders in a date range into a new `Invoice` (`invoice_id` on `Order` prevents double-billing across periods); admin-triggered via `POST /admin/clients/{client_id}/invoices/generate`. Client portal has a real Invoices tab: a list + itemized detail view with a print-friendly layout ("Print / Save as PDF" via the browser, not a server-generated PDF binary — a deliberate scope cut, same class of decision as the payroll module's Rippling gate). Real gap still open: payment collection (a real processor, e.g. Stripe Connect) is explicitly out of scope here, same as B4 for driver payroll — this only produces a statement of what's owed. |
| ~~C4~~ | ~~Multi-user client accounts~~ | **Done** — the single inline login (`Client.portal_email`/`portal_password_hash`) is split into a real `client_users` table (migration `0019`: create + backfill every existing login into an `admin` row + drop the two inline columns), many named users per client, each with a role. Two roles (`admin`/`member`), same minimal line as ops's admin/viewer: an `admin` can manage the other users at their own client (invite/deactivate/change-role/reset-password via `GET`/`POST`/`PATCH /client/users`, own-client-scoped, with a last-active-admin lockout guard), a `member` is read-only on orders/invoices. Portal JWTs are now per-user (`sub`=client_user_id, plus `client_id`/`role` claims); `is_active` is re-checked every request (`app/client_auth/dependencies.py`), so deactivating someone revokes their session immediately, not at token expiry — same tradeoff `app/ops_auth/`. Onboarding (`POST /admin/clients`) creates the client's first admin user; the client's own admin adds the rest with no ops involvement. `scripts/create_client_user.py` mirrors `create_ops_user.py` for out-of-band seeding/lockout recovery. Client portal gained an admin-only "Team" tab (`client-portal/`). Live-verified: 342 backend tests passing, `tsc --noEmit` clean. Known scope boundary, deliberately not built: no data-scoped sub-roles (e.g. an AP contact who sees only invoices) — admin vs member is user-management-vs-not, not a per-view permission matrix; revisit if a real need shows up. Still no self-service client *company* signup (that's C5, by design). |
| C5 | Self-service client signup | New clients are onboarded only via the internal `POST /admin/clients` form (dashboard) — there's no client-initiated signup flow, by design (this is a B2B onboarding relationship, not self-serve SaaS), but worth naming explicitly so it isn't assumed to exist. |

### Operational workflow gaps

From the cofounder workflow review session (July 2026). That session
walked the order-to-delivery operation four ways and produced 39
persona-voiced user stories; cross-checking those stories against this
roadmap and the codebase surfaced seven whole workflows that **nothing
here or in the code was tracking**. These are not polish — several are
daily realities of the auto-parts trade that a distributor would notice
missing in week one.

| # | Item | Why it matters |
|---|---|---|
| W1 | Returns & core pickups as first-class work | Stories GS-7, CP-6, DR-9, exception `RETURNS_NOT_READY`, training case E6. Cores are, in the session's words, "half the economics of the parts trade." Today there is **no model, no endpoint, no route-stop type** for a pickup — the system can only deliver. Needs: pickup stops with an item manifest, per-shop pickup-readiness patterns, a counter-facing "awaiting pickup by shop, with age" list, and a reschedule workflow when a core isn't ready. |
| W2 | COD collection & payment disputes | Exception `COD_DISPUTE`, stories DO-8, training case E3. Nothing in code. The driver rule is unambiguous and must be enforced by the UI, not by training alone: *never negotiate, one tap escalates to the distributor, keep moving.* Needs a dispute flag, an escalation path to the distributor, and a repeat-dispute count per account feeding a monthly owner report. |
| W3 | SLA-breach invoice credits | Story DO-3: contractual credits when SLA thresholds are breached. C3's billing computes fees from delivered orders but has **no credit mechanism** — a breach costs nothing today. Needs the credit schedule as contract data, computed from order-level SLA outcomes, appearing as a line on the statement. Ties to F5 (rate tables) — same billing surface, build together. |
| W4 | Driver-visible scorecard | Story DR-10: the driver sees *the identical metrics and definitions* the orchestrator sees. Explicitly framed as a trust decision, not a feature — "a shared standard, not a camera pointed at me." Folds into I4/F7's analytics work; the requirement is that the driver view is the same computation, not a separate reduced one. |
| W5 | Counter-person order status lookup | Story CP-3: search any order by shop name or order number, get live status and ETA in ten seconds. The client portal today is distributor-owner-facing with one login per company (C4). The counter person is a **distinct persona** with a distinct need — and CP-4 (wrong-part flag reaching the counter mid-route) is a second counter-facing surface. Reopens C4's "one login per client" decision on real grounds rather than as an oversight. |
| W6 | Orchestrator-editable urgency configuration | Story OR-6: "body panels are never urgent" should not require a developer. Distinct from I2 (which promotes *machine-proposed* rules) — this is direct human authoring of part-type tier rules, editable without a code deploy. The `active_rules` table can likely carry it; the gap is the editing surface and validation. |
| W7 | Training-data rights in the customer contract | Session closing note: model-training rights, cross-customer aggregation rights, and anonymization terms "belong in customer #1's contract before the first delivery, not in a future amendment." Distinct from R3 (privacy policy — what LMX does with personal data); this is about the right to train models on a customer's operational data. **Legal work, gates B2, not engineering.** |
| W8 | Epicor staging-module qualification check | Session D6. Whether a prospect's Epicor runs a warehouse/staging module is now a sales-qualification checklist question asked before signing, because it determines whether real-time ingestion is even possible for that customer. Not code — a sales-process artifact that gates which prospects are viable. |

**Open design decision — W10, who owns the barcode:** two materially
different products, and picking wrong is expensive to undo once labels
are in the field.

| | LMX generates and applies its own label | Scan the distributor's existing pick-ticket barcode |
|---|---|---|
| Control | Full — LMX owns the ID space and its meaning | None — depends on their Epicor configuration |
| Works regardless of customer setup | Yes | **No** — only where their Epicor prints one |
| Hardware | Label printer at each warehouse; capex nobody has budgeted. Co-location makes it practical | None |
| Sales impact | Neutral | Becomes a hard qualification requirement, tightening the funnel — **directly compounds W8** |
| Driver step | One extra action per package at pickup | None — scan what's already there |
| Ties to | Nothing existing | W8 (a distributor running a warehouse/staging module very likely already prints barcoded pick tickets) |

**A second, smaller decision underneath it:** does the identifier
describe an *order* or a *package*? `parcel_count` already implies one
order can be several boxes (a caliper plus a box of pads), so these are
different data models — order-level is simpler, package-level is what
makes "3 of 5 collected" auditable rather than self-reported.

**Recommendation if it helps:** resolve W8 first. If the first signed
customer's Epicor already prints barcoded pick tickets, the
scan-existing-label path is dramatically cheaper and gets the WRONG_PART
win immediately; the LMX-label path only becomes necessary when a
customer without that setup has to be onboarded. That sequencing keeps
the decision reversible instead of committing to printers now.

**Sequencing:** W1 and W2 are Phase 6/8 work and both need a day-one
written playbook before the first delivery regardless of whether the
software exists (session decision D5 names `WRONG_PART`, `COD_DISPUTE`,
`SHOP_CLOSED`, and `RETURNS_NOT_READY` as the four day-one playbooks —
note that all four are exactly the exceptions where LMX touches someone
else's money or customer). W3 joins C3/F5 as Phase 8 billing work. W4
folds into Phase 10's I4. W5 reopens C4 in Phase 8. W6 is small and
no-dependency. W7 and W8 are business/legal items gating B2 and should
be moving now. W10 sits in Phase 6 alongside A2 — build them together,
since A2 without W10 is a scanner with nothing to scan; but resolve
W10's open decision (and ideally W8) before either is scheduled.

### Risk, compliance & real-world operations

Surfaced July 2026 by deliberately looking *outside* the existing docs —
these were not in `ARCHITECTURE.md`, `NEXT_STEPS.md`, or this roadmap,
which is exactly why they're worth naming. Three are business/legal
items nobody would think to write code for; three are engineering gaps
that existed only as a passing comment in a source file and had never
been promoted to a tracked item.

**Why this section exists at all:** every other section here tracks work
someone already knew about. These are the ones that could quietly become
the actual Hub 1 blocker precisely because no one is watching them —
R1–R3 in particular are not things engineering can solve, and they gate
putting real drivers on real roads with real customer data.

| # | Item | Why it matters |
|---|---|---|
| R1 | Insurance & liability plan (commercial auto, cargo, general liability) | If a driver has an accident or a package is lost/damaged, what covers it? Not named anywhere in any doc despite being existential for a delivery company. A business decision like B4/B5, not an engineering task — but unlike those two, nothing in the system even hints it's missing. **Gates Phase 9** (real drivers, real roads). Needs Rich/Matan. |
| R2 | Driver background checks & MVR (motor-vehicle record) screening | The system tracks license and insurance *documents* with expiry dates (`driver_documents`) and blocks going online when one is expired — but nothing verifies the driver was safe to put behind the wheel in the first place. Document expiry is not a background check. **Gates Phase 9.** Needs Rich. |
| R3 | Privacy policy & data-handling/retention policy | LMX OS stores customer names, delivery addresses, and phone numbers (orders + shop SMS), plus driver PII. No document says what LMX does with any of it, how long it's kept, or how someone requests deletion. Real legal exposure the moment there are real clients and real drivers; also the first thing an enterprise client's security questionnaire asks about, alongside F10's SOC 2. **Gates Phase 9.** |
| R4 | Driver document upload pipeline | `app/models/driver_document.py`'s own comment: "No file-upload pipeline exists… `file_url` accepts whatever string the client sends." A driver could submit a fabricated URL as their license scan and the system would treat it as valid. Distinct from A3 (proof-of-delivery photos) — this is *onboarding compliance* evidence, and it's what makes R2 enforceable in software rather than on paper. |
| R5 | Failed-delivery / redelivery workflow | `Stop.status` has a `failed` value, but nothing handles what happens next: no redelivery attempt, no client notification, no billing adjustment, no defined resolution path. Every real delivery operation gets refused packages, wrong addresses, and closed shops — today those orders would sit in `failed` forever. Also the gap behind Locus's "failed-delivery disputes" and P6's partner-dispute surface. |
| R6 | Hub closure / holiday calendar | Nothing models a hub not operating. The Learning Loop's nightly scheduler (E7) and the optimizer both assume every active hub runs every day — the first holiday, weather closure, or planned shutdown will either misfire the nightly job or dispatch routes for a hub that isn't open. |

**Sequencing:** R1/R2/R3 are business/legal work that should start *now*
— they're slow (insurance quotes, policy drafting, screening-vendor
selection) and they gate Phase 9, so starting them when the pilot is
imminent is starting them too late. R4 fits Phase 6 alongside A3 (same
file-upload infrastructure, build once). R5 and R6 fit Phase 4 — both
are "the system assumes the happy path" gaps of exactly the kind that
phase exists to close, and both will surface immediately in a real pilot.

### Autonomy partners

How autonomous delivery — AV cars, sidewalk bots, drones, each run by
their operator — plugs in (cofounder conversation, July 2026). The
decision: **no separate app; autonomy partners integrate into the same
dispatch loop the driver app uses, via a capacity-provider adapter
layer.** The driver app is the *human* interface to LMX's
offer→accept→track→deliver loop; a partner's fleet API is a *machine*
interface to the identical loop. Their operators supervise vehicles in
the partner's own console — LMX owns the delivery lifecycle, the partner
owns the vehicle. This is the supply-side mirror of the ingestion
layer's demand-side adapters (Epicor/flat-file): nothing downstream
should ever branch on which partner carried an order, the same way
nothing branches on which POS created it.

**Update, July 2026:** this abstraction now also covers gig-courier
human capacity (Uber/DoorDash-style), not just AV/drone/bot partners —
see P7 below. Sourabh's call: there's value in LMX being able to route
to whichever capacity is cheapest — its own fleet or a gig courier — as
a standing dispatch option, while unit economics and assumptions get
worked through, not just as a rare emergency valve. This reverses an
earlier same-day call to not build this at all (see the "Competitive
feature gaps" section's F9 history for the full back-and-forth) — worth
knowing it was a live debate, not a snap decision.

**Most of this is still not being built now.** The near-term rule it
imposes: when fleet/offer models get touched for other reasons,
generalize toward a "courier" abstraction rather than deepening the
human-driver coupling. P1–P6 (the AV/drone/bot side) remain gated on a
signed autonomy partner (a B-item when it becomes real). P7 (the
gig-courier side) is different — it doesn't need a signed AV partner,
gig-marketplace APIs exist today — but it does need real partner pricing
and the unit-economics numbers before it's built for real, not guessed.

| # | Item | Why it matters |
|---|---|---|
| P1 | Courier abstraction over the fleet model | Today's fleet state is human-shaped (`DriverCandidate`, phone/OTP auth, an implicit person behind every route). Generalize to a courier with a `provider_type` (human_lmx_driver \| autonomy_partner \| gig_courier), service area/geofence, speed profile, payload limits, and capability flags (can batch multi-stop? sidewalk-only? weather-sensitive?). Human LMX drivers become one provider type, not the type system. |
| P2 | Capacity-provider adapter layer | The supply-side mirror of `app/ingestion/adapters/`: one adapter per partner normalizing (a) capacity in — which vehicles/couriers are available, where, with what limits; (b) assignments out — a `RouteOffer` becomes an API call the partner's fleet manager (or gig-marketplace API) accepts/declines within the same TTL a driver gets; (c) status back — partner webhooks map to our stop-status transitions and PoD. Ships with a stub partner (same unconfigured→stub pattern as Twilio/Rippling) so the whole loop is testable before any real partner exists. |
| P3 | Mode-aware dispatch | The optimizer gains eligibility filtering (weight, distance, geofence, tier, weather) before candidate generation, and — later — cost-per-drop mode selection: choose the cheapest *eligible* mode per order, whether that's an autonomy partner or (per P7) a gig courier. Ties directly to the unit-economics work (cost per drop vs. price per drop). |
| P4 | Unmanned handoff + proof of delivery | Nobody walks into the shop when a bot arrives: shop SMS grows a "load the bot" flow (compartment id, load-confirmed ack), and the customer side needs PIN-unlock delivery — which is exactly the A4 PIN issuance/verification item already on the driver-app list, making A4 shared infrastructure rather than app polish. |
| P5 | Partner settlement | Per-delivery payout to the partner — a third money flow next to client billing (in) and driver payroll (out), structurally the same shape as `client_rates`: per-partner, per-mode rates, monthly statements. Reuses C3's statement machinery. Also the payout mechanism for P7's gig couriers. |
| P6 | Partner portal | A thin reporting surface (delivery history, settlement statements, failed-delivery disputes) like `client-portal/` — explicitly a *later* convenience, not the integration mechanism. Partners integrate through P2's API, full stop. |
| P7 | Gig-courier cost-optimized dispatch (reinstated F9) | A standing dispatch option — not just an SLA-emergency valve — where the optimizer can route an order to a gig-courier marketplace (Uber/DoorDash-style) when it's the cheaper *eligible* option, per P3. Client never sees or chooses this — same LMX brand, SLA, and billing either way. Three guardrails, non-negotiable: (1) every gig-courier-dispatched order is tagged distinctly in the data model so I1/I4's analytics and the Learning Loop don't treat it as LMX-optimizer ground truth; (2) a volume cap/threshold per hub so this stays an optimization, not a silent shift of capacity away from LMX's own fleet; (3) built against real gig-courier pricing data, not guessed rates — same discipline as P3's existing gating. Gated on unit-economics work, not on a signed AV partner. |

### Competitive feature gaps

LMX is positioning LMX OS as the operating system for the whole company,
not just a dispatch tool — so it's worth checking it against the
category it's actually competing in. This is a feature-by-feature
comparison against four delivery/logistics platforms researched in July
2026: **Bringg** and **Wise Systems** (named directly), plus **Onfleet**
and **Locus** (added to round out the set — respectively the
small/mid-market and enterprise-retail-logistics ends of the same
category). Sourced from each vendor's public site, docs, and G2/Capterra
where accessible — see `docs/LMX_OS_Competitive_Feature_Analysis.docx`
for the full category-by-category tables and citations.

**Where LMX OS already holds its own:** the SLA-tier engine's strict
Hot-Shot non-commingling guarantee is a concrete, enforced rule none of
the four describe as precisely; the batch-hold queue's 4-question
decision logic is a more explicit, tunable batching strategy than the
generic "smart clubbing" language competitors use; the Annotation &
Learning Loop's human-approval gate (I2) is a more auditable model than
Locus's "agentic" DiSCO framing or Wise's compounding ML claims, once
I2 ships; and the autonomy-partner architecture (P1–P6) is already
designed in at the courier-abstraction level — none of the four have
live drone/sidewalk-bot/AV integration today, only Bringg lists
"autonomous" as a network category in concept.

**Where the gap is real** — the biggest single finding: LMX OS has **no
live GPS tracking at all** today. `Driver` has no location field, the
driver app never pings a position, and neither the ops dashboard nor any
customer-facing surface can show where a driver actually is. Every one
of the four competitors treats live map tracking as baseline table
stakes. That's F1/F2 below, and it's the prerequisite for F3.

| # | Item | Why it matters |
|---|---|---|
| F1 | Live driver location pipeline | Driver app periodically pings lat/lng to the backend; `Driver`/a new `DriverLocation` table stores current position. Prerequisite for F2 and F3 — today this doesn't exist at all, not even for internal ops use. No external dependency. |
| F2 | Live map view (ops dashboard) | Hub staff can see where every driver on shift actually is, not just their assigned stop list. Depends on F1. |
| F3 | Customer-facing live tracking page | A public link (sent to the actual delivery recipient, not the shop) showing driver position + ETA — what Bringg/Onfleet/Locus lead with on their marketing sites. Depends on F1; new component, no external dependency beyond a public route. |
| F4 | Outbound status webhooks + a small integrations surface | Today's ingestion adapters are demand-side *in* (Epicor, flat-file); nothing goes back *out* — no webhook a client system can subscribe to, no Shopify/Zapier-style connector. Bringg, Onfleet, and Wise Systems all name this as a feature. |
| F5 | Flexible/rate-table billing | `client_rates` today is flat per-drop, per-tier. Onfleet and Locus both support per-piece/per-weight/per-mile rate tables — worth revisiting once C3's statement persistence (already open) is tackled, same billing surface. |
| F6 | Real-time mid-route re-optimization | Today's optimizer solves fresh each cycle (E7's scheduler) rather than continuously re-sequencing an in-progress route as conditions change — Wise Systems' and Onfleet's core marketing claim. Depends on E1 (verify the live Google Route Optimization client) being done first. |
| F7 | Client- and ops-facing analytics dashboards | Reinforces I4 (already on the roadmap) — DPH, on-time %, driver leaderboards, cost-per-drop trend, SLA-breach history, CSV/BI export — but every competitor also exposes a *client-facing* cut of this (their own on-time rate, delivery volume) in the portal, which I4 doesn't currently scope. Directly serves the "market adoption" story for a distributor moving to per-drop pricing — it's the retention/upsell proof, not just an ops nicety. |
| F8 | White-label / multi-brand portal theming | Bringg, Onfleet (Enterprise tier), and Locus all offer a rebrandable client-facing surface. Relevant if LMX ever resells through a partner or franchise model — not urgent for Hub 1. |
| ~~F9~~ | ~~Hybrid gig-fleet overflow dispatch~~ → **Reinstated as P7** (Sourabh, July 2026 — see Autonomy Partners section) | Wise Systems' "DoorDash Dial" auto-routes overflow orders to third-party gig couriers by cost/rules. This item's history in one place, since it flipped twice in one day: (1) first flagged as an unresolved conflict against a companion analysis's "LMX is the fleet, not a Bringg competitor" stance; (2) decided **not** to build it, full stop; (3) revisited same-day and reinstated — with three guardrails (data tagging, a volume cap, real pricing before building) — as a *generalized cost-optimized dispatch option* rather than a narrow overflow valve, folded into P1–P3's courier abstraction as **P7**, gated on unit economics rather than dropped as a permanent no. |
| F10 | A real path to SOC 2 (or equivalent) certification | Every one of the four competitors leads their security page with SOC 2 Type II (plus ISO 27001, sometimes HIPAA/GDPR audits). Reinforces S6 (security review) and S2 (secrets management) — this raises their urgency from "good hygiene" to "the thing enterprise clients will ask for in a security questionnaire." A companion analysis adds a concrete trigger: enterprise dealer groups (the recommended anchor client type) ask for SOC 2 in diligence, and it's a multi-month audit — start readiness well before it's needed, not when it's blocking a deal. |
| F11 | SSO/SAML for ops and client logins | S1 built real per-user auth with roles, but not SSO — Bringg, Wise Systems, and Locus all support it for enterprise buyers. |
| F12 | Network/territory optimization tooling | Wise Systems' "Network Optimization" (depot/zone redesign, distinct from daily routing) — relevant once LMX runs multiple hubs, not for a single Hub 1 pilot. |
| F13 | Ratings & feedback capture | One-tap post-delivery rating (+ optional comment) prompt to the shop, landing on the order/stop record. Low effort, and it feeds the Learning Loop (I3's broader annotation vocabulary) with a ground-truth satisfaction signal none of the four researched competitors structurally capture the same way. No external dependency. |
| F14 | Orchestrator route-preview / shadow mode | **Substantially upgraded July 2026 — see W9 below.** Originally scoped as a view to preview the optimizer's proposed plan before it commits. The workflow session's decision D3 makes shadow mode far larger: the standard onboarding gate for *every* customer engagement, not a one-time pilot tool. The preview/override surface described here is still wanted, but it is now the small half of this item. |
| W10 | Package identity & scan-at-pickup verification | **Nothing in this system gives a package a unique identity.** `Stop.parcel_count`/`scanned_count` are two integers, and `POST /driver/stops/{id}/scan` takes `{scanned_count: int}` — a *number*, never a scanned value. There is no `Parcel` model and no barcode field anywhere; `Order.external_order_ref` is the distributor's order number, per order rather than per package. Note this makes **A2 mis-scoped**: A2 reads as "wire in a camera SDK," but wiring one in today would leave nothing to scan, because no barcode is ever generated, printed, or recorded. Neither the session doc (39 stories, 36 training situations) nor any prior doc mentions barcodes or chain of custody at all. **The payoff that justifies it:** `WRONG_PART` is currently caught at the door and the session calls it "the most expensive recoverable error"; a scan-at-pickup check against the order catches it in the warehouse before the driver leaves. Also unlocks real chain of custody (all four benchmarked competitors advertise it), gives W1's returns/cores an identity to track, and makes DR-6's batched multi-order handoffs verifiable. Raised by Sourabh, July 2026. **Design decision open — see below.** |
| W9 | Shadow-mode comparison engine & cutover scorecard | The real shape of shadow mode per session decision D3: every initial customer engagement runs live on the Elite EXTRA scaffold while LMX OS **decides in parallel on the same orders**, and the two are compared until a scorecard passes and that engagement cuts over. Needs: a parallel decision path that records what LMX OS *would* have done without acting; per-order divergence capture (the session is explicit that aggregate metrics look fine while the two systems agree — the divergent orders are the entire point); and a nine-metric scorecard — drops per driver-hour, T1 on-time rate, batch rate, hold-release integrity, miles per drop, re-plan speed (<5s at real volume), human touches, decision divergence with outcome delta, and data completeness. Also a **sales asset**: "we transition only when our OS beats the baseline on your own orders." Open decisions for D3: the thresholds, the minimum consecutive passing weeks, and the weekly review owner. |

**Revisited — F9 vs. the operator-not-aggregator thesis:** a companion
competitive analysis run separately (`docs/LMX_OS_Roadmap_Addendum_Feature_Delta.docx`,
uploaded by Sourabh) explicitly lists "multi-carrier selection + carrier
network" under features LMX **deliberately does not build**, reasoning
that it's the aggregator model LMX rejected — "LMX is the fleet, not a
Bringg competitor." F9 as originally scoped (auto-routing overflow
orders to third-party gig couriers) was the same model in a narrower
form, and was first decided as **not building it** on that basis.

Sourabh revisited this same day: there's value in going this direction
while unit economics and assumptions get worked through, rather than
ruling it out permanently. The distinction that matters — this is about
LMX *sourcing capacity* from a gig marketplace while keeping its own
brand, SLA, and billing (the client never sees or chooses a carrier),
not about becoming a multi-carrier marketplace itself (a bigger,
separate question that would also reopen the "never sell LMX OS as
SaaS" decision, and would need Matan/Rich). On that narrower reading,
**reinstated as P7** in the Autonomy Partners section, generalized into
the same courier-abstraction work already planned for AV/drone/bot
partners, with three guardrails (data-flywheel tagging, a volume cap,
real pricing before building — see P7's row above) so a standing
cost-optimization option doesn't quietly become a bypass around
confronting LMX's own unit economics.

**Sequencing:** F1/F2 slot into Phase 6 (driver app hardening, alongside
A1/A5); F3 follows as a fast Phase 8 follow-up once F1 exists; F4/F5/F8
join C3/C4/C5 as Phase 8 follow-ups; F6 depends on E1 in Phase 4; F7
folds into Phase 10's I4; F10/F11 reinforce Phase 5; F12 is a later,
multi-hub-scale item. F13 (ratings/feedback) is low-effort and
no-dependency — a good Phase 8 follow-up alongside F3/F4. F14
(route-preview/shadow mode) fits Phase 9 (Hub 1 pilot) — it's the
trust-building surface for the pilot itself, not a pre-pilot gate.

**Deliberately not building (validated by the same companion analysis):**
checkout/delivery-slot self-scheduling (B2C retail surface — LMX orders
originate from distributor POS, matching the existing C5 "no self-serve
signup, by design" decision); a no-code automation/workflow builder and
a configurable driver toolbox (Bringg needs both because its customers
self-configure a shared platform; LMX runs one operation and the
Learning Loop already generates rules — configurability here would be
anti-differentiation, not a feature); and a manager mobile app (not a
gap that costs deals at hub scale — revisit post-Series A). Gig-fleet
capacity sourcing is **no longer on this list** — see P7 above; a true
multi-carrier marketplace (clients choosing between carriers) still is,
and would need a Matan/Rich conversation before it's reconsidered.

### Intelligence layer

Where LMX OS's "learning" actually stands, and the ladder to make it real
(cofounder conversation, July 2026). Honest baseline: the Dispatch
Optimizer optimizes but does not learn (every cycle solves from scratch);
the SLA engine and batch-hold queue are hand-written rules with
placeholder numbers; the one genuine seed is the Annotation & Learning
Loop — driver annotations (`stop_flags`, written from the driver app) ARE
labeled data, and the loop's shape (capture → detect → propose → human
approves → per-shop override) is right — but detection is frequency
counting (3+ repeated flags per shop → propose a fixed ±10min T2
hold-window change), it covers only hold windows, the learning influences
*when orders release* (indirectly shaping batches), never route
construction itself, and the human-approval step has no tool (I2).

**The governing constraint: this layer is data-gated, not code-gated.**
Every day Hub 1 runs before ground-truth capture (I1) exists is training
data lost forever — drive times we never recorded can't be backfilled.
Models can wait; instrumentation can't. I1 and I2 are therefore the only
urgent rows below, and both have zero external dependencies.

| # | Item | Why it matters |
|---|---|---|
| I1 | Ground-truth event capture | The prerequisite for every stage above it. Concretely: `Order.delivered_at` (real timestamp, replacing the `updated_at` proxy billing/portal use today); `Stop.arrived_at` (so time-at-stop = arrived→completed becomes measurable, per shop); per-leg actual drive time vs. the optimizer's implied estimate; offer decline/expiry reasons; hold-queue release timing (held→released delta per order, vs. its window). All buildable now with no external dependency. **Superseded in scope, July 2026:** the workflow session's 36-item training coverage matrix is a far richer specification of exactly this item — organized as urgency/tiering (4 situations), hold-and-batch (5), fleet dynamics (4), exceptions (8), communication (3), human-vs-system (4), edge cases and economics (6), and the learning loop itself (2). Build I1 against that matrix, not against the five fields listed here. Two things in the matrix matter disproportionately: **negative examples** (a batch declined because pairing would breach a window; an insertion rejected to protect a driver — "when not to batch is half the skill") and **paired counterfactuals** (the same shop visited before and after an access note exists; the same order decided by LMX OS and by the scaffold). |
| I8 | Manual capture of the non-default training situations | The session's own operating note: roughly a third of the 36 matrix situations **are not captured by default** — the paired access-note comparison, the interim dispatcher's gut-call log, the scaffold-era "where's my part" call tally, and the shadow divergence pairs all require someone deciding in week one that they are worth writing down, on a shared sheet if no app exists yet. Two of these are only capturable *during* the scaffold era and are gone forever after cutover: the call tally (C2) and the human dispatcher's tacit expertise, right and wrong (H1/H2). This is an ops checklist assignment, not a build — but it is on the critical path for I6 and belongs to whoever runs Hub 1 operations. |
| I2 | Rule review & promotion flow | The missing rung of the existing loop: `proposed_rules` accumulate nightly with nowhere to go — no endpoint or dashboard UI promotes them to `active_rules` (today it would be a manual SQL insert). Endpoint + a dashboard review card (proposal, evidence count, confidence, approve/dismiss) completes component 6's core loop. No external dependency. |
| I3 | Broaden the annotation vocabulary | Two flag types exist (`hold_window_too_short`/`_too_long`). Real per-shop knowledge drivers accumulate — parking difficulty, gate/access codes, shop prep slowness, receiving-dock quirks — should become structured flags too, so the labeled dataset covers more than hold timing. Coordinates with E6's naming sign-off; the schema (`stop_flags.flag_type` is a free string) already allows it. |
| I4 | Descriptive analytics on the captured truth | DPH per driver/hub/day, SLA hit rates by tier, hold-window effectiveness (release timing vs. driver flags), ETA vs. actual. First consumer of I1's data; feeds the E9 DPH validation. Needs a few weeks of pilot data to be meaningful, not to be built. |
| I5 | Calibration from data | Already tracked as E2/E5/E9/E10 — retune skip penalties, hold windows, the 2.5 DPH figure from real Hub 1 data instead of placeholders. The intelligence-layer framing just makes explicit that I1+I4 are what make these possible. |
| I6 | Predictive models | ETA prediction per leg/stop; per-shop order-volume forecasting (staffing/positioning); offer-acceptance likelihood (feed the optimizer a probability, not a hope); learned per-shop service times as optimizer inputs — this is where "the next route incorporates the learning" becomes literally true, closing the loop from annotations/ground truth into route construction. Gated on months of I1 data, not on code. |
| I7 | Ops copilot | LLM layer over the (by then rich) structured ops data: daily hub summaries, "why was yesterday slow?", anomaly explanations, natural-language queries over the dashboard. Unlike I6 it needs no training data — just good structured data (I1/I4) and an LLM API key (the one external dependency in this table). |

### Testing / process

| # | Item | Why it matters |
|---|---|---|
| ~~T1~~ | ~~Load/performance test against the design doc's <5s-cycle/20-driver/100-order budget~~ | **Done** — `tests/integration/test_optimizer_load.py`, real Postgres Order/Driver rows (not just Redis/hold-queue data) so the writeback step does the same real work a live cycle would. Measured: 20 drivers/100 orders completes in **~0.08s** (~65x margin under the 5s budget); a 5x stress probe (100 drivers/500 orders, not a contractual target) completes in **~0.24s**. This tests the stub nearest-neighbor engine only - Google Route Optimization's own API latency (E1) is a separate, unmeasured external dependency without live credentials. |
| T2 | Local dev/test sandbox can't fully exercise Redis-backed rate limiting (driver OTP issuance, and now client login) | The bundled test Redis (`redislite`/the sandbox's standalone binary, both v6.2.14) doesn't support `EXPIRE...NX`; production Redis (7-alpine) does. Confirmed not a real bug, but worth a note so it doesn't get "rediscovered" and mistaken for one - now affects `app/client_auth/login_rate_limit.py`'s tests too, same root cause. |

---

## Part 2 — Phased plan to Hub 1

Phases 1–3 (driver app: core delivery loop, profile, earnings/messaging)
and the Phase 1 core backend + internal dashboard are done. What follows
is the path from there to a real, running Hub 1.

**These phases are not strictly sequential.** Once there's more than one
engineer (B1), 4/5/6/7 can mostly run in parallel — they touch different
parts of the system. Phase 8 (client dashboard, Hot Shot tier, tiered
billing, shop SMS, minimal client onboarding) and part of Phase 6 (A0's
screen redesign/offline queue/biometric auth/live push) and part of Phase
7 (W2 payroll's engineering) have already shipped, ahead of the
sequencing below — Sourabh's calls, since none of these had committed
dates constraining the build order.

### Phase 3.5 — The Elite EXTRA scaffold (NEW, July 2026)
**Goal:** Hub 1 generates revenue on a scaffold while LMX OS is still
being built. This phase did not exist in earlier versions of this plan,
which assumed LMX OS ran Hub 1 from day one.

The scaffold, per the workflow session: **Elite EXTRA** as the operating
platform, LMX-owned vans, LMX W-2 drivers (recruited per signed customer,
hiring qualified incumbents from that customer's crew where possible),
co-located at the customer's warehouse, with human dispatch judgment in
place of the SLA engine and batch-hold queue.

- **The integration that does not exist yet:** "Elite EXTRA" appears
  nowhere in the codebase. At minimum LMX OS needs to read the same
  order stream and the same delivery outcomes the scaffold sees, or the
  shadow comparison (W9) has nothing to compare against. Scope this
  before committing to a cutover date.
- **Interim ingestion latency (session decision D6):** Epicor drops a
  file every 15 minutes into Elite EXTRA. Worst case an order sits 15
  minutes before anyone sees it — against a 45-minute T1 promise, that
  is a third of the window gone before dispatch. Three options on the
  table: tighten the drop interval, narrow the interim T1 promise, or
  accept the risk. **Unresolved — needs a decision.**
- W8 (Epicor staging-module qualification) gates which prospects this
  scaffold can even work for.
- I8's scaffold-era-only captures (the "where's my part" call tally, the
  human dispatcher's gut-call log) can **only** be collected during this
  phase. After cutover they are gone permanently.
- **Deliberately not built:** hold/batch logic inside Elite EXTRA. The
  session is explicit that paying Elite EXTRA to build hold logic would
  hand LMX's core differentiator to a competitor. The scaffold dispatches
  as fast as possible and lives with ~1.75 drops/driver-hour; the batching
  advantage arrives with LMX OS, not before.

**Exit criteria:** Hub 1 delivering real orders on the scaffold, with
enough instrumentation that Phase 9's shadow comparison has a baseline
to measure against.

### Phase 4 — Make the placeholders real
**Goal:** every "unverified" or "reconstructed from a summary" caveat in
`docs/ARCHITECTURE.md` gets closed before real orders run through it.

- E1, E2 (Google Route Optimization — provision a service account, run a
  real call, tune skip penalties)
- E3 (Epicor payload — needs B2)
- E4, E5 (batch-hold logic + SLA windows — needs B3)
- E6, E7 (Learning Loop naming sign-off + real scheduler)

**Exit criteria:** no remaining "not yet verified against a live X" line
in `docs/ARCHITECTURE.md`'s core-backend sections.

### Phase 5 — Security & production infrastructure
**Goal:** safe to run with real orders, real drivers, and eventually real
money — not just correct in a demo.

- S1–S7 (real auth, secrets management, hosting decision, observability,
  rate limiting, security review, Twilio webhook signing)
- E8 (event bus — only urgent once this needs to run as more than one
  instance, which a real hosting decision (S3) will likely force)

**Exit criteria:** a documented production runbook and a completed
security review.

### Phase 6 — Driver app hardening
**Goal:** something a real driver can rely on for a full shift without
developer tooling.

A0 (screen consolidation, flag-an-issue, offline write queue, device-bound
biometric auth, live route-change push) already shipped ahead of this
phase's sequencing — Sourabh's call, following a separate wireframe spec
discovered mid-build (`docs/NEXT_STEPS.md` item 19). What's left:

- A1 (push notifications — do this first; everything else in this phase
  is polish by comparison)
- A2–A5 (camera/barcode, photo/signature capture, PIN system, maps SDK)
- A6 (app store deployment — start with TestFlight/Play internal testing,
  not a public release, for the first pilot; also the only way to
  visually verify A0/D3's native icon/splash/adaptive-icon work)
- A7, A8 (masked voice calling, harden SMS reply matching)

**Exit criteria:** a driver can install this from an internal beta channel
and complete a full day's routes without needing you or dev tooling.

### Phase 7 — Payroll & worker classification
**Goal:** earnings becomes a real number, not an estimate — phased across
three worker classifications per Sourabh's stated sequencing: W2 employees
first (paid monthly), then 1099 contractors (paid weekly), then gig
per-delivery workers.

**W2 (Phase 1 of this rollout) — mostly done:** `Driver.employment_type`/
`hourly_rate_cents`, a durable `driver_shift_events` log, real on-duty
hours replacing the old route-span heuristic, federal 40hr/week overtime,
a monthly pay period, and the `PayrollProvider` interface (Rippling
chosen — see B4) are all built and tested (`docs/NEXT_STEPS.md` item 22).
What's left is B4 (a real Rippling account) and any state-specific
daily-OT rules beyond the federal baseline — a business/legal decision,
not more reverse-engineering from code.

**1099 (Phase 2) — not started:** A10 (the worker-autonomy/misclassification
question needs a legal answer before this ships) plus W-9 collection
(likely inside Rippling's own onboarding, same as W2's I-9/W-4, not a new
screen in this app).

**Gig (Phase 3) — not started:** A11 (a real per-delivery fare model,
priced offers, instant payout, self-serve onboarding, per-trip identity
re-verification) — the largest of the three, closer to a second product
line sharing this backend than a config change.

### Phase 8 — Client dashboard, Hot Shot tier, tiered billing, shop SMS — ✅ DONE
**Goal:** make LMX successful with the first client — a full client
portal (not a placeholder), a premium priority delivery tier, per-tier
billing, and automatic shop notifications, all as MVP requirements rather
than deferred to a later phase.

Shipped:
- **HOT_SHOT tier** — a fourth SLA tier, ahead of T1 in urgency, added to
  the `sla_tier` Postgres enum (migration `0007`). Classified from a
  payload flag (`app/sla/engine.py`), bypasses the batch-hold queue's
  cluster-mate wait entirely (`app/batch_queue/queue.py`), is prioritized
  in both the optimizer stub and the Google Route Optimization skip
  penalties (`app/optimizer/google_routes_client.py`), and — critically —
  is never commingled into a shared pickup stop with another order, even
  from the same shop (`app/api/driver_routes.py`'s `accept_offer`).
- **Tiered client billing** — a `client_rates` table (per client, per
  tier, `$/drop`), computed into `Order.fee_cents` at ingestion time
  (`app/ingestion/service.py`). Null (not zero) when no rate is
  configured, so a billing gap can never silently look like a free
  delivery.
- **Client portal** (`client-portal/`) — a separate Vite/React/TS/
  Tailwind app from the internal `dashboard/`, with its own real
  password-based JWT auth (`app/client_auth/`, one login per client
  company). Shows order history, status, and per-order fee; billing
  beyond that is intentionally minimal pending C3.
- **Minimal client onboarding** — `POST /admin/clients`
  (`app/api/admin_routes.py`) creates a client, its first shop, its
  per-tier rates, and its portal login in one action; also has a form in
  the internal dashboard (`OnboardClientForm.tsx`).
- **Shop SMS** — one-way, automatic notifications to a shop's phone at
  pickup and at "driver en route," with Hot-Shot-specific copy for both,
  reusing the existing Message/SmsClient infrastructure
  (`app/messaging/shop_notifications.py`).

**Follow-ups this phase surfaced, not yet done:** E10, C3, C4, C5 (S8 and S9
are now both done — see Part 1's tables above).

### Phase 9 — Shadow, prove, cut over
**Goal:** earn the cutover from the scaffold to LMX OS on the customer's
own orders. **Rewritten July 2026** — this phase previously read "Hub 1
pilot: run real orders through the full pipeline," which assumed LMX OS
ran Hub 1 directly. Under the scaffold model, Hub 1 is already live and
generating revenue on Elite EXTRA (Phase 3.5); what this phase proves is
that LMX OS should replace it.

**Gates:**
- B2 (signed customer #1 — still the actual gate for everything)
- **R1, R2, R3 — the other gate, and the one most likely to be missed.**
  Insurance/liability coverage, driver background checks, and a
  privacy/data-retention policy all need to be *in place* before real
  drivers carry real packages containing real customer data — not started
  when launch is imminent. The workflow session independently reached the
  same conclusion (decision D4: fleet insurance and workers-comp lead
  times "now gate launch"). All three are slow, none are engineering
  work, and they need Rich/Matan moving in parallel with Phases 4–8.
- W7 (training-data rights in the contract) — the shadow comparison
  generates training data on a customer's orders at their customers'
  doors. Those rights belong in the contract before the first delivery.

**The work:**
- W9 (shadow-mode comparison engine + the nine-metric scorecard) — this
  is the phase's centerpiece, not a side feature
- F14 (route preview/override for the orchestrator during shadow running)
- E9 (validate or replace the 2.5 DPH figure — now measurable as
  *shadow-planned DPH vs. scaffold actual on identical orders*, which is
  a far stronger proof than a standalone pilot number)
- T1 (load-test against realistic volume before it's live volume — the
  scorecard's re-plan-speed metric asserts <5s at actual hub volume)
- I8's scaffold-era-only captures, before the window closes

**Cutover decision (session D3):** per customer engagement, not once
globally. Agree the thresholds, the minimum consecutive passing weeks,
and the weekly review owner — all three are still open.

**Exit criteria:** for the first engagement, a scorecard passing for the
agreed number of consecutive weeks on that customer's real orders, with
the divergent-order analysis (not just the aggregates) reviewed and
signed off — and insurance, background checks, and a privacy policy
(R1–R3) demonstrably in place before day one, not retrofitted after.

**Also worth running as its own goal (session D7):** "excellent on the
scaffold" defined in numbers — on-time rate, drops per hour, exception
rate — reviewed in the same weekly session as the shadow scorecard. The
scaffold funds the end-state; it shouldn't be treated as a holding
pattern.

### Phase 10 — Intelligence layer
**Goal:** the system gets measurably better at its job the longer it
runs — the annotations-and-ground-truth flywheel the design doc's
component 6 gestures at, made real. See Part 1's "Intelligence layer"
table for the item-by-item detail.

Sequencing relative to the pilot — this is the important part:
- **Before Phase 9 goes live:** I1 (ground-truth capture) and I2 (rule
  review/promotion flow). I1 because pilot days without instrumentation
  are training data lost forever; I2 because the pilot will generate
  driver annotations from day one, and proposals with no approval tool
  just pile up. I3 (broader annotation vocabulary) is strongly
  preferred pre-pilot too — the flags drivers can't write are the
  labels we won't have.
- **During/after the pilot:** I4 (descriptive analytics) as soon as
  there's a couple of weeks of data; I5 (calibration — E2/E5/E9/E10)
  is the pilot's whole point. F7 (client- and ops-facing analytics
  dashboards, from the competitive analysis) folds into I4's build —
  every competitor exposes a client-facing cut of this that I4 didn't
  originally scope; add it while I4 is being built, not as a separate
  pass. W4 (the driver-visible scorecard) folds in here too — same
  computation, shown to the driver, which the workflow session frames as
  a trust decision rather than a feature.
- **Later, data-gated:** I6 (predictive models feeding the optimizer —
  where learning finally reaches route construction itself) and I7
  (ops copilot; the one row needing an external decision, an LLM
  provider/API key).

**Exit criteria (long-horizon):** at least one learned quantity (per-shop
service time or hold window) flows into dispatch decisions automatically
from data rather than from a hand-set placeholder, with a human approval
gate on every rule change.

### Phase 11 — Autonomy partners
**Goal:** deliveries carried by autonomous vehicles (AV cars, sidewalk
bots, drones) — and, per P7, gig-courier human capacity — through their
operators, dispatched by the same loop that dispatches human drivers.
See Part 1's "Autonomy partners" table (P1–P7) for the item-by-item
detail; the architectural decision (adapter layer, not a separate app)
is recorded there.

Two independent tracks in this phase now, gated differently:

**Track A — AV/drone/sidewalk-bot partners (P1, P2, P4–P6):**
- **Gate:** a signed autonomy partner with API access — this track is a
  business-development outcome first. Until then the only active
  obligation is the design constraint: touch fleet/offer models in a
  courier-shaped way (P1's abstraction), not a human-driver-shaped way.
- **One exception to that gate, added July 2026:** the workflow session
  puts **autonomy-eligibility scoring on every delivery from van one**
  (Stage 9; training case X6 — weight, dimensions, corridor, and an
  eligible/ineligible label captured per drop). That is data capture,
  not integration, and it starts at Hub 1 launch rather than waiting on
  a signed partner. It builds the addressability dataset that makes the
  first partner conversation quantitative instead of speculative — so
  fold the fields into I1's capture work, not into this phase.
- **First buildable slice once a partner signs:** P1 + P2 with the stub
  partner, proving the offer→accept→status loop end-to-end before any
  real vehicle moves; then P3's eligibility filtering (a drone that gets
  offered a 40lb pallet is a bug, not a learning).
- **P4 (unmanned handoff/PIN)** is shared with the driver app's A4 —
  building A4 earlier quietly de-risks this phase.
- **Later:** P5 settlement, P6 portal.

**Track B — Gig-courier cost-optimized dispatch (P7, reinstated F9):**
- **Gate:** unit economics and real gig-marketplace pricing data — not a
  signed partner. This can move independently of, and likely faster
  than, Track A.
- **Sourabh's call (July 2026):** worth pursuing as a standing
  cost-optimization option, not just an SLA-emergency valve, while the
  economics get worked through — reversing a same-day earlier decision
  not to build it at all. See the "Competitive feature gaps" section's
  F9 history for the full reasoning on both sides.
- **Before writing real dispatch logic:** P7's three guardrails (data
  tagging so I1/I4 don't treat these as LMX-optimizer ground truth, a
  volume cap per hub, and real pricing data rather than guessed rates)
  are not optional polish — they're what keeps this from quietly
  becoming a bypass around confronting LMX's own unit economics.
- **Shares P3's cost-per-drop mode-selection logic and P5's settlement
  machinery** with Track A — one dispatch decision, one settlement
  system, two kinds of capacity provider.

**Exit criteria (long-horizon), Track A:** one real order, ingested from
a real client POS, delivered by a partner vehicle with no LMX human in
the loop — dispatched, tracked, PoD'd, and settled through the same
pipeline as every human-driven delivery that day.

**Exit criteria, Track B:** the optimizer routes a real order to a gig
courier because it was the genuinely cheaper eligible option under real
pricing data — not a guess — with the order correctly tagged so it never
contaminates I1/I4's ground-truth analytics, and the hub's volume cap
holding.

---

## A note on sequencing

This order is a recommendation, not a fixed plan — it optimizes for "de-risk
what's already built before adding more," but the actual constraint is
almost always headcount (B1). Worth revisiting once that hire is in place,
since a team of two can run Phases 4–7 in parallel in a way one person
can't.
