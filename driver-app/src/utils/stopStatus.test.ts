import type { Stop } from '../api/types';
import {
  isStopTerminal,
  primaryActionForStop,
  primaryActionLabel,
} from './stopStatus';

/**
 * The one button on the stop screen, and what it offers.
 *
 * `primaryActionForStop` collapsed three screens into a state machine, so a
 * wrong branch here does not look like a bug — it looks like a stop a driver
 * cannot finish. Untested until now, and it had drifted from
 * `applyPendingToStop`: this module offers "Arrived" from `en_route` and the
 * optimistic overlay only applied it from `pending`, so tapping Arrived at an
 * en-route stop while offline did nothing visible.
 */

function stop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: 's1',
    stop_type: 'dropoff',
    status: 'pending',
    sequence: 1,
    shop_name: 'Riverside Motors',
    address: '14 Oak Ave',
    lat: 30.26,
    lng: -97.74,
    parcel_count: 1,
    scanned_count: 0,
    eta: null,
    cod: [],
    ...overrides,
  } as Stop;
}

describe('what the one button offers', () => {
  it('asks a pending stop to arrive', () => {
    expect(primaryActionForStop(stop({ status: 'pending' }))).toEqual({ kind: 'arrive' });
  });

  it('asks an en-route stop to arrive too', () => {
    // A stop reaches `en_route` on its own (app/delivery/en_route.py), so this
    // is a state a driver really meets - and the one the overlay disagreed
    // about.
    expect(primaryActionForStop(stop({ status: 'en_route' }))).toEqual({ kind: 'arrive' });
  });

  it('asks an arrived pickup to scan while parcels are outstanding', () => {
    const action = primaryActionForStop(
      stop({ status: 'arrived', stop_type: 'pickup', parcel_count: 3, scanned_count: 1 }),
    );

    expect(action).toEqual({ kind: 'scan', scanned: 1, total: 3 });
    expect(primaryActionLabel(action)).toBe('Scan (1/3)');
  });

  it('moves a pickup on once every parcel is scanned', () => {
    expect(
      primaryActionForStop(
        stop({ status: 'arrived', stop_type: 'pickup', parcel_count: 2, scanned_count: 2 }),
      ),
    ).toEqual({ kind: 'confirmDelivery' });
  });

  it('never asks a dropoff to scan', () => {
    // Parcels are scanned into the van at the shop, not out of it at the door.
    expect(
      primaryActionForStop(
        stop({ status: 'arrived', stop_type: 'dropoff', parcel_count: 3, scanned_count: 0 }),
      ),
    ).toEqual({ kind: 'confirmDelivery' });
  });

  it.each(['completed', 'failed'] as const)('offers nothing more on a %s stop', (status) => {
    const s = stop({ status });

    expect(isStopTerminal(s)).toBe(true);
    expect(primaryActionForStop(s)).toEqual({ kind: 'done' });
  });

  it('treats terminal as terminal even mid-scan', () => {
    // A stop failed with parcels outstanding must not offer "Scan" again -
    // the driver has already recorded that it could not be completed.
    expect(
      primaryActionForStop(
        stop({ status: 'failed', stop_type: 'pickup', parcel_count: 3, scanned_count: 1 }),
      ),
    ).toEqual({ kind: 'done' });
  });
});
