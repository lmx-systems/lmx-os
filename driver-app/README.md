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
- **Scan parcels (1k)**: no camera/barcode SDK - "Scan next parcel" is a
  manual tap that increments a count against the same
  `POST /driver/stops/{id}/scan` endpoint a real scanner would call. Swap
  in `expo-camera`'s barcode scanning without touching the backend.
- **Proof of delivery (1m)**: "tap to capture" records a placeholder
  photo/signature URL rather than actually invoking the camera or a
  signature pad. The PIN field has no field to verify against yet - there's
  no PIN-issuance system server-side (see `app/models/stop.py`).
- **Messaging (1p/1q)**: real masked SMS - sends via
  `app/messaging/sms_client.py`'s `TwilioSmsClient` once a Twilio account
  is provisioned (`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/
  `TWILIO_FROM_NUMBER`); until then, every send goes through
  `StubSmsClient` (logged, not actually delivered) - same "unconfigured ->
  stub" pattern as OTP codes above. The inbound-reply webhook
  (`app/api/webhooks.py`) also has no Twilio request-signature
  verification yet - a real gap to close before pointing a live number at
  it, not just a formatting note.
- **Masked calling** (as opposed to messaging): still not built - would
  need a separate, heavier Twilio Voice/Proxy integration. The "Call"
  button on the active-job screen says so explicitly now.
- **Navigation (1h)**: no turn-by-turn/maps SDK integration - screens 1h,
  1i, and 1l are merged into one `ActiveRouteScreen` showing the current
  stop plus the full stops list, without live turn directions.
- **Earnings (1n/1o)**: real hours-worked data (computed from each
  completed route's timestamps), run through an explicitly-labeled
  placeholder hourly rate - not a real pay formula, and not connected to
  any payroll system (ADP/Gusto - neither is provisioned yet, see
  `docs/NEXT_STEPS.md` item 15). Every earnings response is marked
  `is_placeholder: true` and shown that way in the app.

## Structure

```
src/
  api/        typed client + response types (mirrors app/schemas/driver_*.py)
  auth/       token storage + current-driver context
  components/ shared UI primitives
  navigation/ stack definitions + the signed-out/setup/signed-in switch
  screens/    one file per wireframe screen (or merged group, noted above)
```
