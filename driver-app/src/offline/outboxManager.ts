import NetInfo from '@react-native-community/netinfo';

import type { DockSurveyBody } from '../api/types';
import { api, ApiError } from '../api/client';
import { adoptAuthToken } from '../auth/token';
import { loadOutbox, saveOutbox } from './outboxStore';
import type { OutboxActionType, OutboxItem } from './types';

const BACKOFF_STEPS_MS = [2000, 5000, 15000, 30000, 60000];

// 4xx statuses that are the server asking us to come back rather than telling
// us the request was wrong. Retrying these is the whole point; treating a 401
// as permanent is what lost a shift's stop events.
const RETRYABLE_4XX = new Set([401, 408, 429]);

/**
 * Is this failure worth giving up on?
 *
 * Exported and pure so it can be tested without standing up a queue, a
 * network stub and a clock — the classification *is* the bug this fixes, and
 * it deserves to be checkable on its own.
 *
 * - **Not an `ApiError`**: the request never reached the server. Transient.
 * - **5xx**: the server fell over. Transient.
 * - **401 / 408 / 429**: the server is asking us to come back — refresh the
 *   token, wait, slow down. Transient, and 401 is the one whose
 *   misclassification cost a shift's stop events (DRV-4).
 * - **Any other 4xx**: a business-rule rejection. "Not all parcels scanned
 *   yet" returns the identical error however many times it is retried, so the
 *   item stays in the queue with `lastError` set for the driver to notice
 *   rather than retrying for ever.
 */
export function isPermanentFailure(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.status < 400 || err.status >= 500) return false;
  return !RETRYABLE_4XX.has(err.status);
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// Driver actions (arrive/scan/complete/flag) write here first - the UI
// updates optimistically (see applyOptimistic.ts) without waiting for the
// network, and this drains to the real API in the background whenever
// connectivity is available. Singleton, not a hook, since it needs to keep
// running/retrying even while no screen using it is mounted.
class OutboxManager {
  private items: OutboxItem[] = [];
  private listeners = new Set<(items: OutboxItem[]) => void>();
  private flushing = false;
  private initialized = false;

  async init(): Promise<void> {
    if (this.initialized) return;
    this.initialized = true;
    this.items = await loadOutbox();
    this.notify();
    NetInfo.addEventListener((state) => {
      if (state.isConnected && state.isInternetReachable !== false) this.flush();
    });
    this.flush();
  }

  /**
   * How many actions have not reached the server yet.
   *
   * For the one caller that has to ask before doing something irreversible:
   * signing out, or revoking the session these items would be sent under.
   * Clearing the token does not empty this queue - it strands it, because
   * every subsequent flush 401s and `refreshOnce` has nothing to refresh
   * with. `flush` treats a 401 as transient precisely so a shift is not lost
   * (DRV-4), and that reasoning only holds while a driver can still sign in.
   */
  pendingCount(): number {
    return this.items.length;
  }

  subscribe(fn: (items: OutboxItem[]) => void): () => void {
    this.listeners.add(fn);
    fn(this.items);
    return () => this.listeners.delete(fn);
  }

  async enqueue(type: OutboxActionType, stopId: string, payload: Record<string, unknown>): Promise<void> {
    if (type === 'scan') {
      // Coalesce: only the latest absolute scanned_count matters, so
      // several rapid taps while offline collapse into one queued item
      // instead of N separate requests once connectivity returns.
      const idx = this.items.findIndex((i) => i.type === 'scan' && i.stopId === stopId);
      if (idx !== -1) {
        this.items = this.items.map((i, n) =>
          n === idx ? { ...i, payload, nextAttemptAt: new Date().toISOString() } : i,
        );
        await this.persistAndNotify();
        this.flush();
        return;
      }
    } else if (this.items.some((i) => i.type === type && i.stopId === stopId)) {
      // arrive/complete/flag are one-shot state transitions - don't queue
      // the same transition twice for the same stop.
      return;
    }

    this.items = [
      ...this.items,
      {
        id: makeId(),
        type,
        stopId,
        payload,
        attempts: 0,
        lastError: null,
        nextAttemptAt: new Date().toISOString(),
      },
    ];
    await this.persistAndNotify();
    this.flush();
  }

