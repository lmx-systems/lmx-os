import type { Stop } from '../api/types';
import { returnsActionForStop } from './stopReturns';

/**
 * Which core button, at which stop, and when.
 *
 * `W1`'s three endpoints have existed since PRs #13–#16 and the driver never
 * had a button for any of them — `StopView` carried no returns field, so the
 * app could not have known when to show one. Found by
 * `tests/test_no_unreachable_routes.py`.
 *
 * The decision is tested and the panel is not, which is this app's standing
 * split: **all of what is tested is pure logic that decides something**
 * (`docs/THE_DRIVER_APP.md` §4). Being wrong here is not cosmetic. A button
 * offered before arrival queues a call the server answers `409`, and
 * `isPermanentFailure` classifies a 4xx as permanent — so the action is not
 * retried, it is discarded, and the driver is told a collection failed.
 */

function stop(overrides: Partial<Stop> = {}): Stop {
  return {
    stop_id: 's1',
    stop_type: 'dropoff',
    status: 'arrived',
    sequence: 1,
    shop_name: 'Larkspur Panel',
    address: '14 Quillon Lane',
    lat: 30.26,
    lng: -97.74,
    parcel_count: 1,
    scanned_count: 0,
    eta: null,
    cod: [],
    returns: null,
    ...overrides,
  } as Stop;
}

describe('what the driver can do about cores here', () => {
  it('offers nothing when the stop carries no returns at all', () => {
    // The overwhelming majority of stops. `returns` is null rather than an
    // empty object precisely so this costs the app nothing to think about.
    expect(returnsActionForStop(stop()).kind).toBe('none');
  });

  it('offers a collection at a dropoff with a core expected', () => {
    const action = returnsActionForStop(
      stop({ returns: { expected_manifests: ['core: alternator'], to_drop_manifests: [] } }),
    );

    expect(action).toEqual({
      kind: 'collect',
      manifests: ['core: alternator'],
      enabled: true,
    });
  });

  it('still offers the panel at a dropoff with nothing expected', () => {
    // A counter person handing over a core nobody predicted is the common case,
    // and the backend has always accepted an ad-hoc manifest for it. Hiding the
    // panel would leave the driver carrying an unrecorded part.
    const action = returnsActionForStop(
      stop({ returns: { expected_manifests: [], to_drop_manifests: [] } }),
    );

    expect(action).toEqual({ kind: 'collect', manifests: [], enabled: true });
  });

  it.each(['pending', 'en_route'] as const)('will not let a driver collect from %s', (status) => {
    // `collect_return` calls `_assert_arrived`. Queueing before arrival is a
    // 409, which the outbox discards rather than retries.
    const action = returnsActionForStop(
      stop({ status, returns: { expected_manifests: ['core: caliper'], to_drop_manifests: [] } }),
    );

    expect(action).toMatchObject({ kind: 'collect', enabled: false });
  });

  it('offers a drop at a pickup whose shop is owed cores', () => {
    const action = returnsActionForStop(
      stop({
        stop_type: 'pickup',
        returns: { expected_manifests: [], to_drop_manifests: ['core: starter'] },
      }),
    );

    expect(action).toEqual({ kind: 'drop', manifests: ['core: starter'] });
  });

  it('offers nothing at a pickup with no cores bound for it', () => {
    const action = returnsActionForStop(
      stop({ stop_type: 'pickup', returns: { expected_manifests: [], to_drop_manifests: [] } }),
    );

    expect(action.kind).toBe('none');
  });

  it('does not gate a drop on arrival, because the server does not', () => {
    // Restating a condition another module owns is what produced both bugs in
    // `applyOptimistic.test.ts`. `return-to-shop` has no arrival check, so
    // adding one here would invent a refusal the server would not make.
    const action = returnsActionForStop(
      stop({
        stop_type: 'pickup',
        status: 'en_route',
        returns: { expected_manifests: [], to_drop_manifests: ['core: starter'] },
      }),
    );

    expect(action.kind).toBe('drop');
  });

  it('never offers both halves at one stop', () => {
    // Only one is ever possible, and the server enforces it — a collect on a
    // pickup is a 409. Rendering both would be offering a refusal.
    const pickup = returnsActionForStop(
      stop({
        stop_type: 'pickup',
        returns: { expected_manifests: ['core: caliper'], to_drop_manifests: ['core: starter'] },
      }),
    );
    const dropoff = returnsActionForStop(
      stop({
        stop_type: 'dropoff',
        returns: { expected_manifests: ['core: caliper'], to_drop_manifests: ['core: starter'] },
      }),
    );

    expect(pickup.kind).toBe('drop');
    expect(dropoff.kind).toBe('collect');
  });
});
