import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';

import { outboxManager } from '../offline/outboxManager';
import type { Stop } from '../api/types';

/**
 * Arrive/depart per stop, from a geofence rather than a tap (DRV-1).
 *
 * `reportDriverLocation.ts` answers "where is this driver now" for the ops
 * map. This answers a different question - "when exactly did this stop start
 * and end" - and the two must not be confused. §2.2(a) of the 1.5 roadmap
 * chose fences over a position breadcrumb deliberately: we need the two edges
 * of a stop, not the path between them, and "we record when you arrive at and
 * leave a delivery address, not where you are in between" is a far easier
 * consent ask when the drivers belong to the customer.
 *
 * Why this exists at all: the design partner's dispatch export is
 * minute-resolution, so 65.5% of its stops compute to exactly zero dwell. The
 * one second-precision export shows those same stops really took 3-50 seconds.
 * No filter recovers that - only a new sensor does.
 */

export const STOP_GEOFENCE_TASK = 'lmx-stop-geofence';

/**
 * iOS monitors at most 20 regions per app, process-wide. The design partner's
 * best driver runs 20.2 stops per route, so a route does not fit and never
 * will - rolling registration is not an optimisation, it is the only way this
 * works at all (§2.2a).
 *
 * Eighteen rather than twenty leaves headroom: the cap is per *app*, and
 * anything else that ever registers a region - a warehouse fence for DRV-3,
 * say - would otherwise silently evict a stop. Silently is the problem; iOS
 * does not tell you which region it dropped.
 */
export const MAX_MONITORED_REGIONS = 18;

/**
 * Metres. The number that decides how good the measurement is, and it is a
 * genuine trade-off rather than a tuned value:
 *
 *   too small - GPS noise means the fence never fires, and a missed crossing
 *               is a stop with no arrival at all
 *   too large - the fence fires while the van is still down the street, and
 *               "arrival" is stamped early
 *
 * Both platforms are unreliable below roughly 100m, which is why that is the
 * usual advice. 75m is a deliberate compromise and **is not yet calibrated
 * against anything** - no field data exists. The dwell bias partly cancels,
 * since enter and exit are both early by roughly the same walk, but the
 * arrival timestamp itself is not self-correcting. Revisit once DRV-1 has run
 * a real route: compare fence crossings against driver taps and see how far
 * apart they actually land.
 */
export const GEOFENCE_RADIUS_M = 75;

type Crossing = 'enter' | 'exit';

/**
 * Queue a crossing for delivery (DRV-4's outbox).
 *
 * Never posted directly. A geofence fires exactly when a driver is most likely
 * to be somewhere with no signal - inside a building, in a loading bay - and
 * the existing location pings are fire-and-forget precisely because losing one
 * does not matter. Losing a stop event does: it is the measurement.
 *
 * The device's own clock is sent as `occurred_at`, so a crossing that waits
 * out a dead zone still lands at the moment it happened rather than collapsing
 * onto the reconnect.
 */
async function queueCrossing(stopId: string, kind: Crossing, at: Date): Promise<void> {
  await outboxManager.enqueue('geofence', stopId, {
    kind,
    occurred_at: at.toISOString(),
  });
}

/**
 * Defined at module scope, which the OS requires: iOS relaunches the app into
 * the background to deliver a region event, and the task has to already exist
 * by the time the JS bundle finishes evaluating. Registering it inside a
 * component would mean the crossings that matter most - the ones that arrive
 * while the app is not running - are delivered to nothing.
 */
TaskManager.defineTask(STOP_GEOFENCE_TASK, async ({ data, error }) => {
  if (error) {
    // Nothing useful to do from a background task, and throwing would give the
    // OS a reason to stop waking us. The next crossing still fires.
    return;
  }
  const { eventType, region } = (data ?? {}) as {
    eventType?: Location.GeofencingEventType;
    region?: Location.LocationRegion;
  };
  if (!region?.identifier) {
    return;
  }

  // The crossing happened now, by this device's clock. There is no timestamp
  // on the region event itself.
  const at = new Date();
  if (eventType === Location.GeofencingEventType.Enter) {
    await queueCrossing(region.identifier, 'enter', at);
  } else if (eventType === Location.GeofencingEventType.Exit) {
    await queueCrossing(region.identifier, 'exit', at);
  }
});

/** Stops that still need measuring, nearest in the route order first. */
function pendingStops(stops: Stop[]): Stop[] {
  return stops
    .filter((stop) => stop.status !== 'completed' && stop.status !== 'failed')
    .sort((a, b) => a.sequence - b.sequence);
}

/**
 * The regions to monitor right now: the next `MAX_MONITORED_REGIONS` stops.
 *
 * Exported for testing. The window is what makes a 25-stop route work against
 * a 20-region cap, and it is worth being able to assert on directly rather
 * than through a native module that cannot run in a test.
 */
export function regionsForRoute(stops: Stop[]): Location.LocationRegion[] {
  return pendingStops(stops)
    .slice(0, MAX_MONITORED_REGIONS)
    .map((stop) => ({
      identifier: stop.stop_id,
      latitude: stop.lat,
      longitude: stop.lng,
      radius: GEOFENCE_RADIUS_M,
      notifyOnEnter: true,
      notifyOnExit: true,
    }));
}

/**
 * Re-register the rolling window for the current route.
 *
 * Called whenever the route changes - a stop completed, a stop was inserted
 * mid-route - so the window advances with the driver. `startGeofencingAsync`
 * replaces the whole set rather than adding to it, which is what makes this
 * safe to call repeatedly.
 *
 * Best-effort throughout, the same convention as `reportDriverLocation` and
 * `registerForPushNotifications`: a driver who has denied background location
 * gets a fully working app that falls back to tapping arrive and complete. The
 * measurement is lost for that shift, not the shift.
 */
export async function syncStopGeofences(stops: Stop[]): Promise<void> {
  const regions = regionsForRoute(stops);
  if (regions.length === 0) {
    await stopStopGeofencing();
    return;
  }

  const { granted } = await Location.getBackgroundPermissionsAsync();
  if (!granted) {
    // Deliberately not requesting here. The "always" tier is asked for once,
    // on a screen that explains it first (docs/BACKGROUND_LOCATION_CONSENT.md
    // §4.4) - a cold prompt mid-route is the most common reason drivers refuse
    // and the most common reason App Store review rejects.
    return;
  }

  try {
    await Location.startGeofencingAsync(STOP_GEOFENCE_TASK, regions);
  } catch {
    // Same reasoning as the permission check: a failure here costs the
    // measurement, not the delivery.
  }
}

/** Stop monitoring. Safe to call when nothing is registered. */
export async function stopStopGeofencing(): Promise<void> {
  try {
    const running = await Location.hasStartedGeofencingAsync(STOP_GEOFENCE_TASK);
    if (running) {
      await Location.stopGeofencingAsync(STOP_GEOFENCE_TASK);
    }
  } catch {
    // Nothing was registered, or the task is unknown to the OS. Either way
    // there is nothing to stop.
  }
}
