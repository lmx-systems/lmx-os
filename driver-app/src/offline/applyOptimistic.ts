import type { Stop } from '../api/types';
import type { OutboxItem } from './types';

// Overlays not-yet-flushed queue items onto the last server-fetched Stop,
// so the UI reflects "arrived"/"scanned"/"completed" optimistically before
// the network confirms. No separate rollback path needed: once flush()
// succeeds and removes an item, the next server refetch already agrees.
export function applyPendingToStop(stop: Stop, pending: OutboxItem[]): Stop {
  let next = stop;
  for (const item of pending) {
    if (item.stopId !== stop.stop_id) continue;
    if (item.type === 'arrive' && next.status === 'pending') {
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
