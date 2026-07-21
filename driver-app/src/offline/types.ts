export type OutboxActionType = 'arrive' | 'scan' | 'complete' | 'flag';

export interface OutboxItem {
  id: string;
  type: OutboxActionType;
  stopId: string;
  // arrive: {}; scan: {scannedCount}; complete: CompleteStopBody-shaped;
  // flag: {reason, note?} - kept loose here since each type's shape is
  // only ever read by outboxManager.send(), not by UI code.
  payload: Record<string, unknown>;
  attempts: number;
  lastError: string | null;
  nextAttemptAt: string; // ISO
  // A 4xx response (business-rule rejection, stale auth) - retrying would
  // return the identical error every time, so flush() stops attempting it.
  permanentlyFailed?: boolean;
}