  async flush(): Promise<void> {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const net = await NetInfo.fetch();
      if (!net.isConnected || net.isInternetReachable === false) return;

      // Preserve per-stop order: never run a stop's later action ahead of
      // its own earlier failure (e.g. don't attempt "complete" before a
      // still-failing "arrive" for the same stop has landed).
      const failedStopIds = new Set<string>();
      for (const item of [...this.items]) {
        if (failedStopIds.has(item.stopId)) continue;
        if (item.permanentlyFailed) continue;
        if (new Date(item.nextAttemptAt).getTime() > Date.now()) continue;

        try {
          await this.send(item);
          this.items = this.items.filter((i) => i.id !== item.id);
          await this.persistAndNotify();
        } catch (err) {
          failedStopIds.add(item.stopId);
          // A 4xx from the server is usually a business-rule rejection
          // ("not all parcels scanned yet") that will return the exact same
          // error however many times it is retried - mark it permanently
          // failed rather than retrying forever. It stays in the queue with
          // lastError set (SyncStatusPill's warning state) instead of
          // vanishing, since a driver needs to notice it didn't go through.
          // A network-level failure (no response reached at all) is presumed
          // transient - keep retrying with backoff once connectivity returns.
          //
          // **401 is the exception, and treating it as permanent lost a
          // shift's work.** This is DRV-4's done-when exactly: "a full shift
          // with no signal loses no stop events". Queue up a day of stops in a
          // dead zone, have the access token expire while you are down there,
          // come back into signal - and every queued item 401s at once, gets
          // marked permanent, and is never sent. A stale token is the textbook
          // transient failure: it is fixed by refreshing, which is what
          // `refreshOnce` below does before the next pass.
          //
          // 408 and 429 are retryable for the same reason - the server is
          // telling us to come back, not that the request was wrong.
          const isPermanent = isPermanentFailure(err);
          const attempts = item.attempts + 1;
          const backoffMs = BACKOFF_STEPS_MS[Math.min(attempts - 1, BACKOFF_STEPS_MS.length - 1)];
          this.items = this.items.map((i) =>
            i.id === item.id
              ? {
                  ...i,
                  attempts,
                  lastError: err instanceof Error ? err.message : 'Unknown error',
                  nextAttemptAt: new Date(Date.now() + backoffMs).toISOString(),
                  permanentlyFailed: isPermanent,
                }
              : i,
          );
          await this.persistAndNotify();

          // A 401 means every remaining item will 401 too. Refresh once and
          // stop the pass: the next flush goes out with a live token rather
          // than burning the whole queue's retry budget against a dead one.
          if (err instanceof ApiError && err.status === 401) {
            await this.refreshOnce();
            break;
          }

          // A network-level failure (not an ApiError, i.e. the request
          // never reached the server) means the rest of the queue will
          // fail identically right now - stop this pass instead of
          // burning through every item's retry budget at once.
          if (!(err instanceof ApiError)) break;
        }
      }
    } finally {
      this.flushing = false;
    }
  }

  /**
   * Swap a stale access token for a live one, best-effort.
   *
   * The queued stop events are the point: without this, excluding 401 from
   * "permanent" would only mean retrying forever against a token that is
   * never going to work. Failure is silent on purpose - the driver is offline
   * or genuinely signed out, and either way the items stay queued and
   * retryable rather than being thrown away.
   */
  private async refreshOnce(): Promise<void> {
    try {
      const { access_token } = await api.refreshToken();
      await adoptAuthToken(access_token);
    } catch {
      // Left for the next flush. The items are still in the queue.
    }
  }

  private send(item: OutboxItem): Promise<unknown> {
    switch (item.type) {
      case 'arrive':
        return api.arriveAtStop(item.stopId);
      case 'scan':
        return api.scanParcels(item.stopId, item.payload.scannedCount as number);
      case 'complete':
        // Safe to retry blindly - app/api/driver_routes.py's complete_stop
        // is idempotent (a retry after a successful completion returns
        // the existing result instead of a 409).
        return api.completeStop(item.stopId, item.payload as Parameters<typeof api.completeStop>[1]);
      case 'flag':
        return api.flagStop(item.stopId, item.payload as Parameters<typeof api.flagStop>[1]);
      case 'hub_geofence':
        // DRV-3. The server takes the hub from the driver's token, so the id
        // carried here is only the outbox's own key - a phone that could name
        // its hub could name somebody else's.
        return api.recordHubGeofenceEvents([
          item.payload as { kind: 'enter' | 'exit'; occurred_at: string },
        ]);
      case 'geofence':
        // One crossing per item. The endpoint takes a batch and the server
        // de-duplicates on (stop, kind, occurred_at), so a retry after a
        // response we never saw is a no-op rather than a second arrival.
        return api.recordGeofenceEvents(item.stopId, [
          item.payload as { kind: 'enter' | 'exit'; occurred_at: string },
        ]);
      case 'collect-return':
        // Safe to retry. The server recognises an identical ad-hoc manifest on
        // the same stop as this request arriving twice - before that it created
        // a second core, and a phantom core is a part a shop is owed that was
        // never in the van.
        return api.collectReturn(item.stopId, item.payload.manifest as string | undefined);
      case 'return-not-ready':
        return api.returnNotReady(item.stopId);
      case 'return-to-shop':
        return api.returnToShop(item.stopId);
      case 'survey':
        // Safe to retry: the server overwrites the same columns on the same
        // dock and re-stamps `surveyed_at`. It cannot create a second profile -
        // `profile_for` is keyed on the canonical dock.
        return api.recordDockSurvey(item.stopId, item.payload as DockSurveyBody);
    }
  }

  private async persistAndNotify(): Promise<void> {
    await saveOutbox(this.items);
    this.notify();
  }

  private notify(): void {
    this.listeners.forEach((fn) => fn(this.items));
  }
}

export const outboxManager = new OutboxManager();
