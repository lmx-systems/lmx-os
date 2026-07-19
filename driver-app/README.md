# LMX Driver App

React Native (Expo) app for drivers - Phase 1 of the driver app build (see
`docs/NEXT_STEPS.md` item 12). Covers the core delivery loop from the
wireframe spec (`LMX Driver App Wireframes.dc.html`, screens 1a-1m):
sign in, vehicle setup, go online, accept a job offer, and run the
pickup -> scan -> dropoff -> proof-of-delivery flow against the real LMX OS
backend (`app/api/driver_routes.py`).

Screens 1n-1r (earnings, messaging/support, full profile) are Phase 2/3 -
not built here. See the root `docs/NEXT_STEPS.md` for the phasing.

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

The backend's driver routes are exempt from `API_SHARED_SECRET`
(`app/security.py`) since they have their own real per-driver auth - no
extra header needed from this app.

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
- **Masked calling / messaging** (referenced on the active-job screen):
  not built - Phase 3, needs a real Twilio/telephony integration.
  Currently shows an inert alert.
- **Navigation (1h)**: no turn-by-turn/maps SDK integration - screens 1h,
  1i, and 1l are merged into one `ActiveRouteScreen` showing the current
  stop plus the full stops list, without live turn directions.
- **Earnings**: not built at all (Phase 2 - no backend for it yet).

## Structure

```
src/
  api/        typed client + response types (mirrors app/schemas/driver_*.py)
  auth/       token storage + current-driver context
  components/ shared UI primitives
  navigation/ stack definitions + the signed-out/setup/signed-in switch
  screens/    one file per wireframe screen (or merged group, noted above)
```
