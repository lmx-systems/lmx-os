// `ApiError` lives in the API client, which reaches AsyncStorage through
// serverUrl.ts. The package ships a jest mock for exactly this; without it the
// suite cannot even import the module under test.
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

import { ApiError } from '../api/client';
import { isPermanentFailure } from './outboxManager';

/**
 * DRV-4: *"a full shift with no signal loses no stop events."*
 *
 * The outbox already did the hard part — arrive, scan, complete, flag and
 * geofence crossings all queue to AsyncStorage and survive the app being
 * killed. One line undid it.
 *
 * `isPermanent = status >= 400 && status < 500` treated a **401** as a
 * business-rule rejection. The comment beside it even named "a stale/rejected
 * auth token" as something that "will return the exact same error no matter how
 * many times it's retried" — which is the one thing a stale token does not do.
 *
 * So: queue a day of stops in a dead zone, have the access token expire while
 * you are down there, come back into signal. Every queued item 401s at once,
 * every one is marked permanently failed, and `flush()` skips them for ever
 * (`if (item.permanentlyFailed) continue`). Nothing ever clears the flag. **The
 * whole shift is lost**, sitting visibly in the queue, never to be sent.
 *
 * These test the classification directly. It is a pure function for that
 * reason: the misclassification *is* the bug, and it should be checkable
 * without standing up a queue, a network stub and a clock.
 */
describe('which failures are worth giving up on', () => {
  it('does not give up on a 401 — a stale token is fixed by refreshing', () => {
    expect(isPermanentFailure(new ApiError(401, 'Invalid or expired session'))).toBe(false);
  });

  it.each([408, 429])('does not give up on a %i — the server is asking us to come back', (status) => {
    expect(isPermanentFailure(new ApiError(status, 'later'))).toBe(false);
  });

  it.each([400, 403, 404, 409, 422])(
    'gives up on a %i — the request itself is wrong and will stay wrong',
    (status) => {
      expect(isPermanentFailure(new ApiError(status, 'nope'))).toBe(true);
    },
  );

  it('does not give up on a 5xx — the server fell over, not the request', () => {
    expect(isPermanentFailure(new ApiError(500, 'boom'))).toBe(false);
    expect(isPermanentFailure(new ApiError(503, 'unavailable'))).toBe(false);
  });

  it('does not give up when the request never reached the server', () => {
    expect(isPermanentFailure(new TypeError('Network request failed'))).toBe(false);
    expect(isPermanentFailure(undefined)).toBe(false);
  });

  it('keeps giving up on the rejection this rule exists for', () => {
    // The case the original classification was written for, and still right:
    // "not all parcels scanned yet" returns the identical 409 for ever, so the
    // item stays in the queue with lastError set for the driver to notice.
    expect(isPermanentFailure(new ApiError(409, 'Scan all parcels before completing'))).toBe(
      true,
    );
  });
});
