# LMX OS — Full-System Roadmap

Two things in one document: (1) every open item across the whole system —
not just the driver app — in one place, and (2) a phased plan to take LMX
OS from "code that works in a demo" to "running Hub 1 for real."

This supersedes the "Recommended next steps" list at the bottom of
`docs/ARCHITECTURE.md` (still there for historical context) and sits above
`docs/NEXT_STEPS.md`'s row-by-row punch list — that file is the detailed
backlog; this one is the map of how those rows fit into getting to launch.

## Decision log — founders offsite, August 3 2026

**A second demand path.** The offsite adopted gig-platform demand sourcing
(Curri, Dispatch, Roadie) as a way to get real paid order flow — and real
training data — without waiting on a signed distributor. Source:
`LMX Gig-Platform Demand Sourcing & LMXOS Training Strategy`, plus a
two-week live pilot on Dispatch (Austin, 7/22–8/1/26, Rich: 23 commercial
jobs, $578.86, $25.17/job, $1.75/mi, $70.74/hr engaged).

| Item | Decision |
|---|---|
| **Demand source** | **Gig platforms become a parallel demand path**, not a replacement for the distributor thesis. LMX drivers hold individual accounts on Curri, Dispatch and Roadie; accepted jobs are relayed into LMX OS, which sequences the day. Tracked as the **G-items** below. **Open cofounder question: does G replace the distributor path, run beside it, or bridge to it?** Nothing below assumes an answer. |
| **Onboarding track** | **Both tracks are legal** (Sourabh, Aug 2026) — the gig-driver track (individual, no DOT) and the carrier track (LMX as FMCSA-authorised motor carrier). Choose on merit, not legality. The gig track starts immediately; the carrier track additionally permits cross-driver pooling, likely yields a real API, and removes dual completion. |
| **Assignment scope** | **A per-job property, not a system mode.** Both tracks will run simultaneously during any migration, so a job carries its own scope: gig-sourced → pinned to the accepting driver (`allowedVehicleIndices`); carrier-sourced → assignable to any driver. One optimizer run handles both. Getting this wrong means a rewrite. |
| **Driver fleet hardware** | **Android only.** iOS has no notification-listener API, so zero-touch intake is impossible there. Cheap to standardise at 3 drivers, expensive at 30. Device choice must include "notification reliability under aggressive battery management" — the pilot phone is a Samsung, the worst offender. |
| **Personal service (§4.2.3)** | Expressed in the optimizer as a per-job constraint rather than treated as a blocker, so the design honours it on the gig track without depending on either legal reading. **Additionally: a collected parcel is a hard pin on any track** — reassignment after pickup needs a physical handoff, so pooling only buys anything between accept and pickup. |
| **Intake automation** | **Deferred deliberately.** At 3 drivers (~6–12 offers/day) manual entry costs minutes a day, while G1+G2 are 9–16 days of the highest-risk work in the section. Automated intake is a 30-driver problem. Test the notification payload now; build it later. |

## Decision log — LMX Link kickoff, August 6-7 2026

The order-intake track, planned in `docs/LMX_LINK_PLAN.md` and now partly built.
Two decisions here **reverse earlier ones**, and both reversals are recorded in
full rather than quietly applied.

| Item | Decision |
|---|---|
| **Naming** | The intake track is **LMX Link**, internal only. "LMX Lite" is retired: "Lite" reads as a software tier, implies a Pro version, and invites a conversation about seats and fees that contradicts LMX being an operator. To a customer it has no name — it is *"how you send us orders"*. |
| **Clients sign up** | **REVERSES §2.2 principle 1 ("no account creation") and C5.** Clients apply on a public URL and get real portal accounts. The B2B posture C5 protected is preserved by an approval gate rather than by having no form. |
| **Ordering lives in the portal** | Not a separate magic-link surface. Removes magic-link auth, a second front-end app, the anonymous-identity problem and a billing view outside the portal, and reuses C3 and C4 wholesale. |
| **Ad-hoc pickup creates a Shop** | A typed address is geocoded, deduped on its normalized form and remembered as a `Shop`. Pickup location is Shop-dependent through four layers and the last of them renders a shopless pickup at **0.0, 0.0** — a driver's stop in the Gulf of Guinea, silently. Creating the Shop leaves clustering, the optimizer, pickup grouping and the HOT_SHOT commingling guarantee untouched; threading coordinates onto `Order` instead would have risked the one guarantee we sell. It also isn't a workaround: §2.2 principle 3 already requires remembering every shop. |
| **Geocoding is real, not a stub** | The only external dependency here with **no usable degraded mode** — a stub either fails (nothing routes) or invents coordinates (a driver goes to a fictional address). Nominatim, chosen because it needs no account, so "unconfigured" cannot happen. A **pilot decision**: 1 req/sec, no commercial bulk use. Real volume forces a keyed provider. |
| **Approval sets rates** | The only moment someone is already deciding commercial terms for that client. Means an *active* client always has rates, so `Order.fee_cents` is never null for them, and the "default rate card" workstream is not needed at all. |
| **Delivery time is an estimate, not a promise** | The confirmation shows a real `collect_by` from the spec-verified SLA windows and an `estimated_delivery_by` from straight-line distance at a placeholder speed. Named differently on purpose. **Do not promote it to a promise before E1 makes a live routing call.** |
| **Show the estimate anyway (Sourabh, Aug 2026)** | Asked explicitly whether to hide the delivery estimate until routing is verified. **Decision: show it — the trust it buys is worth more than the precision it lacks.** The mitigation is the labelling, not omission: it reads "estimated delivery around…", is set quieter than the collection commitment, and returns null rather than guessing when the drop has no coordinates. Revisit the wording, not the decision, once E1 lands and the number becomes defensible. |

### LMX Link — status

Seven steps built and merged (`3c796dd`..`182ef56`). `tests/integration/test_lmx_link_end_to_end.py`
walks signup → approval → order → dispatch → collection → delivery and asserts
the client sees each transition.

