# LMX Driver App

React Native (Expo) app for drivers - now covers all 18 screens of the
wireframe spec (`LMX Driver App Wireframes.dc.html`) across three phases
(see `docs/NEXT_STEPS.md` items 12/13/14):

- **Phase 1** (screens 1a-1m): sign in, vehicle setup, go online, accept a
  job offer, and run the pickup -> scan -> dropoff -> proof-of-delivery
  flow against the real LMX OS backend (`app/api/driver_routes.py`).
- **Phase 2** (screen 1r): profile - edit vehicle, license/insurance
  documents (with a real going-online gate if either's expired), and a
  masked (last-4-only) payment method.
- **Phase 3** (screens 1n/1o/1p/1q): a placeholder earnings estimate and
  trip history, plus masked SMS messaging with the customer (from the
  active-job screen) and with dispatch/support (from Profile).

## Setup

```bash
cd driver-app
npm install
npm start
```

Point the app at your backend by editing `app.json`'s `expo.extra.apiBaseUrl`
(defaults to `http://localhost:8000`). If you're running the Expo dev
client on a physical device, `localhost` won't reach your laptop - use
your machine's LAN IP or `ngrok` instead.

The backend's driver routes are exempt from the ops-dashboard auth gate
(`app/ops_auth/middleware.py`) since they have their own real per-driver
auth - no extra header needed from this app.

## What's real vs. stubbed in this pass

- **Auth**: real phone + OTP + JWT session against the backend. No Twilio
  SMS wired up yet (see `app/driver_auth/otp_store.py`) - the OTP code is
  shown on-screen in dev (`debug_code` in the API response) instead of
  being texted.
- **Scan parcels (1k)**: real `expo-camera` barcode scanning
  (`src/media/BarcodeScannerModal.tsx`), calling the same
  `POST /driver/stops/{id}/scan` endpoint a manual count always did - the
  backend contract never changed. A manual "can't scan? confirm
  manually" fallback stays for a damaged barcode.
- **Proof of delivery (1m)**: real camera photo capture and a real
  drawable signature pad (`src/media/PhotoCaptureModal.tsx`,
  `SignaturePadModal.tsx`), uploaded via a presigned S3 URL
  (`app/storage/photo_upload_client.py`) - same "unconfigured -> stub"
  status as Twilio/Rippling until a real bucket is configured. The PIN
  method is real too now: `app/messaging/delivery_pin.py` issues and
  texts a real 4-digit PIN to the customer at offer-accept time, and
  `complete_stop` verifies the driver's submission against it server-side
  (with a lockout after too many wrong attempts) - not just recorded.
- **Messaging (1p/1q)**: real masked SMS - sends via
  `app/messaging/sms_client.py`'s `TwilioSmsClient` once a Twilio account
  is provisioned (`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/
  `TWILIO_FROM_NUMBER`); until then, every send goes through
  `StubSmsClient` (logged, not actually delivered) - same "unconfigured ->
  stub" pattern as OTP codes above. The inbound-reply webhook
  (`app/api/webhooks.py`) also has no Twilio request-signature
  verification yet - a real gap to close before pointing a live number at
  it, not just a formatting note.
- **Masked calling** (as opposed to messaging): real now too -
  `app/messaging/voice_client.py` places a Twilio Voice call to the
  driver's own phone, then bridges to the customer via TwiML
  (`app/api/webhooks.py`'s `voice_connect`) with LMX's shared number as
  caller ID - two real bridged phone calls, not in-app audio. The "Call"
  button on the stop-detail screen calls it for real instead of showing a
  dead-stub alert.
- **Navigation (1h)**: real now - a "Navigate" button on the stop-detail
  screen (`src/utils/navigation.ts`) hands off to the device's own native
  maps app for real turn-by-turn, rather than embedding a maps SDK/
  rendering a route in-app. No API key needed - falls back through
  Google Maps -> Apple Maps -> a plain web URL, whichever is actually
  installed.
- **Earnings (1n/1o)**: real hours-worked data (reconstructed from the
  shift-event log, `app/payroll/hours.py` - not a route-timestamp
  heuristic), including real federal FLSA overtime and a pluggable
  per-state overtime rule mechanism (`app/payroll/overtime_rules.py`,
  see `docs/PAYROLL_STATE_OT_RESEARCH.md` - no state-specific rule is
  turned on yet, that's a business/legal decision). Pay itself still runs
  through an explicitly-labeled placeholder hourly rate when a driver has
  no real `hourly_rate_cents` set, and isn't connected to any payroll
  system yet (Rippling - not provisioned, see `docs/NEXT_STEPS.md` item
  15). Every earnings response is marked `is_placeholder: true` and shown
  that way in the app whenever the rate itself is a placeholder.

## Building for TestFlight / Play Store (EAS)

`eas.json` has real `development`/`preview`/`production` build profiles
and a `submit.production` block, and `eas-cli` is a devDependency - all
of it `terraform validate`-equivalent (`eas config` reaches the real
"log in" step, not a parse error) but not yet exercised against a real
Expo account, since none exists for this project yet (docs/ROADMAP.md
A6). One-time setup once one does:

```bash
cd driver-app
npx eas-cli login                # or set EXPO_TOKEN for CI - see .github/workflows/eas-build.yml
npx eas-cli init                 # creates the real project, writes app.json's extra.eas.projectId
```

That second step is also what closes the one open gap from push
notifications (docs/ROADMAP.md A1) - `Notifications.getExpoPushTokenAsync()`
needs a real project id to mint a real token, and
`src/notifications/registerForPushNotifications.ts` already checks for
exactly this field and no-ops until it's there. No code change needed
either way; `eas init` is the whole unlock.

After that, fill in `eas.json`'s `submit.production` placeholders
(`REPLACE_WITH_REAL_...`) once real Apple/Google developer accounts
exist, and `.github/workflows/eas-build.yml` (manual dispatch, or push a
`driver-app-vX` tag) builds a real binary via `EXPO_TOKEN` (a repo
secret) with no further setup.

## Structure

```
src/
  api/        typed client + response types (mirrors app/schemas/driver_*.py)
  auth/       token storage + current-driver context
  components/ shared UI primitives
  navigation/ stack definitions + the signed-out/setup/signed-in switch
  screens/    one file per wireframe screen (or merged group, noted above)
```
