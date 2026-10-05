import type { Stop } from '../api/types';
import { isBeforeArrival } from '../utils/stopStatus';
import type { OutboxItem } from './types';

// Overlays not-yet-flushed queue items onto the last server-fetched Stop,
// so the UI reflects "arrived"/"scanned"/"completed" optimistically before
// the network confirms. No separate rollback path needed: once flush()
// succeeds and removes an item, the next server refetch already agrees, and
// an item the server rejected stops being overlaid the moment it's rejected.
export function applyPendingToStop(stop: Stop, pending: OutboxItem[]): Stop {
  let next = stop;
  for (const item of pending) {
    if (item.stopId !== stop.stop_id) continue;
    // The server refused it, so it isn't going to happen. Overlaid, a rejected
    // "complete" kept showing the stop as done; the stop shows what the server
    // holds instead, and SyncStatusPill says why.
    if (item.permanentlyFailed) continue;
    if (item.type === 'arrive' && isBeforeArrival(next)) {
      // `isBeforeArrival` rather than `status === 'pending'`. A stop reaches
      // `en_route` on its own (app/delivery/en_route.py), and
      // `primaryActionForStop` offers "Arrived" from there - so restating the
      // condition here let the two drift, and a driver tapping Arrived at an
      // en-route stop while offline saw the button do nothing.
      next = { ...next, status: 'arrived' };
    } else if (item.type === 'scan') {
      const scannedCount = item.payload.scannedCount as number;
      next = { ...next, scanned_count: Math.max(next.scanned_count, scannedCount) };
    } else if (item.type === 'complete') {
      next = { ...next, status: 'completed' };
    } else if (item.type === 'flag') {
      next = { ...next, status: 'failed' };
    }
  }
  return next;
}