| # | Item | Status |
|---|---|---|
| ~~L1~~ | ~~LMX Order Object v1, status state machine, model~~ | **Done** — `app/schemas/lmx_order.py`, `app/orders/state_machine.py`, migration `0028`. The machine is **additive**: `delivery_failed` and `returned` already carried §1.4's `EXCEPTION_RAISED`/`RETURNED_TO_HUB` meanings, so four values are genuinely new and they promote stop-level progress onto the order. `sla_owner` (LMX \| EXTERNAL) is the field that lets all three demand paths coexist. |
| ~~L2~~ | ~~Geocoding + address cache~~ | **Done** — `app/geocoding/`, migration `0029`. Cache-first, failures cached too, verified against the live service. The only third-party client in this repo that has actually made a real call. |
| ~~L3~~ | ~~Ad-hoc pickup, one ingestion path~~ | **Done** — `ingest_order` now maps into the contract and delegates to `ingest_lmx_order`, so there is a single persistence route. All pre-existing tests passed unmodified through it, which is the evidence it is behaviour-preserving. |
| ~~L4~~ | ~~Public signup + approval gate~~ | **Done** — see C5 above. |
| ~~L5~~ | ~~Client order submission~~ | **Done** — `POST /client/orders`, gated on `signup_status == 'active'`. The deadline choice becomes an urgency flag the existing SLA engine reads, rather than letting a client name a tier: they state urgency, LMX decides what it means (§1.3). |
| ~~L6~~ | ~~Signup page + order form~~ | **Done** — `/signup` is the URL to share or embed. `entry_seconds` is logged with every order so §3.4's sub-60-second target is measured from real counter use, not asserted. |
| ~~L7~~ | ~~Status write-back~~ | **Done** — `app/orders/sinks.py`, `app/orders/status_service.py`. §1.4: *"a carrier that takes orders and goes quiet is not a carrier — it is a favour."* |
| L8 | Publish the terms | **Still blocking the signup page, but the engineering half is done and the blocker is now enforced rather than noted (August 2026).** `app/legal/content/terms.md` and `privacy.md` are the served copies, versioned in their own front matter, rendered by the portal at `/terms` and `/privacy`, and linked from the checkbox — so an applicant can read what they are accepting. `status: draft` makes `POST /public/signup` return **503**: it will not record assent to an unapproved document, which was previously prevented only by a comment. Three defects went with it, all of which made the acceptance record worthless in a dispute: **the version was client-supplied** (`SignupPage.tsx` held `TERMS_VERSION` and sent it up, so the only evidence of what was agreed was written by the applicant's browser — now the server writes the document's own value); **nothing checked it was current** (a form open across a terms change recorded assent to unseen text — now a 409); and **nothing checked a document existed at all**. `settings.allow_unpublished_terms` is a demo escape hatch, off by default, logging a warning on every signup it lets through. What remains is genuinely legal: `R1` (four liability numbers), `W7` (clause 8), retention decisions, sub-processor names, governing law. `docs/LEGAL_BRIEF.md` is the counsel memo and the publish checklist. |
| ~~L9~~ | ~~Bulk paste~~ | **Done, August 2026** (§2.2 principle 5) — `POST /client/orders/batch` plus `client-portal/`'s `BulkPastePanel`. **Deliberately not all-or-nothing:** each row is ingested and reported independently, so one unfindable address among six does not discard the five that were fine, and the failed lines are left in the textarea so "fix it and resend" is pressing send again. The CSV adapter's "never silently drop a row" rule, applied a step earlier. Rows share a pickup and deadline, which is where the speed comes from. The parser splits on TAB first and only falls back to comma when there is no tab, because addresses contain commas and splitting those would shred them. Capped at 25 rows: every genuinely new address costs a geocoder call at one per second, so bulk paste is the first feature to make that ceiling bite — a concrete argument for L12. |
| ~~L10~~ | ~~A sink that reaches a customer~~ | **Done, August 2026** — via F4's outbound webhook (`app/webhooks/sink.py`), which is exactly the shape this row anticipated: it plugs into `app/orders/sinks.py` rather than growing a second notification path. The client portal still reads status straight from Postgres, which remains right while it is a surface *of* this application rather than a consumer *of* it. |
| ~~L13~~ | ~~Transactional email~~ | **Done, August 2026** — `app/messaging/email_client.py` + `client_emails.py`. **This was the largest functional hole in LMX Link and nothing on the roadmap had named it:** the signup page promised "we'll be in touch" and nothing was, approval flipped a login active with nobody told, and an activated account nobody knows about is the same as no account. Two flows now send — application received, and approved-with-a-sign-in-link. SMTP rather than a vendor API deliberately, so the provider stays a config decision (none chosen yet); stdlib `smtplib` in a thread rather than a new dependency, since volume is a handful a day and every send is off the hot path. Unconfigured → stub, same as Twilio. **A duplicate signup deliberately sends nothing** — mailing an address's real owner "we got your signup" would both alarm them and confirm registration to whoever submitted it. Sends are best-effort *and* guarded: an approval stands even if the mailer raises, which a test found because the first version trusted `send`'s never-raise contract absolutely. |
| ~~L14~~ | ~~Self-serve password reset~~ | **Done, August 2026** — `app/client_auth/password_reset.py`, `POST /public/password-reset/{request,confirm}`, plus a forgot-password flow on the portal login and a `/reset-password` page. Closes an outage, not a support gap: a company with one admin — which is every company on its first day — was previously locked out until someone ran a script by hand. Four deliberate properties: **tokens stored hashed** so a Redis dump yields nothing usable; **single-use via GETDEL** so a forwarded or proxy-logged link can't be replayed; **enumeration-safe** — unknown address, pending applicant, deactivated user and real reset are indistinguishable from outside, including when the per-email throttle trips (a 429 there would itself signal existence); and **`is_active` rechecked at redemption**, since the token outlives the state it was issued against. A reset also clears the login lockout, otherwise a correct new password still bounces. **Known limitation, not fixed:** portal sessions are stateless JWTs with no denylist, so a reset does not invalidate sessions already issued — pre-existing (an admin-initiated reset has the same hole) and closing it needs a token denylist or per-user token version. |
| ~~L15~~ | ~~Proxy-aware rate limiting~~ | **Done, August 2026** — `app/client_ip.py`, used by all three limiters (general middleware, signup, password-reset request), plus `TRUSTED_PROXY_COUNT=1` wired into `infra/aws/ecs.tf` so the fix isn't inert once deployed. **The naive version of this fix is worse than the bug it fixes:** `X-Forwarded-For` is caller-controlled, so taking the leftmost entry lets an attacker mint a fresh empty bucket per request — no limit at all, dressed as one. Each proxy *appends* the peer it saw, so with N trusted proxies the real caller is the entry N from the right and everything left of it is worthless. Defaults to **0 = trust nothing, use the TCP peer**, so behaviour is unchanged until someone declares the proxy count; over-declaring is the dangerous direction and under-declaring merely over-throttles a shared bucket. Every ambiguous case — missing header, chain shorter than declared, non-address junk that would otherwise become a Redis key — falls back to the peer. 16 tests hold that asymmetry in place. |
| ~~L11~~ | ~~`en_route_drop` emission~~ | **Done, August 2026** — `app/delivery/en_route.py`, wired into `accept_offer`, `complete_stop` and `flag_stop_issue`. **Two** states were declared and never reached, not one: `OrderStatus.en_route_drop` was in the enum and the transition map, and `Stop.status` has documented `pending | en_route | arrived | completed | failed` since the model was written with nothing ever writing `en_route`. So a client's order went `PICKED_UP → DELIVERED`, and F3's tracking page had to derive "your driver is on the way" from stop rows because the status couldn't be trusted to say it. **The signal is the stop sequence**, not pickup completion: a route's current stop is its earliest non-terminal one, so when that becomes a given dropoff every prior stop is finished and the driver genuinely is heading there. This row rejected the pickup-completion stamp as meaningless and on a multi-stop route it is worse — a driver who collects four orders and drives to the first customer is not en route to the fourth, and marking all four would tell three clients their driver is inbound while he is half an hour away. **Deliberately not gated on live position** even though F1 makes it possible: a driver whose app hasn't pinged would then never leave `picked_up`, and precision that fails closed on a missing GPS fix is the wrong trade for a client-facing field. **A dead state is not inert — filling it broke four guards.** `scan_parcels`, `scan_parcel`, `collect_return` and `complete_stop` all spelled "has this driver arrived" as `status == "pending"`, correct only while `pending` was the sole pre-arrival state; once a stop could be `en_route`, a driver merely driving toward one could scan its parcels and complete it from anywhere. Replaced with a single `_assert_arrived` helper that checks for arrival rather than against one alternative. 11 tests. |
| ~~L12~~ | ~~Keyed geocoding provider~~ | **Done, August 2026** — `app/geocoding/google.py`, selected by `GEOCODER_PROVIDER=google` with `GOOGLE_MAPS_API_KEY`. Newly unblocked by the GCP project created for the Cloud Run deployment. No throttle, which removes the 1-req/sec ceiling that bulk paste (L9) made bite. Refuses to start rather than falling back to Nominatim when the key is missing — a silent fallback would reintroduce both the licensing problem and the ceiling the setting exists to escape. **The substance of this item turned out to be a latent bug it exposed:** the address cache remembered ANY failed lookup and never retried it, so one exhausted quota or expired key would have permanently marked every address attempted during that window as unresolvable, with every future order to them refused and nothing to explain why. Google returns HTTP 200 for quota and auth failures, which made that near-certain. Now `None` means "asked, and this address is not real" (cached) and `GeocoderUnavailableError` means "could not ask" (never cached, always retried); Nominatim was updated to honour the same contract. |
| ~~L16~~ | ~~Inbound API-key auth for external order submission (LMX Link T5's other half)~~ | **Done, August 2026** — `POST /api/v1/orders`, `GET /api/v1/orders/{your_ref}`, per-client keys in the portal's Integrations tab, and `docs/ORDER_API.md`. **The gap:** `/ingestion/{hub}/{client}/{source}` calls itself *"the webhook target you'd register with a client's POS"*, but sits behind `OpsUserAuthMiddleware` — so that was only true if you handed the POS an LMX **ops** login, which can also run dispatch cycles, read the fleet and reach `/admin`. No credential existed meaning "may submit orders for exactly one client". With F4's callbacks already shipped, T5's exit criterion (*"an external system POSTs an order and receives status callbacks without LMX assistance"*) is now met. **A new prefix rather than relaxing that endpoint's auth, deliberately:** it takes `hub_id` and `client_id` as *path parameters*, so exempting it would have let one client's key submit orders billed to and delivered for another. Here the client comes from the key and the hub from the client — the request has nowhere to name either. Keys are stored as SHA-256 hashes (unlike the outbound webhook secret, which we must *sign* with and therefore need in the clear — the asymmetry is the point), with a display prefix so rotation is safe, `last_used_at` so a client can tell which of two keys their system actually uses, and a `lmxk_live_` prefix so secret scanners can flag a leaked key. Rate-limited per key rather than per IP: an integration runs from one address, several clients can share a NAT, and one client's runaway job must not spend another's capacity. **Idempotent on the caller's own reference** — a POST that times out is unknowable to the caller, and a duplicate here is a second van to a real address billed twice. 25 tests. **Found on the way:** `uq_orders_source_ref` (from 0028) was unique on `(source_system, source_order_ref)` with no client scope, so two clients using the same internal order number collided in the database — latent while every adapter was a per-tenant connector, certain once every API client shares `source_system='client_api'`. Fixed in migration 0035 by scoping it to the client, which is what 0028 meant. |
| ~~L17~~ | ~~LMX Link scorecard: §3.4's success metrics, actually measured~~ | **Done, August 2026** — `app/reporting/lmx_link.py`, `GET /lmx-link/scorecard` (ops-authed). §3.4 names five success metrics and **not one of them was answerable**, which is the ordinary fate of a metrics table in a plan: the targets get quoted in updates and nobody can say whether they are being hit. Three are now computed from durable rows — approval-to-first-delivery (the plan calls this "the entire point of LMX Link"), order entry time second-order-onward, and status write-back latency. **Two facts had to start being recorded first.** `entry_seconds` was *sent* by the portal since L6 and *logged*, but never stored — and a log line is not a dataset when the target is a distribution. `clients.approved_at` did not exist at all: approval flipped `signup_status` and nothing else, so the headline metric had no start point. Both added in migration 0036, the second with no backfill because the moment genuinely isn't recoverable and estimating from `updated_at` would produce a number that looks like data. **Entry time deliberately excludes each client's FIRST order** — that one also creates their pickup shop and teaches them the form, so counting it measures onboarding and would make the metric look worse the more new clients we win. Machine paths (Epicor, the client API) are excluded too, or the number would appear to improve every time API volume grew. Percentiles rather than means, since an average is dominated by the one order left open over lunch. **The other two metrics report as not measured, with the reason, rather than being dropped or filled with a plausible zero:** manual-correction rate needs an explicit ops signal (a heuristic over `updated_at` would count every status change and be worse than nothing), and adapter/core coupling is a property of the code answerable from a diff. 15 tests, most of them about that honesty rather than the arithmetic. |
| ~~L18~~ | ~~Driver-app POD configurability~~ | **Done, August 2026** — `app/delivery/proof.py`, enforced in `complete_stop`, and every stop now carries its proof requirement to the app. `ProofRequirements` has been on the order object since L1 and written to `orders.proof_requirements` at ingestion since L3, **and read by nothing** — so the contract advertised configurable proof while the endpoint enforced a constant. **And the constant was "none":** `method="photo"` with a null `photo_url` completed the stop, and nine tests in this repo did exactly that. Proof of delivery proved nothing while the order recorded that we knew better. Three rules: the chosen method must carry evidence; a photo count above one is mandatory whatever the method (a signature cannot stand in for "four photos of named subjects"); a signature requirement is additional and a verified PIN satisfies it — both answer "the right person received this" and the PIN is the stronger, being checked against what we issued (A4). **A correction worth recording:** the first cut treated the default `photo_count_required=1` as an absolute floor, which made every PIN delivery also demand a photo and broke A4's flow outright — the app's model has always been "pick one of photo/signature/PIN" and the defaults are documented as matching it, so the count means *one photo if a photo is the proof*. A commingled stop takes the UNION of its orders' requirements: taking the laxest would mean one client's signature requirement disappearing because someone else's order shared the van. Requirements travel with each stop rather than surfacing on rejection — a driver who learns at the door that this client wanted four photos has already put the box down. `CompleteStopBody` gained `photo_urls` (legacy `photo_url` still folded in, so an older app build keeps working) and migration 0037 adds `stops.pod_photo_urls`, because insisting on four photos while storing one would leave us unable to produce the evidence we demanded. 15 tests. |
| ~~L19~~ | ~~CSV manifest adapter (LMX Link T3)~~ | **Done, August 2026** — `app/ingestion/manifest.py`, `POST /client/orders/manifest`, and an "Upload a file" mode in the portal's order entry. T3's exit criterion (*"a 40-row manifest imports, with bad rows reported and good rows dispatched"*) is a test. L9's bulk paste covered pasting; this is the file, which is what a distributor actually has once the count gets past a handful. **Parses to the same rows the paste path takes and calls the same function** — §1.1 says a new adapter must not need a new way for orders to be created, and two ingestion paths would drift on exactly the things that matter (SLA classification, shop memory, the hold queue). **The parsing is the product:** headers are matched generously (`Ship To Address`, `Customer Address`, `DESTINATION`), semicolon and tab files are read, cp1252 and a UTF-8 BOM are handled — but it **refuses to guess between two columns matching the same alias**, because mapping one arbitrarily would send forty deliveries to the wrong column silently, and a whole-file refusal is recoverable where forty wrong deliveries are not. Line numbers are the dispatcher's, counting the header, since they are looking at the same spreadsheet. Every line is reported exactly once: a dispatcher who uploads 40 and gets 38 with no account of the other two has lost orders they still believe are coming. Two real bugs found by tests: an unquoted address containing a comma splits into more fields than headers and `DictReader` parks the extras as a *list*, which crashed on `.strip()` — now rejoined onto the last column, but only when the parts have content, because joining two empty fields produced `","` and turned every export's trailing blank line into a phantom error. And **the 25-row paste cap collided with T3's 40**: the manifest chunks into `MAX_BATCH_ROWS` calls rather than raising that cap, which keeps the paste path's bounded-latency guarantee (one geocoder call per new address, one per second on the pilot provider) instead of quietly weakening it for everyone. 26 tests. |
| ~~L20~~ | ~~Driver app caught up to the API~~ | **Done, August 2026** — the app had fallen behind three pieces of backend work (R4 compliance, configurable proof, W2 cash on delivery) and **a driver could not go on shift at all**. `updateDocument` sent `expires_at` where the endpoint expects `claimed_expires_at`, so saving failed with a 422; nothing requested an upload URL, so no document ever had a file attached, so every driver read as non-compliant and the go-online toggle refused permanently. Fixed: `DocumentsScreen` rebuilt around upload-then-review (claimed vs verified dates shown side by side so a rejection is legible, and the reviewer's date is what the gate reads), a new `ComplianceBanner` on the route screen that reads `GET /driver/me/compliance` and lists every reason at once **before** the driver taps rather than after a 409 — and distinguishes whose move it is, since telling a driver to "fix" a document sitting in our review queue sends them round in circles. `PodCapture` now reads the stop's own `proof` requirement and collects N photos with named subjects, keeps a signature alongside photos rather than instead of them, and offers a retake of the last photo only — a driver whose fourth shot blurred should not lose the first three. New `CodPanel` collects in full or escalates, **with no field to type an amount into**, placed above the proof controls because the server refuses a completion with cash unaccounted for. Also fixed: `MainTabParamList` was typed `undefined` per tab, making cross-navigator navigation inexpressible without a cast — now `NavigatorScreenParams`, which is what lets the banner send a driver from Home to Documents. Two backend tests added pinning the exact calls the app now makes. |

## Decision log — cofounder alignment, July 28 2026

Design/strategy calls made and aligned in a decision-review session. These
resolve the "open decision" flags scattered below; where an item still
reads "open"/"unresolved" inline, this log is the authority.

| Item | Decision |
|---|---|
| **W1** (returns/cores trigger) | **Piggyback core pickups on the delivery visit to that shop + a counter-person "cores ready" flag for standalone returns.** Each core pickup references its originating delivery order and carries an item manifest. Per-shop readiness *prediction* is a later intelligence-layer follow-on (uses I1 data), not v1. |
| **W10** (package identity path) | **LMX prints and applies its own labels** (universal, LMX owns the ID space) rather than scanning distributors' pick-ticket barcodes. Accepts label-printer capex + an on-site labeling step + co-location. No code change (the `Parcel` model already defaults to LMX-generated barcodes); W8's barcode-printing qualification is dropped. |
| **D6** (interim Epicor latency) | **Tighten the Epicor export interval as the primary onboarding ask, AND fast-path HOT_SHOT/T1 off the 15-min batch drop** (per-order push/webhook/phoned trigger during the scaffold). The 15-min latency then only ever touches T2/T3, where it's noise. |
| **D3** (shadow→cutover bar) | Relative to the scaffold's own actuals on identical orders: **LMX OS must strictly beat drops/driver-hour** (the differentiator), **be no-worse on T1 on-time + hold-release integrity + <5s re-plan**, and drive negative-outcome divergence toward zero — for **2 consecutive weeks that each clear a minimum order volume**. **Founder-owned** weekly review initially; absolute numbers calibrate once shadow data exists. |
| **Security fail-safe** | **Fail closed:** default `ENVIRONMENT` to `production` so a forgotten env var can never ship forgeable default secrets; dev/test/CI set `ENVIRONMENT=development` explicitly. |
| **E6** (flag-type naming) | **Signed off as-is** (`hold_window_too_short` / `hold_window_too_long`). Broadening the annotation vocabulary stays as I3. |
| **B2 contract terms (W7/W8)** | **Co-location is a structural term** (required by the W10 label process). **Training-data rights (W7) required broad and upfront** — model-training, cross-customer aggregation, anonymization — before the first delivery. **Epicor export cadence (W8) is a preference, not a disqualifier** (D6 covers the latency). Owner: Rich/Matan on the contract; founder on the data-rights posture. |
| **R1–R3** (insurance / driver screening / privacy) | **Start all three now, in parallel** with the engineering — not when the pilot is imminent. Owners: Rich/Matan (insurance), Rich (screening vendor), legal/Rich (privacy policy). R4 (driver-document upload) is the buildable software counterpart, slotted alongside A3's upload infra. |
| **A10 / state OT** | **Defer 1099 employment counsel to the 1099 phase** (rollout is W2→1099→gig); when engaging, bring the single-offer-vs-multi-offer question as the precise item — no speculative multi-offer build meanwhile. **State-specific OT** waits on the launch location: populate `Hub.state_code` and research that one state's daily-OT rules once the site is set. |

## Part 1 — Every open item, in one place

Nothing below is new work discovered today — all of it was already called
out somewhere in `docs/ARCHITECTURE.md`, `docs/NEXT_STEPS.md`, or
`driver-app/README.md` as this got built. This just pulls it into one
list instead of leaving it scattered across three documents.

### Gig-platform demand path

From the August 2026 founders offsite. LMX drivers hold individual accounts
on Curri, Dispatch and Roadie; they accept offers in each platform's own
app; the job is relayed into LMX OS, which sequences their day across all
three. Execution outcomes train the dispatch logic. See the decision log
above for the track and hardware calls.

**Three things to be honest about before reading the table.**

**(1) This path cannot validate the batching thesis, and not because of
engineering.** 2.5 DPH rests on two mechanisms: *holding* an order to pair
it with a nearby one, and *assigning* across the whole fleet. A gig job's
delivery window is committed the moment it is accepted, so it can never be
held; and on the gig track it cannot leave the account that accepted it.
What survives is opportunistic sequencing of jobs a driver already holds —
real, valuable, and not the same claim. The carrier track restores
cross-driver assignment but still not holding.

**(2) Three drivers will not produce batchable density.** The pilot ran
~1.8 jobs/driver/day. Three drivers across three platforms is perhaps 6–12
offers/day across a metro the size of Austin, where two jobs rarely overlap
in both time and space. So the near-term value of this section is *not*
batching — it is accept/decline discipline and data accumulation. Pairing
opportunities plausibly need 10–15 drivers. Do not let this section be
pitched internally as proof of commingling until the density supports it.

**(3) The optimizer cannot express these constraints today.**
`app/optimizer/google_routes_client.py`'s `_build_request` sends shipments
with a `deliveries` leg only — no `pickups`, no `timeWindows`, no
`allowedVehicleIndices`; only `globalStartTime`/`globalEndTime` bound the
model. That is correct for the distributor design, where the batch-hold
queue enforces SLAs upstream by choosing *when to release*. Under gig it
inverts: windows are hard, external, and unholdable, so the solver itself
must enforce them. Google's Route Optimization API supports all three
natively, so this is a request-builder change, not a solver to write — but
it extends an integration that has still never made one real call (E1).

| # | Item | Why it matters |
|---|---|---|
| G1 | Notification-listener intake (Android) | `NotificationListenerService` reads Curri/Dispatch/Roadie order alerts and creates the job with zero taps. Also fixes the pilot's worst friction — *"alerts unreliable, buried behind a status banner; one near-miss where an accepted order vanished for about an hour."* The screenshotted Dispatch offer surfaced with **4 minutes left of a 70-minute pickup window**; if that is typical, intake latency is the whole game. **Gated on an empirical payload check** — if the notification body is just "New delivery request", this is an alert aggregator (still worth it) rather than intake. iOS has no equivalent API at all. Must be tested on the real Samsung device with the screen off for an hour, because Samsung's battery manager silently kills listener services. |
| G2 | Share-sheet intake + vision extraction | Driver screenshots the offer → Share → LMX; a vision model extracts the fields. Two taps, works anywhere. Note from the real screenshot: **the collapsed card hides the dropoff address behind a chevron**, so a collapsed-card capture gives windows/pay/distance/pickup/ref but not precise dropoff geocoding — enough to *reject* most offers, not to plan one. Automatic screenshot detection is impossible (iOS fires the event only when your own app is foreground; camera-roll watching is fragile and battery-hostile). |
| ~~G3~~ | ~~`GigJob` model + multi-platform store~~ | **Done** — `app/models/gig_job.py` (migration `0027`) plus `app/gig_platform/service.py`, the intake-agnostic store every path writes through. Carries source platform, both windows, pay, distance, platform ref and **assignment scope as a per-job property** (the rewrite risk the offsite flagged); `is_pinned_to_driver` is derived from scope OR possession, so a collected parcel pins on any track. Dropoff coordinates are nullable for a real reason — a collapsed offer card hides the address (G2) — and `is_sequenceable` says which case a row is in. `offered_at`/`intake_source` exist to make the intake-latency and G1-vs-G2 questions empirical. Named `app/gig_platform/` to avoid colliding with the gig-*driver-pay* code (A11), which is the opposite direction. |
| ~~G4~~ | ~~Accept-gate service~~ | **Done** — `app/gig_platform/accept_gate.py`, `POST /driver/me/gig-jobs/evaluate`. Evaluates without recording (most offers are declined; a 45-second window shouldn't cost a write). Checks short-circuit cheapest-first exactly as scoped — reachability, self-consistency, placement, capacity, economics — and the screenshotted offer fails at step 1 with a test asserting it is never costed. Placement is the G13 hard stop: an offer that would strand a committed pickup is refused regardless of pay (tested with a $500 offer). Sibling bonus deliberately absent — that's G8's parser, and the seam is marked. |
| G5 | Optimizer: pickup legs, time windows, per-job vehicle restriction | Extend `_build_request` with `pickups[].timeWindows`, `deliveries[].timeWindows`, and `allowedVehicleIndices` for gig-scoped jobs. Also: **a collected parcel is pinned** regardless of track. Formally this is a Pickup-and-Delivery Problem with Time Windows — a *harder* shape than the distributor case (many-to-many pickups, hard windows on both legs, no holding), not an easier one. **Do E1 first**; extending an unverified integration means debugging two unknowns at once. |
| G6 | Unified cross-platform itinerary in the driver app | One ordered day spanning three platforms, with each stop showing **which app to close it in** — see G11. |
| ~~G7~~ | ~~Deadhead / reposition cost model~~ | **Done** — `app/gig_platform/economics.py`. Three legs costed, only the middle one paid: deadhead in, engaged, reposition out (charged at 50%, since a driver finishing somewhere useful hasn't incurred a full trip back). A test asserts effective hourly always lands *below* the pilot's $70.74/hr engaged figure — if it ever doesn't, the model has stopped charging for deadhead. Every rate is an explicit `PLACEHOLDER_` constant: the structure is durable, the five numbers are reasoned defaults for a van in a US metro and **replacing them with real per-vehicle cost data is the highest-value change available to this module**. |
| G8 | Sibling-ref detector | The pilot screenshot's ref is `S4588150.002-HOU1` — implying `.001`, `.003` exist. Same base ref, or a shared pickup location, means the marginal cost of the second job collapses. This is the cheapest real batching signal available under these contracts, and it works at any density. |
| G9 | Retention firewall | Enforce the confidentiality boundary in code: train on own execution telemetry and aggregate patterns; do **not** warehouse a standing, identifiable record of a platform's senders, their order frequency, or their pricing. Counsel review before the pipeline is finalised. |
| G10 | Non-circumvention register | Track which sender was served via which platform and when, so any direct-MSA target can be screened against the 6-month lookback. The pilot's largest repeat account (6 of 23 jobs) is the live watch-item. |
| G11 | Dual-completion handling | Under the gig track the driver must mark delivered **in the platform's app to get paid**, so LMX OS tells them where to go and they close the job elsewhere — double entry on every stop. This is the friction most likely to make drivers quietly abandon the app, and it needs a deliberate design answer. The carrier track removes it. |
| ~~G12~~ | ~~Density & volume instrumentation~~ | **Done** — `app/gig_platform/density.py`, `GET /admin/hubs/{hub_id}/gig-density`. `sequenced_share` is the number that matters and its definition is the design: **overlapping possession**, not back-to-back work. Two jobs done in sequence with no overlap deliberately do not count — that is sequential work, not a pairing, and counting it would inflate exactly the figure this exists to keep honest. Pinned by test. jobs/driver/day divides by days actually worked; an empty hub reports null rather than 0.0, because a zero sequenced share on no data reads as evidence of no batching. Rich's 1.8 jobs/driver/day travels with every report as the control group. |
| G13 | Platform-standing risk management | An LMX OS route that causes a miss is a Service Failure on an individual driver's account, affecting their acceptance/on-time standing and deactivation risk. With three drivers, one deactivation is a third of capacity. Needs a hard-stop rule (never accept into an infeasible plan) and per-driver standing monitoring. |

**Open questions to settle empirically — cheap, and they gate real
decisions:**

| Question | How | Gates |
|---|---|---|
| What is in the Android notification payload for each platform? | Android Settings → Notifications → Notification history for a first look; then a throwaway `NotificationListenerService` dumping `extras` to logcat, because the history UI shows only *displayed* text and can give a false negative when the detail sits in `bigText` or a custom extra | G1's scope: full intake, partial pre-fill, or alert-only |
| How late do offers typically surface, relative to the pickup window? | Log notification timestamp vs. window start over a week of real driving | Whether intake latency is the binding constraint |
| Does Dispatch's own **Route** tab conflict with LMX OS's sequence, and does the platform penalise deviation? | Observe during the pilot | G6's design, and G13's risk model |
| Whose account absorbs a Service Failure, and what is the standing penalty? | Platform terms + observation | G13 |
| Do sibling refs (`.001`/`.002`) actually surface as separate offers? | Watch the offer feed | G8's value, and the earliest batching evidence available |

**Baseline for measuring anything here:** Rich's pilot — **$25.17/job,
$1.75/mi, $70.74/hr engaged, B2B ~2× B2C per job, field-service the
best repeatable pattern at $2.03/mi.** That is the control group. Any
claim that LMX OS improved things is measured against those numbers.

**Deliberately not built for this path:** automated intake before volume
justifies it (see the decision log); auto-accepting offers via an
accessibility service — Play Store rejects non-accessibility use of that
API and it is almost certainly a platform ToS breach.

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
| E1 | Verify the Google Route Optimization client against a live Google Cloud project | **Still open, but no longer the same task — one command away.** The real client (`app/optimizer/google_routes_client.py`) has still never made a live `optimizeTours` call; what changed is that reading it against the documented API first turned up four defects, so that call now verifies a mapping worth verifying rather than discovering these one at a time. **(1) The request had no objective function.** Vehicle costs default to zero in the API and none were set, so with skip penalties as the only costs every feasible plan scored identically and the returned sequence was arbitrary — we were paying for a solver and asking it to optimize nothing, while `considerRoadTraffic` bought accurate traffic for a route nobody minimised. Fixed with `costPerHour`/`costPerKilometer`, chosen to stay an order of magnitude below the smallest skip penalty so driving never becomes more expensive than abandoning an order. **(2) The request modelled only half the journey.** `StopCandidate.lat/lng` is the *shop* — the pickup — and it was sent as the shipment's only `deliveries` entry, so the solver was told every job ended on collection. Shipments now carry a `pickups` leg at the shop and a `deliveries` leg at the customer, which also means Route Optimization's atomic-shipment guarantee prevents a driver being given a collection whose drop doesn't fit. Required threading delivery coordinates through `HeldOrder` (and its Redis payload, backward-compatibly) and all four of its construction sites. **(3) A failed call was almost undiagnosable** — `raise_for_status()` discarded Google's error body, which for this API is usually the actual fix ("API has not been used in project X" vs. a missing `roles/cloudoptimization.user`), and tenacity without `reraise=True` replaced it with `RetryError`. Also added a retry predicate: it previously retried a 400, spending the 5s cycle budget twice to fail identically. **(4) The client was rebuilt every dispatch cycle**, running blocking `google.auth.default()` on the event loop, discarding the credential cache so every cycle did a token-endpoint round-trip, and leaking an `httpx.AsyncClient`. Now a process-wide singleton, same shape as the geocoder's. **Remaining work is one command:** `.venv/bin/python -m scripts.verify_route_optimization` makes a single real call against a two-cluster scenario whose right answer is knowable in advance, and checks label round-tripping, that nothing was skipped, that both legs were sent, and — the one that matters — that each driver got the cluster beside them, which is what catches an objective function being ignored while every other check still passes. A test asserts that scenario is satisfiable, so the single paid run isn't spent on a broken assumption. Needs the Route Optimization API enabled and `roles/cloudoptimization.user`. |
| E2 | Tune `SLA_TIER_SKIP_PENALTY` values | **Investigated, still open** — checked against the same Source of Truth documents that resolved E4/E5 (`LMX_OS_Tech_Strategy_and_Design.docx`, `LMX_OS_Architecture.docx`) and the canonical Unit Economics doc (Data Room item 7): none specify a skip-penalty value or per-tier dispatch-priority ratio — Unit Economics covers van-vs-autonomy cost, not per-SLA-tier dispatch weighting. Unlike E4/E5, this is a pure solver-tuning constant (Google Route Optimization's `penaltyCost`), not a documented business rule — there's nothing to check it against until real per-tier route economics exist from an actual operating hub (same gate as E9's DPH validation, gated on B2). The current relative ordering (HOT_SHOT > T1 > T2 > T3) is already directionally correct per every doc's urgency hierarchy; only the exact magnitudes are unconfirmed and unconfirmable before then. (An older, non-canonical "Hub Unit Economics" doc with drone-based T1/T2/T3 pricing exists in Drive but isn't in the Source of Truth Index's artifact list and describes a since-superseded drone-hub concept — its tier semantics don't match this codebase's urgency tiers, so it isn't usable here.) |
| E3 | Confirm real Epicor payload field names | `OrderNum`/`ShipToNum`/etc. are a guess (`app/ingestion/adapters/epicor.py`), not checked against a real tenant. Peer review calls this the most common cause of Phase 1 slippage. |
| ~~E4~~ | ~~Verify the batch-hold "4-question decision logic" against the Source of Truth Index~~ | **Done** — confirmed against `LMX_OS_Tech_Strategy_and_Design.docx`. Questions 1 (SLA deadline) and 2 (0.8mi cluster radius) already matched. Question 4 didn't: the built version used a fabricated absolute hold-time cap instead of the real check (would dispatching now strand a more urgent, still-held order about to need this same scarce driver supply) — now replaced (`app/batch_queue/queue.py`'s `_would_conflict_with_a_more_urgent_order`). Question 3 (driver already heading this direction) still isn't implemented directly here — see `app/optimizer/service.py`'s mid-route insertion for the closest equivalent. |
| ~~E5~~ | ~~Recalibrate SLA hold-window minutes~~ | **Done** — confirmed spec values from the same doc: T1=8min, T2=90min, T3=1080min (18hrs), replacing a placeholder guess (T1=10, T2=45, T3=120) that was off by 2x on T2 and 9x on T3. Per-shop overrides already exist (`active_rules`), so further retuning against real Hub 1 data remains a data change, not a code change. |
| E6 | Confirm the Learning Loop's flag-type naming convention with a driver-app stakeholder | `HOLD_TOO_SHORT_FLAG`/`HOLD_TOO_LONG_FLAG` (`app/learning_loop/detection.py`) is a proposed contract, not one anyone outside this build has signed off on. |
| ~~E7~~ | ~~Wire a real scheduler for the Learning Loop's nightly job~~ | **Done** — `app/learning_loop/scheduler.py`'s `LearningLoopScheduler`, same "asyncio background loop + Redis distributed lock" shape as `app/events/bus.py`'s `HubEventBus`, started at app startup alongside it. Runs once a day, per hub, at that hub's own local 2am (`Hub.timezone`, via stdlib `zoneinfo`) — not one fixed UTC hour, so hubs in different timezones each get a real "nightly" run. `POST /learning-loop/{hub_id}/run-nightly-job` still exists for manual triggering/testing. |
| ~~E8~~ | ~~Move the event bus off in-process~~ | **Done** — `app/events/bus.py` now coordinates through Redis (a `dirty_hubs` set for idempotent cross-instance coalescing, a `SET NX EX` lock for mutual exclusion) instead of local asyncio state, with a fixed-interval poll loop started at app startup. Live-verified with two real, separate app containers sharing one Redis: an event published on instance A while instance A was paused (never got to run its own poll loop) was still picked up and completed by instance B; letting both race normally, exactly one of them ran a given hub's cycle, never both. |
| E9 | Validate the 2.5 deliveries-per-hour (DPH) figure | Called out by the peer review as a model assumption, not an established fact — only provable with real driver/order data at Hub 1 (gated on B2). |
| E10 | Tune HOT_SHOT's skip-penalty/hold-window placeholders | Phase 8 added `HOT_SHOT` ahead of T1 in `SLA_TIER_SKIP_PENALTY` and a 2-minute hold window (`app/sla/engine.py`, `app/optimizer/google_routes_client.py`). Hold window: same status as E5 was before it was resolved — but HOT_SHOT postdates the Source of Truth docs entirely (Phase 8 was added to this codebase after those were written), so there's no confirmed value to check it against; 2 minutes remains a reasoned guess. Skip-penalty: same "no spec exists, not calculable before real data" status as E2 above. |
| E11 | Confirm the SLA delivery targets and credit percentages | **Placeholders in place, nothing agreed** — `PLACEHOLDER_SLA_TERMS` in `app/models/client_sla_term.py`, applied with `scripts/set_client_sla_terms.py --placeholders`. W3 made a breach cost something and needed a delivery commitment to measure against; none existed anywhere (`app/sla/engine.py` defines *hold* windows, not delivery times). Rather than leave the table empty — which means no breach is assessable and the contract goes unenforced while looking fine — the tiers carry openly provisional numbers, same convention as `PLACEHOLDER_AVERAGE_SPEED_MPH`. **Targets are derived; credits are not.** Targets start from `DEFAULT_HOLD_WINDOW_MINUTES` and add the work that can't be skipped (2×8 min on the ground, ~17 min travel for a 5-mile metro run at 18 mph), then carry headroom because two of those three inputs are themselves placeholders: HOT_SHOT 60, T1 90, T2 180, T3 1440 minutes. A test asserts each clears its floor, since a target below it would be **breached by physics** and pay out on every order for a service level nobody sold. The credit percentages (100/50/25/0) encode one commercial judgement — the more a client paid for speed, the more back when we miss — and have no derivation at all. **Confirmed by B2**, when customer #1 signs something; same status as E2/E10, except these are per-client rows rather than constants, so a real contract overrides them without a deploy. |
| ~~E11~~ | ~~Real hours-worked/overtime calculation + admin payroll-run endpoint~~ | **Done** — `app/payroll/hours.py` replaces the old route-span earnings heuristic with real on-duty hours from a durable `driver_shift_events` log, plus federal 40hr/week overtime for `w2` drivers. New `POST /admin/payroll/{hub_id}/run`. Known gaps: no state-specific daily-OT rules, and a workweek split across two pay periods only sees hours visible in the period being computed — see `docs/NEXT_STEPS.md` item 22. |
| ~~E12~~ | ~~Real vehicle-capacity tracking for mid-route insertion~~ | **Done** — replaced the placeholder `MAX_STOPS_PER_ACTIVE_ROUTE` stop-count cap with real `DriverState.capacity_units - load_units` tracking, now actually incremented/decremented by `complete_stop`/`flag_stop_issue` — see `docs/NEXT_STEPS.md` item 21. |

### Security & production readiness

| # | Item | Why it matters |
|---|---|---|
| ~~S1~~ | ~~Real per-user authentication for the ops dashboard~~ | **Done** — `app/ops_auth/`'s `OpsUserAuthMiddleware` replaces the old shared `X-API-Key` with a real per-account Bearer JWT (same password+JWT shape as the client portal); `dashboard/` has a real login screen. `scripts/create_ops_user.py --role admin\|viewer` bootstraps accounts. Real role model now exists (migration `0012`): `admin` (everything) vs `viewer` (read-only) - `require_admin` gates the mutating endpoints (run-cycle, run-nightly-job, onboard a client, revoke a driver device, run payroll), and the dashboard hides those controls entirely for a viewer rather than showing them disabled. Live-verified with real admin and viewer accounts over real HTTP and in a real browser. Still just two roles, not a full permissions matrix - revisit if a reason for finer granularity ever shows up. |
| ~~S2~~ | ~~Secrets management~~ | **Partially done** — `app/secrets_provider.py`'s `SecretsProvider` abstraction (`EnvSecretsProvider` today, a real `AWSSecretsManagerProvider` ready but unexercised without a real AWS account) loads into `os.environ` before `Settings` is constructed in `app/config.py`, so a real vault's values take precedence over `.env` with zero changes needed anywhere else `settings.foo` is read (`os.environ.setdefault` never overrides an explicit env var). Same "unconfigured -> stub" status as Twilio/Rippling/Sentry, one level up. Real gap: which vault to actually adopt, when to migrate, and how rotation should work operationally are still open, deployment-platform-specific decisions (same nature as S3). |
| ~~S3~~ | ~~A real production hosting decision~~ | **Partially done** — `infra/` (AWS, Terraform): managed Postgres (RDS, automated backups, storage autoscaling) and Redis (ElastiCache), the app/dashboard/client-portal each as autoscaled ECS Fargate services behind one ALB, secrets in AWS Secrets Manager (the real account `app/secrets_provider.py`'s `AWSSecretsManagerProvider` was built for), a GitHub Actions deploy pipeline via OIDC (no stored AWS keys). `terraform validate`-clean but not yet applied against a real AWS account — same "real code, unexercised against a live account" status as Google Route Optimization/Rippling/Twilio. See `infra/README.md` for the real named gaps (no staging environment, no NAT Gateway, HTTPS needs a real owned domain first). |
| ~~S4~~ | ~~Observability~~ | **Done, August 2026** — error tracking via Sentry (`app/logging_config.py`), same "unconfigured credential -> no-op" status as Twilio/Rippling until a real DSN exists. A structlog processor forwards warning/error/critical/exception-level events straight to Sentry, since this codebase's structlog setup never touches stdlib logging (Sentry's default `LoggingIntegration` hook would otherwise miss every "caught, logged, and intentionally swallowed" exception, e.g. `HubEventBus`'s handler-failure path) - so both unhandled exceptions and deliberately-caught-and-logged ones reach Sentry. **Alerting completed via `app/health/checks.py` + `GET /internal/health/dispatch` (docs/ALERTING.md), NOT by scraping the Prometheus metrics** — and that choice is the substance of the item. Prometheus counters live in process memory, so on an autoscaled Cloud Run service they reset on every cold start and differ per instance, which makes `rate()` over them noise; nothing was scraping them anyway; and the condition that matters most ("dispatch stopped") is an *absence*, which needs a server with history to express. So the app evaluates the conditions itself from state every instance shares (Redis + Postgres) and answers 200/503, and a Cloud Monitoring uptime check is the entire alerting stack. Four conditions: Redis, Postgres, **dispatch liveness** (orders waiting AND no recent cycle — the failure that is otherwise invisible, since with one driver "no offers arrived" and "no orders today" look identical), and **stuck orders** (past `promised_at` with no driver — cycles can run perfectly and assign nothing, so liveness alone reports healthy right through it). Most of the 26 tests are about the check NOT firing: a hub closed for the day writes no cycle snapshot at all, so without the calendar check this pages every Sunday. Metrics *dashboards* still not started, and an ingestion flatline is a known uncovered gap — it needs a volume baseline before it can be alerted on without false alarms. |
| ~~S5~~ | ~~General API rate limiting~~ | **Done** — `app/rate_limit.py`'s `GeneralRateLimitMiddleware`, a Redis counter+NX-TTL per client IP (deliberately generous - this system leans on client-side polling, see the module's own docstring), 429 + `Retry-After` once tripped, `/health`/docs paths exempt. Known limitation: keyed by the direct TCP peer, not `X-Forwarded-For` - correct only until a real reverse proxy sits in front (Phase 5's hosting decision). |
| ~~S6~~ | ~~A real security review~~ | **Partially done** — a self-review pass across auth, authorization, injection/input-validation, and secrets/CORS/infra. Fixed: driver OTP codes were unconditionally echoed in the API response regardless of Twilio configuration (`app/driver_auth/otp_store.py` — a hardcoded `sent_via_sms=False` meant this would have kept leaking even with real Twilio creds configured, since no real send was ever wired up either; now actually sends via `TwilioSmsClient` and only omits the code when that succeeds), the phone-number-existence check on `request-otp` was an unthrottled enumeration oracle (rate limit now charged before the DB lookup), two fleet-state-mutation endpoints were missing `require_admin` (a viewer could overwrite any driver's status/location), `docker-compose.yml`'s Postgres/Redis ports were published on every interface with a well-known default password, the app container ran as root, a few request bodies took unconstrained strings where a `Literal`/length bound was cheap and correct, and the Twilio webhook now warns loudly at boot if signature verification would be silently disabled in production. Real gap still open: the JWT-secret/webhook boot-time checks all key off `ENVIRONMENT != "development"`, so an operator who simply forgets to set `ENVIRONMENT` in production gets zero protection instead of the most — a cross-cutting fail-safe-default question worth a deliberate decision, not a change made unilaterally in this pass. **Decided July 2026 (see Decision log): fail closed — default `ENVIRONMENT` to `production`, with dev/test/CI setting `development` explicitly.** No one outside this build has reviewed the rest of the pass yet either. |
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
| ~~C5~~ | ~~Self-service client signup~~ | **REVERSED and done, August 2026** — this row previously recorded self-serve signup as deliberately absent ("a B2B onboarding relationship, not self-serve SaaS"). LMX Link needed a front door, so `POST /public/signup` now exists: anyone can apply. **The posture C5 was protecting is preserved by an approval gate, not abandoned** — a signup creates a `pending` client whose first user is `is_active=False`, so C4's existing per-request check already prevents login, and nobody dispatches an LMX van until a human approves. Approval (`POST /admin/signups/{id}/approve`, `dashboard/`'s `PendingSignupsPanel`) is also where per-tier rates are set, which means an *active* client always has rates and `Order.fee_cents` can never be null for them. The endpoint is rate-limited by IP charged *before* the duplicate-email check, and a duplicate returns the same 202 as a fresh signup — otherwise it is an oracle for discovering who is already an LMX customer. |

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
| ~~W1~~ | ~~Returns & core pickups as first-class work~~ | **Done** (PRs #13–#16, `migrations/0024`–`0025`). `app/models/return_item.py` is the reverse leg the system couldn't model before: a core moves `expected → collected → returned_to_shop`, with `not_ready`/`cancelled` branches. Slice 1 — piggyback capture at the delivery visit (created at ingestion from a `return_manifest`/`core_return` flag, collected on the dropoff). Slice 2 — a counter-person "cores ready" flag for standalone accumulated returns (`origin_order_id` nullable). Slice 3 — the return-to-shop leg (driver drops cores at the shop; ops manual mark). Slice 4 — the counter-facing "awaiting pickup, with age" list (`age_hours` on every view), the `not_ready → ready_for_pickup` reschedule, and the client-portal **Returns** tab (list + flag-cores form). Per-shop readiness *prediction* remains a later intelligence-layer follow-on (uses I1 data), as scoped. |
| ~~W2~~ | ~~COD collection & payment disputes~~ | **Done, August 2026** — `app/delivery/cod.py`, `app/models/cod_collection.py`, `POST /driver/stops/{id}/collect-cod` and `/cod-dispute`, escalation to the distributor, and `GET /admin/hubs/{hub}/cod-disputes` for the monthly owner report. **First finding: `COD_DISPUTE` has been a stop failure reason since the driver app was built, for a payment mode the order object could not express** — `PayerType` was `contract_client | prepaid | card_on_file`, so a driver could flag a COD dispute on an order that was never COD. Added `cash_on_delivery` and `orders.cod_amount_cents`, kept separate from `fee_cents` and `quoted_amount_cents` because **it is the distributor's invoice to their own customer — money we carry, not money we charge** — and conflating them would make a customer's dispute look like a billing question about LMX. That ownership is also why the rule is what it is: nobody at LMX has authority to discount it. **"Enforced by the UI, not by training alone" is implemented as an absence:** `CollectCodBody` has no amount field, so "collected" can only mean all of it. A driver facing a customer offering eighty against a hundred has two paths because a third was never built. **The teeth:** a COD dropoff cannot be completed with the money unaccounted for — before this a driver could mark one delivered with no record of any cash changing hands, parts gone and invoice unpaid, and nothing would notice. A dispute settles it for the purpose of leaving, because "keep moving" is part of the rule. Escalation goes to the **shop, not LMX ops** — they are the only party who can decide anything, and routing through us costs them hearing while their customer is still standing there. **Second finding:** the stub SMS client returns no Twilio SID, so a naive `escalated = sid is not None` would report every dispute on today's deployment as un-escalated — a metric that cries wolf permanently. Escalation is now tri-state, and the report carries `sms_configured` so one deployment-wide fact (B5) isn't reported as N per-account failures. **Open, and worth knowing: until B5 lands, nothing is actually escalated** — the dispute is recorded and the message stored, but no distributor is texted. Report groups by shop rather than client (a distributor can have forty branches) and carries collected counts beside disputes, since three of four and three of three hundred are different facts. 26 tests. Cash custody remains an R1 question; every collection names the driver who took it. |
| ~~W3~~ | ~~SLA-breach invoice credits~~ | **Done, August 2026** — `app/billing/credits.py`, `app/models/client_sla_term.py`, `invoice_credits`, netted into the statement. **The finding: "late" was not computable.** W3 asks for credits "computed from order-level SLA outcomes" and the outcome did not exist — `app/sla/engine.py` defines HOLD windows (when we must set off), `hold_deadline` is ours and internal, and `promised_at` is only populated when a source hands us one, which for an LMX-owned order is never. A credit schedule alone would have been a penalty with no trigger. `client_sla_terms` supplies the missing half **as contract data per client and per tier, not a constant chosen in a Python file** — LMX has no company-wide delivery SLA written down anywhere, and hardcoding one would have invented our service level. Measured from receipt, because that is the moment a client can point at. Three rules, each a way this could quietly be wrong: **a tier with no term is reported as unassessable rather than clean** ("we owe nothing" and "nobody wrote down what we promised" are different answers); an explicit `promised_at` beats the tier default (something said out loud outranks a default); and **a credit never exceeds the fee**, since crediting more than was billed turns a statement into a payment. A row per breached order with the evidence frozen — what was promised, what happened, how late — because "which ones?" is the first question and an aggregate answers it with "check your own records". Invoices now carry gross, credits and net separately: a statement showing only a net is one a client cannot reconcile, and one that hid the credit would also hide that we missed something. |
| W4 | Driver-visible scorecard | Story DR-10: the driver sees *the identical metrics and definitions* the orchestrator sees. Explicitly framed as a trust decision, not a feature — "a shared standard, not a camera pointed at me." Folds into I4/F7's analytics work; the requirement is that the driver view is the same computation, not a separate reduced one. |
| W5 | Counter-person order status lookup | Story CP-3: search any order by shop name or order number, get live status and ETA in ten seconds. The client portal today is distributor-owner-facing with one login per company (C4). The counter person is a **distinct persona** with a distinct need — and CP-4 (wrong-part flag reaching the counter mid-route) is a second counter-facing surface. Reopens C4's "one login per client" decision on real grounds rather than as an oversight. |
| ~~W6~~ | ~~Orchestrator-editable urgency configuration~~ | **Done** (PR #8) — per-hub urgency rules with full CRUD (`POST`/`GET`/`PATCH`/`DELETE /admin/hubs/{hub_id}/urgency-rules`), consumed by `app/sla/engine.py` at classification time and `app/ingestion/service.py` at ingestion; `dashboard/src/components/UrgencyRulesPanel.tsx` is the editing surface. "Body panels are never urgent" (story OR-6) is now a dashboard change, not a code deploy. Distinct from I2, which promotes *machine-proposed* rules — this is direct human authoring. |
| W7 | Training-data rights in the customer contract | Session closing note: model-training rights, cross-customer aggregation rights, and anonymization terms "belong in customer #1's contract before the first delivery, not in a future amendment." Distinct from R3 (privacy policy — what LMX does with personal data); this is about the right to train models on a customer's operational data. **Legal work, gates B2, not engineering.** |
| W8 | Epicor staging-module qualification check | Session D6. Whether a prospect's Epicor runs a warehouse/staging module is now a sales-qualification checklist question asked before signing, because it determines whether real-time ingestion is even possible for that customer. Not code — a sales-process artifact that gates which prospects are viable. |

**W10, who owns the barcode — DECIDED July 2026: LMX prints and applies
its own labels** (see the Decision log at the top of this file). The
table below is kept for the reasoning behind the call. Two materially
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
be moving now. ~~W6~~ and ~~W10~~ are **done** (PRs #8 and #11). W10
shipped ownership-agnostic — `Parcel.barcode` carries either an
LMX-generated code or the distributor's own pick-ticket value, and the
verification path is identical either way — so the printer-vs-scan-existing
hardware decision is still open but no longer blocking anything. It rides
on W8's answer whenever that lands.

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
| R3 | Privacy policy & data-handling/retention policy | **Drafted and served, awaiting counsel and four retention decisions (August 2026).** `app/legal/content/privacy.md`, structured by whose data it is rather than by data type — businesses we deliver for, people receiving a delivery, and drivers — because a recipient never agreed to anything with us and their requests mostly route back through the sender. The inventory was written from the schema, not from memory: every field it names is a column that exists. **The retention section is the part with teeth,** because a stated period nothing enforces is the same defect as proof of delivery nobody checks. Two are enforced — driver location trails at `LOCATION_PING_RETENTION_DAYS`, pruned by `POST /internal/retention/prune` (`app/legal/retention.py`), and tracking links via `tracking_link_grace_hours`. **Four are not:** proof-of-delivery images and driver documents (object storage — a bucket lifecycle rule, not an application loop), SMS/call records, and declined applications. Those either get built before the policy states them or the policy stays vaguer. Note the interaction with `R1`: **proof retention must outlast the claim window**, or we delete the photograph before the client can still claim on it. Still gates Phase 9; `docs/LEGAL_BRIEF.md` has the decision list. |
| ~~R4~~ | ~~Driver document upload pipeline~~ | **Done, August 2026** — and it turned out to be a bigger hole than this row described. The row said `file_url` accepts any string; reading the code found the compliance gate was defeatable **two** ways. **(1) The driver set their own expiry date.** `PUT /driver/me/documents/{doc_type}` took `expires_at` from the driver, and `update_my_availability` then refused to put them online if that self-chosen date had passed — so a lapsed license became a valid one by typing next year. **(2) The gate only looked at rows that existed.** It searched for documents *past* their expiry, so a driver with no documents at all had none expired and went online — it blocked the honest driver who recorded a lapsed license and cleared the one who recorded nothing. Fixed by separating claim from established fact: `claimed_expires_at` (the driver's, never read by any gate — the name is deliberate so a future caller can't reach for it by accident), `verified_expires_at` (what an ops reviewer read off the document, the only date any decision acts on), plus `review_status`/`reviewed_at`/`reviewed_by_ops_user_id`. `app/compliance/driver_documents.py` is now the single gate and requires every document in `REQUIRED_DOC_TYPES` to be **present, uploaded, reviewed and unexpired** — absence, awaiting-review and rejection are distinct reported reasons, since "we haven't looked at your insurance yet" and "your license expired" need different actions from different people. `file_url` is now backend-written from a key it minted for a presigned upload (`app/storage/document_upload_client.py`, own key prefix, restricted content types so a driver can't upload HTML that renders when a reviewer opens the "scan"); a re-upload gets a fresh key so it can't overwrite evidence someone already reviewed, and resets the verdict. Ops side: `GET /admin/drivers/documents/pending` + `POST /admin/drivers/documents/{id}/review` and a `DriverDocumentsPanel` — where the expiry field is **empty and required**, never prefilled with the claim, because prefilling would make one-click approval the path of least resistance and turn the review into a rubber stamp on self-attested data. 29 tests. **This is a presence check, not a safety check** — nothing here establishes a license is genuine or its holder safe to drive; that is still R2. What it does is stop the system asserting a check it never performed. **Operational consequence of migration 0032, stated plainly: every existing driver becomes non-compliant on deploy** and needs an ops review pass, because nothing in this table was ever verified and backfilling `verified` would invent a review that never happened. |
| ~~R5~~ | ~~Failed-delivery / redelivery workflow~~ | **Done** (PR #6) — `app/delivery/resolution.py` plus `POST /admin/orders/{order_id}/resolve`. `Order` gained `delivery_attempts` (1 = the original), `failure_reason`, and a `returned` status, so a `failed` stop now has a defined next step instead of dead-ending: redelivery, return-to-distributor, or cancel, with shop notification wired through `app/messaging/shop_notifications.py`. The `SHOP_CLOSED` day-one playbook (session D5) now has software behind it rather than only a phone call. |
| ~~R6~~ | ~~Hub closure / holiday calendar~~ | **Done** (PR #7, `migrations/0021_hub_closures.py`) — `app/models/hub_closure.py` + `app/hub_calendar.py`, with admin create/list/delete endpoints. Wired into both places that previously assumed every hub runs every day: `app/learning_loop/scheduler.py` (the nightly job skips a closed hub) and `app/optimizer/service.py` (no dispatch cycles for a hub that isn't open). The first holiday or weather closure no longer misfires the scheduler or plans routes into a closed warehouse. |
**Sequencing:** R1/R2/R3 are business/legal work that should start *now*
— they're slow (insurance quotes, policy drafting, screening-vendor
selection) and they gate Phase 9, so starting them when the pilot is
imminent is starting them too late. R4 fits Phase 6 alongside A3 (same
file-upload infrastructure, build once). ~~R5~~ and ~~R6~~ are **done**
(PRs #6 and #7) — both were "the system assumes the happy path" gaps, and
both are now closed before a real pilot could surface them the hard way.

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
| ~~F1~~ | ~~Live driver location pipeline~~ | **Done** — `POST /driver/me/location` (driver-authenticated; the only prior write path required an ops admin, so **nothing in production would ever have populated a position and the optimizer skips any driver whose location is None — a live fleet would have been assigned no work at all**). Writes twice: Redis for the optimizer's hot path, and `driver_location_pings` (migration `0026`) as a durable trail, because Redis overwrites and miles-per-drop is one of W9's nine scorecard metrics. `recorded_at` is the device's observation time, not the write time. `DriverState` now carries lat/lng so the roster can render positions (F2 is then purely front-end). The app reports **only while on duty** — an off-shift driver isn't tracked, which is what keeps W4's driver-visible scorecard defensible as a shared standard rather than a camera. Foreground-only; background location needs Apple's "always" tier and a review justification (A6). Retention of the trail is deliberately unresolved and belongs to R3. |
| ~~F2~~ | ~~Live map view (ops dashboard)~~ | **Done, August 2026** — `dashboard/src/components/FleetMap.tsx`. Purely front-end: F1 put each driver's position onto the fleet roster response, so the map consumes the same polled data `FleetRoster` already gets and adds no endpoint. **OpenStreetMap tiles, no account** — same pilot reasoning as the geocoder, since a keyed provider needs the Google Cloud project still blocking E1 and waiting would have left the dispatcher blind. Attribution set per OSM's policy; heavy or public-facing use needs a real tile provider. Two deliberate calls: a position over 5 minutes old is drawn **hollow rather than hidden**, because a driver silently vanishing from the map is worse than one shown where they last were; and drivers with **no** position get their own list under the map with the reason stated, since the optimizer skips a driver it can't locate — so "nobody is being assigned work" and "nobody is reporting" are the same fact, and that is where it becomes visible instead of mysterious. |
| ~~F3~~ | ~~Customer-facing live tracking page~~ | **Done, August 2026** — `GET /public/track/{token}` + `client-portal`'s `/track?token=…` page, with the link texted to the recipient's own phone when their parts are collected (`app/messaging/tracking_notifications.py`). Unblocked by F1, which gave drivers a way to report position at all. **The page was the easy half; the substance is what it refuses to show, because the obvious implementation — render whatever Redis holds for the assigned driver — hands a member of the public a continuous GPS feed for one of our employees.** Three rules, in `app/tracking/service.py`: (1) a driver's position is visible ONLY while that driver's *current stop* is this recipient's delivery — not merely "while the order is picked up", because a driver mid-route is carrying other people's parcels, so showing their position between drops tells recipient A roughly where recipient B lives and shows both the shape of the driver's whole working day. Deriving it from the stop sequence rather than `Order.status` is what makes the rule precise. (2) The link expires (`TRACKING_LINK_GRACE_HOURS`, default 24h past completion) — it survives delivery long enough to show the confirmation, then answers exactly as it would for a token that never existed, because a tracking URL with no end date is a permanent window onto whoever carries that route next week. (3) The response schema IS the privacy boundary: no driver name or phone, no other stops, no client identity, no internal ids, and the destination is *hinted* (street without house number) rather than disclosed, since these links get forwarded and screenshotted. Enumeration-safe — unknown and expired tokens are byte-identical 404s — and rate-limited with a ceiling set for "a family refreshing on two devices" rather than one reader, since a limit tight enough to stop a guesser would break the feature. 24 tests, most of them proving the check does NOT fire. Map is an OSM-tiled iframe: no key, no bundle weight on a page opened once over mobile data, one marker. **Noted while here:** `OrderStatus.en_route_drop` exists in the enum and the state machine but nothing ever transitions an order into it — the tracking page derives "on the way" from the stop rows instead, which is more truthful, but the dead state is worth either wiring or removing. |
| ~~F4~~ | ~~Outbound status webhooks + a small integrations surface~~ | **Done, August 2026** — `app/webhooks/`, the first status sink that reaches outside this system, plus an **Integrations** tab in the client portal and a consumer-facing contract in `docs/WEBHOOKS.md`. Also closes **L10** ("a sink that reaches a customer"). **The design decision that matters is that the sink does not send anything.** `emit_status_change` runs inside `advance_orders`, *before* the caller commits — so an inline POST could tell a client an order was delivered on a transaction that then rolled back, and there is no un-sending that. It also sits on the hot path a driver is standing at a door waiting for. So the sink writes a `WebhookDelivery` row in the caller's transaction and returns; delivery happens afterwards, immediately as a post-commit task (hooked into `get_db`/`session_scope` teardown, so no call site has to remember) and via `POST /internal/webhooks/deliver-pending` as the guarantee — the same in-process-primary / scheduler-safety-net shape as dispatch, and for the same reason. Retry discipline is the same distinction the geocoder and routing client needed: 5xx / 429 / 408 / transport errors retry over ~3 days; **any other 4xx is `rejected`, not retried, and deliberately does not count against the endpoint's failure budget** — a handler that 422s an unimplemented event type must not lose the events it does handle. 20 consecutive failures pauses the endpoint (switched off, not deleted, with the reason visible in the portal). Signed HMAC-SHA256 over `{timestamp}.{body}` — the timestamp is *inside* the signed string, so a captured request isn't replayable forever. **A client-supplied URL is an SSRF primitive, not a config field** (`url_safety.py`): https-only, every resolved address must be public, no redirects followed, credentials in the URL refused, and it fails closed when DNS doesn't answer. DNS rebinding is recorded as not solved — closing it needs request-time resolution with connection pinning, which httpx doesn't offer. 48 tests. Payload carries the §1.4 public label and `source_order_ref` (the only id a client's own system recognises) and no internal ids. |
| ~~F5~~ | ~~Flexible/rate-table billing~~ | **Done, August 2026** — `app/billing/rates.py`, migration 0039. `client_rates` was flat per-drop per-tier and now carries per-mile, per-piece and per-weight components plus a minimum charge. **Additive rather than a mutually-exclusive `basis` enum, because that is how courier rates are actually written** — "$8 plus $1.50 a mile, minimum $12", never per-mile alone. An enum would force every hybrid contract to be approximated, and the approximation surfaces later as an argument about an invoice. All components default to zero, so every existing rate prices exactly as before. `orders.fee_breakdown` records the arithmetic: with one flat rate "why is this $18" needed no explanation, and with a rate table reconstructing it later from a card that may since have changed is not an answer. Two care points — mileage is multiplied before rounding (rounding the distance first turns a 4.4-mile drop into a 4-mile one and under-bills every short run), and **a distance we could not compute is not charged for**, recorded as an explicit zero line rather than silently omitted. Distance is straight-line at the same placeholder used everywhere else and the breakdown says so, so the day E1 lands old and new lines are distinguishable. Pricing stays frozen at ingestion: a card edited mid-month affects the next order, not the last hundred. 25 tests shared with W3. |
| F6 | Real-time mid-route re-optimization | Today's optimizer solves fresh each cycle (E7's scheduler) rather than continuously re-sequencing an in-progress route as conditions change — Wise Systems' and Onfleet's core marketing claim. Depends on E1 (verify the live Google Route Optimization client) being done first. |
| F7 | Client- and ops-facing analytics dashboards | Reinforces I4 (already on the roadmap) — DPH, on-time %, driver leaderboards, cost-per-drop trend, SLA-breach history, CSV/BI export — but every competitor also exposes a *client-facing* cut of this (their own on-time rate, delivery volume) in the portal, which I4 doesn't currently scope. Directly serves the "market adoption" story for a distributor moving to per-drop pricing — it's the retention/upsell proof, not just an ops nicety. |
| F8 | White-label / multi-brand portal theming | Bringg, Onfleet (Enterprise tier), and Locus all offer a rebrandable client-facing surface. Relevant if LMX ever resells through a partner or franchise model — not urgent for Hub 1. |
| ~~F9~~ | ~~Hybrid gig-fleet overflow dispatch~~ → **Reinstated as P7** (Sourabh, July 2026 — see Autonomy Partners section) | Wise Systems' "DoorDash Dial" auto-routes overflow orders to third-party gig couriers by cost/rules. This item's history in one place, since it flipped twice in one day: (1) first flagged as an unresolved conflict against a companion analysis's "LMX is the fleet, not a Bringg competitor" stance; (2) decided **not** to build it, full stop; (3) revisited same-day and reinstated — with three guardrails (data tagging, a volume cap, real pricing before building) — as a *generalized cost-optimized dispatch option* rather than a narrow overflow valve, folded into P1–P3's courier abstraction as **P7**, gated on unit economics rather than dropped as a permanent no. |
| F10 | A real path to SOC 2 (or equivalent) certification | Every one of the four competitors leads their security page with SOC 2 Type II (plus ISO 27001, sometimes HIPAA/GDPR audits). Reinforces S6 (security review) and S2 (secrets management) — this raises their urgency from "good hygiene" to "the thing enterprise clients will ask for in a security questionnaire." A companion analysis adds a concrete trigger: enterprise dealer groups (the recommended anchor client type) ask for SOC 2 in diligence, and it's a multi-month audit — start readiness well before it's needed, not when it's blocking a deal. |
| F11 | SSO/SAML for ops and client logins | S1 built real per-user auth with roles, but not SSO — Bringg, Wise Systems, and Locus all support it for enterprise buyers. |
| F12 | Network/territory optimization tooling | Wise Systems' "Network Optimization" (depot/zone redesign, distinct from daily routing) — relevant once LMX runs multiple hubs, not for a single Hub 1 pilot. |
| F13 | Ratings & feedback capture | One-tap post-delivery rating (+ optional comment) prompt to the shop, landing on the order/stop record. Low effort, and it feeds the Learning Loop (I3's broader annotation vocabulary) with a ground-truth satisfaction signal none of the four researched competitors structurally capture the same way. No external dependency. |
| F14 | Orchestrator route-preview / shadow mode | **Substantially upgraded July 2026 — see W9 below.** Originally scoped as a view to preview the optimizer's proposed plan before it commits. The workflow session's decision D3 makes shadow mode far larger: the standard onboarding gate for *every* customer engagement, not a one-time pilot tool. The preview/override surface described here is still wanted, but it is now the small half of this item. |
| ~~W10~~ | ~~Package identity & scan-at-pickup verification~~ | **Done** (PR #11, `migrations/0023_parcels.py`) — a real `app/models/parcel.py` with a uniqueness constraint, and `ScanParcelBody{barcode: str}` verified against the order at the pickup stop (`app/api/driver_routes.py`); the old bare-count `ScanParcelsBody` survives only as the manual "can't scan, confirm by hand" fallback. `tests/integration/test_parcel_scanning.py` covers it. **The open label decision was handled well rather than forced:** the model is ownership-agnostic — `barcode` holds either an LMX-generated code or the distributor's own pick-ticket value (`app/ingestion/service.py` takes it from the payload when present, generates one otherwise), and the verification path is identical either way. So the printer-vs-scan-existing hardware call stays deferred and reversible, which is what W8 should decide. A2's scanner now has something to read, and `WRONG_PART` is catchable at pickup rather than at the customer's door. |
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
Every day Hub 1 runs before ground-truth capture exists is training data
lost forever — drive times we never recorded can't be backfilled. Models
can wait; instrumentation can't.

**Status, July 2026: the two urgent rows are done.** I1 (ground-truth
capture) shipped in PR #9 and I2 (rule review & promotion) in PR #10 —
both landed *before* Hub 1 goes live, which was the whole point. The
remaining rows below are genuinely data-gated rather than code-gated: I3
is still worth doing pre-pilot (the flags drivers can't write are the
labels we won't have), I8 is an ops assignment rather than a build, and
I4–I7 need weeks-to-months of real data before they mean anything. Note
that under the scaffold model the data now accrues during the **shadow
period**, not from Hub 1's own operation — the scaffold does no batching,
so it never exercises the hold windows.

| # | Item | Why it matters |
|---|---|---|
| ~~I1~~ | ~~Ground-truth event capture~~ | **Done** (PR #9, `migrations/0022_ground_truth_capture.py`) — `Order.delivered_at` written once at dropoff completion and never on update, retiring the `updated_at`-as-delivered-at proxy billing and the portal relied on (`updated_at` moved on *any* later mutation, e.g. attaching an invoice id — exactly the corruption the proxy needed guarding against). Also `Stop.arrived_at`, per-leg drive-time capture, and `RouteOffer.decline_reason` — a decline is only a usable training label for I6's offer-acceptance signal if the reason is known, so expiry (no response) and declined-without-reason stay distinguishable as NULL. |
| I8 | Manual capture of the non-default training situations | The session's own operating note: roughly a third of the 36 matrix situations **are not captured by default** — the paired access-note comparison, the interim dispatcher's gut-call log, the scaffold-era "where's my part" call tally, and the shadow divergence pairs all require someone deciding in week one that they are worth writing down, on a shared sheet if no app exists yet. Two of these are only capturable *during* the scaffold era and are gone forever after cutover: the call tally (C2) and the human dispatcher's tacit expertise, right and wrong (H1/H2). This is an ops checklist assignment, not a build — but it is on the critical path for I6 and belongs to whoever runs Hub 1 operations. |
| ~~I2~~ | ~~Rule review & promotion flow~~ | **Done** (PR #10) — `app/learning_loop/promotion.py` plus `GET /admin/hubs/{hub_id}/proposed-rules` and the promote/dismiss endpoints; `dashboard/src/components/ProposedRulesPanel.tsx` is the review card (proposal, evidence count, confidence, approve/dismiss). Covered by `tests/integration/test_rule_promotion.py`. This closes the Learning Loop's missing rung — nightly proposals no longer accumulate with nowhere to go, and promotion is a click rather than a manual SQL insert. |
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

### Phase 3.4 — Gig-platform demand path (NEW, August 2026)
**Goal:** real paid order flow and real training data without waiting on a
signed distributor. See Part 1's "Gig-platform demand path" for the
item-by-item detail and the three honest caveats.

**This phase and Phase 3.5 are alternatives, not a sequence.** Both exist
to generate revenue and learning before LMX OS runs a real operation, but
they source demand from opposite directions — gig platforms versus a signed
distributor on a scaffold. **Whether G replaces the distributor path, runs
beside it, or bridges to it is an open cofounder question** (see the August
decision log). Nothing here assumes an answer, and the phases are numbered
3.4/3.5 rather than sequentially to avoid implying one.

Build order, revised deliberately so the highest-risk work comes last:

| Stage | Items | Effort | Gets you |
|---|---|---|---|
| 1 | **E1** (verify Google API), **G3** (job model), manual entry, **G7** (deadhead cost), **G8** (sibling detector) | 8–12 days | Jobs logged, real marginal economics, sibling detection. The data flywheel starts |
| 2 | **G5** (pickup legs + time windows + per-job vehicle restriction), **G4** (accept gate) | 8–12 days | Windowed optimization and real accept/decline advice — the actual product at this size |
| 3 | **G6** (unified itinerary), **G11** (dual completion), **G12** (density instrumentation) | 7–10 days | A route worth showing a driver, and the data to know when batching becomes possible |
| 4 | **G1** or **G2** (automated intake) | 5–10 days | Only once manual entry genuinely hurts — a 30-driver problem, not a 3-driver one |
| — | **G9**, **G10**, **G13** | 5–7 days | On counsel's timeline, before any scale-up |

**Stages 1–2 are about four to five weeks** and deliver the thing that
actually matters at three drivers: knowing which offers to take. Total
section G is **~32–48 engineer-days ≈ 7–10 focused weeks**, which at a
realistic share of one founder's time is **3–5 calendar months** — and it
would be *displacing* Phase 3.5 work, not adding to it.

**Exit criteria:** three drivers running live across all three platforms,
every accepted job in LMX OS, an accept/decline recommendation produced
inside the platform's acceptance window, and $/engaged-hour measurable
against the pilot's $70.74 baseline.

**Do not claim from this phase:** validated batching economics. Density at
three drivers will not support it (Part 1 explains why). Claim accept
discipline, marginal-economics visibility, and training data.

### Phase 3.5 — The Elite EXTRA scaffold (distributor path, July 2026)
**Goal:** Hub 1 generates revenue on a scaffold while LMX OS is still
being built. This phase did not exist in earlier versions of this plan,
which assumed LMX OS ran Hub 1 from day one.

**Status note, August 2026:** the offsite's gig-platform path (Phase 3.4)
is an alternative source of early revenue and learning that does not
require a signed distributor. This phase remains the plan of record for
the distributor thesis, and everything below still stands — but it is no
longer the *only* way to get to real orders, and it should not be assumed
to be the one being pursued.

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
  accept the risk. **Decided July 2026 (see Decision log): tighten the
  drop as the primary onboarding ask, and fast-path HOT_SHOT/T1 off the
  batch so the 15-min latency only touches T2/T3.**
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

**This phase belongs to the distributor path only.** The gig-platform path
(Phase 3.4) has no competing human dispatcher to shadow and no scaffold to
cut over from — LMX OS is in the loop from day one there, just constrained
to sequencing rather than deciding what to dispatch. So if the offsite's
path becomes the primary one, this phase does not apply as written and the
2.5 DPH claim needs a different proof (the gig path cannot supply one — see
Part 1's caveat (1)).

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
and the weekly review owner — **decided July 2026 (see Decision log):
beat drops/driver-hour, parity on the safety metrics, 2 consecutive
qualifying weeks, founder-owned review; absolute numbers calibrate once
shadow data exists.**

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
- **Before Phase 9 goes live — done.** ~~I1~~ (ground-truth capture, PR
  #9) and ~~I2~~ (rule review/promotion, PR #10) both shipped ahead of
  the pilot, which is exactly the sequencing this section argued for:
  instrumentation before the first real order, and an approval tool
  before proposals start accumulating. **I3 (broader annotation
  vocabulary) is now the one remaining pre-pilot item here** — the flags
  drivers can't write are the labels we won't have.
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
