export type OutboxActionType =
  | 'arrive'
  | 'scan'
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
  // arrive: {}; scan: {scannedCount}; complete: CompleteStopBody-shaped;
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
