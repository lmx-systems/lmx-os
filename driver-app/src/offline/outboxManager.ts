import NetInfo from '@react-native-community/netinfo';

import { api, ApiError } from '../api/client';
import { loadOutbox, saveOutbox } from './outboxStore';
import type { OutboxActionType, OutboxItem } from './types';

const BACKOFF_STEPS_MS = [2000, 5000, 15000, 30000, 60000];

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
          // A 4xx from the server (business-rule rejection, e.g. "not all
          // parcels scanned yet", or a stale/rejected auth token) will
          // return the exact same error no matter how many times it's
          // retried - mark it permanently failed rather than retrying
          // forever. It stays in the queue with lastError set
          // (SyncStatusPill's warning state) instead of vanishing, since
          // a driver needs to notice it didn't go through. A network-level
          // failure (no response reached at all) is presumed transient -
          // keep retrying with backoff once connectivity returns.
          const isPermanent = err instanceof ApiError && err.status >= 400 && err.status < 500;
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
