import type { Stop } from '../api/types';
import { applyPendingToStop } from './applyOptimistic';
import type { OutboxItem } from './types';

/**
 * What the driver sees before the network agrees.
 *
 * This overlay is what makes the app usable in a dead zone, and `DRV-4` just
 * made that path far more load-bearing — a queued action can now sit unsent for
 * a whole shift and still go through. If the overlay does not reflect it, the
 * driver taps again.
 *
 * **The bug these were written for.** `arrive` applied only when the stop read
 * `pending`, while `primaryActionForStop` offers "Arrived" from `en_route` too.
 * A stop reaches `en_route` on its own (`app/delivery/en_route.py`), so a
 * driver tapping Arrived there while offline saw the button do nothing at all
 * — and the obvious response to a button that does nothing is to press it
 * again. Both now share `isBeforeArrival` rather than restating the condition.
 */

function stop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: 's1',
    stop_type: 'pickup',
    status: 'pending',
    sequence: 1,
    shop_name: 'Riverside Motors',
    address: '14 Oak Ave',
    lat: 30.26,
    lng: -97.74,
    parcel_count: 3,
    scanned_count: 0,
    eta: null,
    cod: [],
    ...overrides,
  } as Stop;
}

function item(overrides: Partial<OutboxItem> & { type: OutboxItem['type'] }): OutboxItem {
  return {
    id: 'i1',
    stopId: 's1',
    payload: {},
    attempts: 0,
    lastError: null,
    nextAttemptAt: new Date(0).toISOString(),
    ...overrides,
  };
}

describe('showing a queued action before it sends', () => {
  it('advances a pending stop to arrived', () => {
    const result = applyPendingToStop(stop({ status: 'pending' }), [item({ type: 'arrive' })]);

    expect(result.status).toBe('arrived');
  });

  it('advances an en-route stop to arrived', () => {
    // The regression. This returned the stop unchanged, so the button did
    // nothing and the driver pressed it again.
    const result = applyPendingToStop(stop({ status: 'en_route' }), [item({ type: 'arrive' })]);

    expect(result.status).toBe('arrived');
  });

  it('does not walk an arrived stop backwards', () => {
    const result = applyPendingToStop(stop({ status: 'arrived' }), [item({ type: 'arrive' })]);

    expect(result.status).toBe('arrived');
  });

  it('shows scanned parcels immediately', () => {
    const result = applyPendingToStop(
      stop({ status: 'arrived', scanned_count: 0 }),
      [item({ type: 'scan', payload: { scannedCount: 2 } })],
    );

    expect(result.scanned_count).toBe(2);
  });

  it('never lowers a count the server has already confirmed', () => {
    // A stale queued scan flushing after a fresher server figure must not make
    // a driver rescan parcels already in the van.
    const result = applyPendingToStop(
      stop({ status: 'arrived', scanned_count: 3 }),
      [item({ type: 'scan', payload: { scannedCount: 1 } })],
    );

    expect(result.scanned_count).toBe(3);
  });

  it('applies a whole queue in order', () => {
    const result = applyPendingToStop(stop({ status: 'pending' }), [
      item({ id: 'a', type: 'arrive' }),
      item({ id: 'b', type: 'scan', payload: { scannedCount: 3 } }),
      item({ id: 'c', type: 'complete' }),
    ]);

    expect(result.status).toBe('completed');
    expect(result.scanned_count).toBe(3);
  });

  it('shows a flagged stop as failed', () => {
    const result = applyPendingToStop(stop({ status: 'arrived' }), [item({ type: 'flag' })]);

    expect(result.status).toBe('failed');
  });

  it('ignores queued items belonging to another stop', () => {
    // One queue serves every stop on the route. Overlaying a neighbour's
    // arrival would mark a stop the driver has not reached.
    const result = applyPendingToStop(stop({ stop_id: 's1', status: 'pending' }), [
      item({ stopId: 's2', type: 'arrive' }),
    ]);

    expect(result.status).toBe('pending');
  });

  it('leaves the stop untouched when nothing is queued', () => {
    const original = stop({ status: 'arrived', scanned_count: 2 });

    expect(applyPendingToStop(original, [])).toBe(original);
  });

  it('does not mutate the stop it was given', () => {
    // The caller holds the last server-fetched stop and re-overlays on every
    // render; mutating it would make the overlay cumulative and unremovable.
    const original = stop({ status: 'pending' });

    applyPendingToStop(original, [item({ type: 'arrive' })]);

    expect(original.status).toBe('pending');
  });
});
