export type OutboxActionType =
  | 'arrive'
  | 'scan'
  // One real barcode (DE-5). Not coalesced like 'scan': each barcode is its
  // own check against the stop's orders, and the server is idempotent per
  // barcode, so a retry is a no-op rather than a second count.
  | 'scan-parcel'
  // DE-5: one order's item at the pickup counter. The latest edit to an order
  // replaces an unsent earlier one; the server overwrites, so a retry is safe.
  | 'item-facts'
  // DE-4: one guided handoff photo, kept as a file until it is sent. A retake
  // of the same slot replaces an unsent one.
  | 'handoff-photo'
  | 'complete'
  | 'flag'
  | 'geofence'
  // The warehouse fence (DRV-3). Carries no stop - `stopId` holds the hub id,
  // which the server ignores in favour of the driver's token.
  | 'hub_geofence'
  // W1's reverse leg. All three are idempotent on the server, which they had to
  // become before they could be queued: a retried collection used to create a
  // second core.
  | 'collect-return'
  | 'return-not-ready'
  | 'return-to-shop'
  // DRV-7. Idempotent on the server by overwrite: a retry writes the same
  // answers to the same columns rather than creating a second profile.
  | 'survey';

export interface OutboxItem {
  id: string;
  type: OutboxActionType;
  stopId: string;
  // arrive: {} or a stop pin; scan: {scannedCount}; scan-parcel: {barcode};
  // item-facts: {orderId, facts}; handoff-photo: {slot, uri, contentType};
  // complete: CompleteStopBody-shaped;
  // flag: {reason, note?}; geofence: {kind, occurred_at};
  // hub_geofence: {kind, occurred_at}; collect-return: {manifest?};
  // return-not-ready: {}; return-to-shop: {} - kept loose here since each
  // type's shape is only ever read by outboxManager.send(), not by UI code.
  payload: Record<string, unknown>;
  attempts: number;
  lastError: string | null;
  nextAttemptAt: string; // ISO
  // A 4xx response (business-rule rejection, stale auth) - retrying would
  // return the identical error every time, so flush() stops attempting it.
  permanentlyFailed?: boolean;
}
