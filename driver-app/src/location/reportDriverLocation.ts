import * as Location from 'expo-location';

import { api } from '../api/client';

// How often an on-duty driver reports position. 30s is a deliberate middle:
// the optimizer re-plans on events rather than on a timer (see
// app/optimizer/event_trigger.py), so a fresher fix buys nothing on the
// dispatch side, while every ping costs battery and one durable row
// (app/models/driver_location_ping.py). Mid-route insertion is the one thing
// that genuinely wants recency, and 30s is well inside the window where a
// driver hasn't moved far enough to change that decision.
export const LOCATION_PING_INTERVAL_MS = 30_000;

// Don't wake the GPS for movement smaller than this. A driver sitting at a
// counter or stopped at a light would otherwise emit a stream of near-
// identical fixes; the interval above is a ceiling on frequency, this is the
// floor on meaningfulness.
const MINIMUM_DISTANCE_M = 25;

let subscription: Location.LocationSubscription | null = null;

/**
 * Start reporting this driver's position (docs/ROADMAP.md F1).
 *
 * Called when the driver goes on duty and stopped when they go off - NOT at
 * sign-in. That boundary is the point: an off-shift driver is not tracked,
 * which is both the honest reading of consent and what makes the driver-
 * visible scorecard (W4) defensible as "a shared standard, not a camera
 * pointed at me."
 *
 * Foreground-only, on purpose. Background location on iOS requires the
 * "always" authorization tier and an App Store review justification, which
 * is a decision to make deliberately with a real Apple Developer account
 * (docs/ROADMAP.md A6) rather than a permission to request speculatively.
 * The practical consequence is honest and worth knowing: position stops
 * updating when the driver backgrounds the app. Job offers still reach them
 * via push (A1).
 *
 * Best-effort throughout - same convention as
 * notifications/registerForPushNotifications.ts. A driver who declines the
 * permission still gets a fully working app; they just won't appear on the
 * ops map, and the optimizer will skip them for assignment, which is
 * existing behaviour for any driver with no known position rather than a
 * new failure mode introduced here.
 */
export async function startReportingLocation(): Promise<void> {
  if (subscription) {
    return;
  }

  const { status: existingStatus } = await Location.getForegroundPermissionsAsync();
  let status = existingStatus;
  if (status !== 'granted') {
    const requested = await Location.requestForegroundPermissionsAsync();
    status = requested.status;
  }
  if (status !== 'granted') {
    return;
  }

  // Re-check: an await above yields, so two rapid on-duty toggles could both
  // get past the initial guard and leave one subscription orphaned with
  // nothing holding a reference to remove it.
  if (subscription) {
    return;
  }

  subscription = await Location.watchPositionAsync(
    {
      accuracy: Location.Accuracy.Balanced,
      timeInterval: LOCATION_PING_INTERVAL_MS,
      distanceInterval: MINIMUM_DISTANCE_M,
    },
    (position) => {
      // Fire-and-forget. A dropped ping is not worth surfacing to a driver
      // mid-delivery, and the next one is 30 seconds away.
      api
        .reportLocation({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          recorded_at: new Date(position.timestamp).toISOString(),
          accuracy_m: position.coords.accuracy ?? null,
        })
        .catch(() => {});
    },
  );
}

/** Stop reporting position. Safe to call when not started. */
export function stopReportingLocation(): void {
  subscription?.remove();
  subscription = null;
}
