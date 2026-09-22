import type { Stop } from '../api/types';

/**
 * Which stops to monitor right now (DRV-1's rolling window).
 *
 * Deliberately separate from `stopGeofences.ts`, which imports expo-location
 * and expo-task-manager and therefore cannot be loaded outside a native
 * runtime. This module imports nothing but a type, so the one piece of logic
 * that decides whether a 25-stop route is measured correctly can be tested
 * directly rather than through a native module that will not run in a test.
 */

/**
 * iOS monitors at most 20 regions per app, process-wide. The design partner's
 * best driver runs 20.2 stops per route, so a route does not fit and never
 * will - the window is not an optimisation, it is the only way this works
 * (ROADMAP_1.5 §2.2a).
 *
 * Eighteen rather than twenty leaves headroom. The cap is per *app*, so
 * anything else that ever registers a region - DRV-3's warehouse fence - would
 * otherwise silently evict a stop, and iOS does not report which one it
 * dropped.
 */
export const MAX_MONITORED_REGIONS = 18;

/**
 * Metres. The number that decides how good the measurement is, and a genuine
 * trade-off rather than a tuned value:
 *
 *   too small - GPS noise means the fence never fires, and a missed crossing
 *               is a stop with no arrival at all
 *   too large - the fence fires while the van is still down the street, and
 *               "arrival" is stamped early
 *
 * Both platforms are unreliable below roughly 100m, which is the usual advice.
 * 75m is a compromise and **is not calibrated against anything** - no field
 * data exists yet. `app/reporting/geofence_calibration.py` is the measurement
 * that settles it: it compares each crossing against the driver's own tap, so
 * the lead time and the miss rate become numbers instead of guesses.
 */
export const GEOFENCE_RADIUS_M = 75;

/** A circular region, structurally compatible with expo-location's own type. */
export interface StopRegion {
  identifier: string;
  latitude: number;
  longitude: number;
  radius: number;
  notifyOnEnter: boolean;
  notifyOnExit: boolean;
}

/** Stops that still need measuring, in the order the driver will reach them. */
export function pendingStops(stops: Stop[]): Stop[] {
  return stops
    .filter((stop) => stop.status !== 'completed' && stop.status !== 'failed')
    .slice()
    .sort((a, b) => a.sequence - b.sequence);
}

/**
 * The regions to monitor right now: the next `MAX_MONITORED_REGIONS` stops
 * that are not already finished.
 *
 * Called on every route change, so the window advances as stops complete and
 * covers a stop the optimizer inserted mid-route.
 */
export function regionsForRoute(stops: Stop[]): StopRegion[] {
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
 * The identifier prefix that marks the warehouse fence (`DRV-3`).
 *
 * One region set, not two. iOS caps monitored regions per *app*, and
 * `startGeofencingAsync` replaces the whole set — so a second task for the hub
 * would either fight the first for the cap or replace it outright. Instead the
 * hub rides along as one more region and the task tells them apart by
 * identifier.
 *
 * A stop identifier is a UUID, so this prefix cannot collide with one.
 */
export const HUB_REGION_PREFIX = 'hub:'

export function hubRegionIdentifier(hubId: string): string {
  return `${HUB_REGION_PREFIX}${hubId}`
}

export function isHubRegion(identifier: string): boolean {
  return identifier.startsWith(HUB_REGION_PREFIX)
}

export function hubIdFromRegion(identifier: string): string {
  return identifier.slice(HUB_REGION_PREFIX.length)
}
