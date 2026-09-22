import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';

import { outboxManager } from '../offline/outboxManager';
import type { Stop } from '../api/types';
import {
  GEOFENCE_RADIUS_M,
  MAX_MONITORED_REGIONS,
  hubIdFromRegion,
  hubRegionIdentifier,
  isHubRegion,
  regionsForRoute,
} from './geofenceWindow';

// Re-exported so callers and tests have one import site for the sensor.
export { GEOFENCE_RADIUS_M, MAX_MONITORED_REGIONS, regionsForRoute };

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
 * The same, for the warehouse fence (DRV-3).
 *
 * A separate outbox action because it posts to a different endpoint, not
 * because the crossing differs. Everything else about it is identical: the
 * device's clock, queued rather than posted, idempotent on replay.
 */
async function queueHubCrossing(hubId: string, kind: Crossing, at: Date): Promise<void> {
  await outboxManager.enqueue('hub_geofence', hubId, {
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
  const kind: Crossing | null =
    eventType === Location.GeofencingEventType.Enter
      ? 'enter'
      : eventType === Location.GeofencingEventType.Exit
        ? 'exit'
        : null;
  if (kind === null) {
    return;
  }

  // One region set carries both sensors, distinguished by identifier - see
  // `HUB_REGION_PREFIX`. A second geofencing task would fight this one for the
  // per-app region cap, or replace it outright.
  if (isHubRegion(region.identifier)) {
    await queueHubCrossing(hubIdFromRegion(region.identifier), kind, at);
  } else {
    await queueCrossing(region.identifier, kind, at);
  }
});

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
export async function syncStopGeofences(
  stops: Stop[],
  hub?: { id: string; lat: number; lng: number } | null,
): Promise<void> {
  const regions = regionsForRoute(stops);

  // The warehouse rides along in the same set (DRV-3). Registered whenever the
  // driver is on duty, not only when they have a route: a driver back in the
  // yard with nothing assigned is exactly the turnaround worth measuring, and
  // that is the moment a route-only fence would be switched off.
  if (hub) {
    regions.push({
      identifier: hubRegionIdentifier(hub.id),
      latitude: hub.lat,
      longitude: hub.lng,
      radius: GEOFENCE_RADIUS_M,
      notifyOnEnter: true,
      notifyOnExit: true,
    });
  }

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
