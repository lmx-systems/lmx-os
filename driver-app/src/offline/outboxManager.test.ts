// `ApiError` lives in the API client, which reaches AsyncStorage through
// serverUrl.ts. The package ships a jest mock for exactly this; without it the
// suite cannot even import the module under test.
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);
// The queue itself runs against stand-ins for the network, the API and the
// phone's storage, so the tests at the bottom can drive it the way a shift does.
jest.mock('@react-native-community/netinfo', () =>
  require('@react-native-community/netinfo/jest/netinfo-mock.js'),
);
jest.mock('./outboxStore', () => ({
  loadOutbox: jest.fn(async () => []),
  saveOutbox: jest.fn(async () => undefined),
}));
jest.mock('../auth/token', () => ({ adoptAuthToken: jest.fn(async () => undefined) }));
jest.mock('../api/client', () => ({
  ...jest.requireActual('../api/client'),
  api: {
    arriveAtStop: jest.fn(),
    scanParcels: jest.fn(),
    completeStop: jest.fn(),
    flagStop: jest.fn(),
    refreshToken: jest.fn(),
  },
}));

import { ApiError, api } from '../api/client';
import { isPermanentFailure, OutboxManager } from './outboxManager';
import type { OutboxItem } from './types';

const mockApi = api as unknown as Record<
  'arriveAtStop' | 'scanParcels' | 'completeStop' | 'flagStop',
  jest.Mock
>;

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

/**
 * What the queue does with an action the server refused, and with one that
 * failed while the phone was online.
 *
 * A refused action sat under "Couldn't sync yet - will retry" for good. It was
 * never going to be retried, nothing could clear it, it kept its stop looking
 * done, and the driver's attempt to do it again was dropped as a duplicate of
 * it. A failure while online wasn't tried again until something else happened.
 */
describe('the queue on a shift', () => {
  // Lets everything the queue started settle. The real setImmediate, because
  // the fake timers stand in only for the queue's own clock.
  const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

  function itemsOf(queue: OutboxManager): OutboxItem[] {
    let seen: OutboxItem[] = [];
    queue.subscribe((items) => {
      seen = items;
    })();
    return seen;
  }

  beforeEach(() => {
    jest.useFakeTimers({ doNotFake: ['setImmediate'] });
    for (const fn of Object.values(mockApi)) fn.mockReset();
  });

  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
  });

  it('sends a redo in place of the action the server rejected', async () => {
    mockApi.completeStop
      .mockRejectedValueOnce(new ApiError(422, 'That PIN is not right'))
      .mockResolvedValueOnce({});
    const queue = new OutboxManager();

    await queue.enqueue('complete', 's1', { method: 'pin', pin: '1111' });
    await settle();
    expect(itemsOf(queue)).toMatchObject([
      { type: 'complete', permanentlyFailed: true, lastError: 'That PIN is not right' },
    ]);

    await queue.enqueue('complete', 's1', { method: 'pin', pin: '4821' });
    await settle();

    expect(mockApi.completeStop).toHaveBeenLastCalledWith('s1', { method: 'pin', pin: '4821' });
    expect(itemsOf(queue)).toEqual([]);
  });

  it('sends the next scan after one was rejected', async () => {
    mockApi.scanParcels
      .mockRejectedValueOnce(new ApiError(409, 'Arrive at the stop before scanning'))
      .mockResolvedValue({});
    const queue = new OutboxManager();

    await queue.enqueue('scan', 's1', { scannedCount: 1 });
    await settle();
    await queue.enqueue('scan', 's1', { scannedCount: 2 });
    await settle();

    expect(mockApi.scanParcels).toHaveBeenLastCalledWith('s1', 2);
    expect(itemsOf(queue)).toEqual([]);
  });

  it('tries a failure again by itself while the phone is online', async () => {
    mockApi.arriveAtStop
      .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
      .mockResolvedValue({});
    const queue = new OutboxManager();

    await queue.enqueue('arrive', 's1', {});
    await settle();
    expect(mockApi.arriveAtStop).toHaveBeenCalledTimes(1);

    // Nothing else happens: no new tap, and the connection doesn't change.
    await jest.advanceTimersByTimeAsync(2000);
    await settle();

    expect(mockApi.arriveAtStop).toHaveBeenCalledTimes(2);
    expect(itemsOf(queue)).toEqual([]);
  });

  it("holds a stop's later action back while its earlier one waits to retry", async () => {
    mockApi.arriveAtStop
      .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
      .mockResolvedValue({});
    mockApi.completeStop.mockResolvedValue({});
    const queue = new OutboxManager();

    await queue.enqueue('arrive', 's1', {});
    await settle();
    await queue.enqueue('complete', 's1', { method: 'photo' });
    await settle();
    // Sent now, the completion would reach a stop the server hasn't seen
    // arrive, and that refusal would be permanent.
    expect(mockApi.completeStop).not.toHaveBeenCalled();

    await jest.advanceTimersByTimeAsync(2000);
    await settle();

    expect(mockApi.arriveAtStop).toHaveBeenCalledTimes(2);
    expect(mockApi.completeStop).toHaveBeenCalledTimes(1);
    expect(mockApi.arriveAtStop.mock.invocationCallOrder[1]).toBeLessThan(
      mockApi.completeStop.mock.invocationCallOrder[0],
    );
    expect(itemsOf(queue)).toEqual([]);
  });

  it('clears a rejected action when dismissed, and nothing still trying', async () => {
    mockApi.flagStop.mockRejectedValueOnce(new ApiError(409, 'Stop is completed, cannot flag'));
    mockApi.arriveAtStop.mockRejectedValue(new ApiError(503, 'unavailable'));
    const queue = new OutboxManager();
    await queue.enqueue('flag', 's1', { reason: 'shop_closed' });
    await settle();
    await queue.enqueue('arrive', 's2', {});
    await settle();
    const [flag, arrive] = itemsOf(queue);

    await queue.dismiss(arrive.id);
    await queue.dismiss(flag.id);

    // The arrival is work the driver did, and it's still on its way.
    expect(itemsOf(queue).map((i) => i.type)).toEqual(['arrive']);
  });

  it('counts for sign-out only what can still be sent', async () => {
    mockApi.flagStop.mockRejectedValueOnce(new ApiError(409, 'Stop is completed, cannot flag'));
    mockApi.arriveAtStop.mockRejectedValue(new ApiError(503, 'unavailable'));
    const queue = new OutboxManager();
    await queue.enqueue('flag', 's1', { reason: 'shop_closed' });
    await settle();
    await queue.enqueue('arrive', 's2', {});
    await settle();

    expect(queue.pendingCount()).toBe(1);
  });
});
