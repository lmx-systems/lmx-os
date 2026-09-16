import type { Stop } from '../api/types';
import {
  GEOFENCE_RADIUS_M,
  MAX_MONITORED_REGIONS,
  pendingStops,
  regionsForRoute,
} from './geofenceWindow';

/**
 * The rolling window is the riskiest logic in DRV-1, and the roadmap calls
 * DRV-1 and DRV-2 "the whole of Phase 1's risk".
 *
 * The failure it exists to prevent is silent: iOS monitors 20 regions per app
 * and drops the excess without saying which. A route longer than the cap that
 * registers naively loses its later stops, and the symptom is not an error -
 * it is a dock that quietly never records an arrival.
 */

function stop(overrides: Partial<Stop> & { stop_id: string; sequence: number }): Stop {
  return {
    stop_type: 'dropoff',
    status: 'pending',
    lat: 30.25,
    lng: -97.74,
    shop_name: null,
    address: null,
    contact_name: null,
    contact_phone: null,
    notes: null,
    parcel_count: 1,
    scanned_count: 0,
    order_ids: [],
    ...overrides,
  } as Stop;
}

function route(count: number, status: Stop['status'] = 'pending'): Stop[] {
  return Array.from({ length: count }, (_, index) =>
    stop({ stop_id: `stop-${index}`, sequence: index, status }),
  );
}

describe('regionsForRoute', () => {
  it('never registers more regions than the platform will monitor', () => {
    // 25 stops is the done-when in the roadmap, and above the iOS cap.
    const regions = regionsForRoute(route(25));
    expect(regions).toHaveLength(MAX_MONITORED_REGIONS);
  });

  it('leaves headroom under the hard cap of 20', () => {
    // The cap is per app, not per task. A warehouse fence (DRV-3) registering
    // later must not evict a stop.
    expect(MAX_MONITORED_REGIONS).toBeLessThan(20);
  });

  it('monitors the stops the driver reaches next, not an arbitrary slice', () => {
    const regions = regionsForRoute(route(25));
    expect(regions.map((r) => r.identifier)).toEqual(
      Array.from({ length: MAX_MONITORED_REGIONS }, (_, i) => `stop-${i}`),
    );
  });

  it('advances as stops finish, so later stops eventually get a fence', () => {
    const stops = route(25);
    // The first ten are done; the window should have moved past them.
    for (let i = 0; i < 10; i += 1) stops[i].status = 'completed';

    const identifiers = regionsForRoute(stops).map((r) => r.identifier);

    expect(identifiers).not.toContain('stop-0');
    expect(identifiers[0]).toBe('stop-10');
    // stop-24 was outside the window before and is inside it now - the whole
    // point of re-registering.
    expect(identifiers).toContain('stop-24');
  });

  it('does not waste a slot on a stop that is already finished', () => {
    const stops = route(5);
    stops[1].status = 'completed';
    stops[3].status = 'failed';

    expect(regionsForRoute(stops).map((r) => r.identifier)).toEqual([
      'stop-0',
      'stop-2',
      'stop-4',
    ]);
  });

  it('orders by the route sequence, not by however the array arrived', () => {
    const shuffled = [
      stop({ stop_id: 'c', sequence: 3 }),
      stop({ stop_id: 'a', sequence: 1 }),
      stop({ stop_id: 'b', sequence: 2 }),
    ];
    expect(regionsForRoute(shuffled).map((r) => r.identifier)).toEqual(['a', 'b', 'c']);
  });

  it('does not reorder the caller\'s own array', () => {
    const stops = [
      stop({ stop_id: 'c', sequence: 3 }),
      stop({ stop_id: 'a', sequence: 1 }),
    ];
    pendingStops(stops);
    expect(stops.map((s) => s.stop_id)).toEqual(['c', 'a']);
  });

  it('carries the stop id as the region identifier', () => {
    // The task handler has nothing else to go on: a crossing arrives with a
    // region identifier and no other context, and it has to name a stop.
    const regions = regionsForRoute([stop({ stop_id: 'the-stop', sequence: 0 })]);
    expect(regions[0].identifier).toBe('the-stop');
  });

  it('watches both edges, because dwell needs two', () => {
    const [region] = regionsForRoute([stop({ stop_id: 's', sequence: 0 })]);
    expect(region.notifyOnEnter).toBe(true);
    expect(region.notifyOnExit).toBe(true);
    expect(region.radius).toBe(GEOFENCE_RADIUS_M);
  });

  it('registers nothing when every stop is done', () => {
    expect(regionsForRoute(route(5, 'completed'))).toEqual([]);
  });

  it('registers nothing for an empty route', () => {
    expect(regionsForRoute([])).toEqual([]);
  });
});
