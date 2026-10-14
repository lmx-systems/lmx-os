import type { Stop } from '../api/types';

// A stop's terminal statuses - once here, it never transitions again (see
// the matching _TERMINAL_STOP_STATUSES on the backend, app/api/driver_routes.py).
export function isStopTerminal(stop: Stop): boolean {
  return stop.status === 'completed' || stop.status === 'failed';
}

export function stopLabel(stop: Stop): string {
  if (stop.stop_type === 'pickup') return stop.shop_name || 'Pickup';
  return stop.address || stopTypeLabel(stop);
}

export function stopTypeLabel(stop: Pick<Stop, 'stop_type'>): string {
  switch (stop.stop_type) {
    case 'pickup':
      return 'Pickup';
    case 'visit':
      return 'Site visit';
    default:
      return 'Drop-off';
  }
}

export type StopAction =
  | { kind: 'arrive' }
  | { kind: 'scan'; scanned: number; total: number }
  | { kind: 'confirmDelivery' }
  // A visit (DE-6): nothing to hand over; the door is the job.
  | { kind: 'captureDoor' }
  | { kind: 'done' };

// Drives StopDetailScreen's single action button (and a label preview on
// TodayRouteScreen's current-stop card) from the stop's own state, per the
// wireframe's "one button reflects the stop's state" stop-detail approach -
// collapsing what used to be three separate screens (arrive/scan/POD) into
// one state machine instead of three navigation steps.
/**
 * Statuses a stop can be in before the driver has arrived.
 *
 * Exported so `applyPendingToStop` can share it rather than restate it. They
 * had drifted: this module offered "Arrived" from `en_route`, and the
 * optimistic overlay only applied it from `pending` — so a driver tapping
 * Arrived at an en-route stop while offline saw nothing happen, and could tap
 * again.
 */
export const PRE_ARRIVAL_STATUSES = ['pending', 'en_route'] as const;

export function isBeforeArrival(stop: Pick<Stop, 'status'>): boolean {
  return (PRE_ARRIVAL_STATUSES as readonly string[]).includes(stop.status);
}

export function primaryActionForStop(stop: Stop): StopAction {
  if (isStopTerminal(stop)) return { kind: 'done' };
  if (isBeforeArrival(stop)) return { kind: 'arrive' };
  // status === 'arrived'
  if (stop.stop_type === 'visit') return { kind: 'captureDoor' };
  if (stop.stop_type === 'pickup' && stop.scanned_count < stop.parcel_count) {
    return { kind: 'scan', scanned: stop.scanned_count, total: stop.parcel_count };
  }
  return { kind: 'confirmDelivery' };
}

// The stop type matters for one label: at a pickup nothing is delivered yet, so
// Home's card said "Confirm delivery" to a driver loading the van at a shop.
export function primaryActionLabel(action: StopAction, stopType?: Stop['stop_type']): string {
  switch (action.kind) {
    case 'arrive':
      return 'Arrived';
    case 'scan':
      return `Scan (${action.scanned}/${action.total})`;
    case 'confirmDelivery':
      return stopType === 'pickup' ? 'Confirm collection' : 'Confirm delivery';
    case 'captureDoor':
      return 'Capture the door';
    case 'done':
      return 'Done';
  }
}
