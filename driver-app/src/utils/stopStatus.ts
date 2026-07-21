import type { Stop } from '../api/types';

// A stop's terminal statuses - once here, it never transitions again (see
// the matching _TERMINAL_STOP_STATUSES on the backend, app/api/driver_routes.py).
export function isStopTerminal(stop: Stop): boolean {
  return stop.status === 'completed' || stop.status === 'failed';
}

export function stopLabel(stop: Stop): string {
  return stop.stop_type === 'pickup' ? stop.shop_name || 'Pickup' : stop.address || 'Drop-off';
}

export type StopAction =
  | { kind: 'arrive' }
  | { kind: 'scan'; scanned: number; total: number }
  | { kind: 'confirmDelivery' }
  | { kind: 'done' };

// Drives StopDetailScreen's single action button (and a label preview on
// TodayRouteScreen's current-stop card) from the stop's own state, per the
// wireframe's "one button reflects the stop's state" stop-detail approach -
// collapsing what used to be three separate screens (arrive/scan/POD) into
// one state machine instead of three navigation steps.
export function primaryActionForStop(stop: Stop): StopAction {
  if (isStopTerminal(stop)) return { kind: 'done' };
  if (stop.status === 'pending' || stop.status === 'en_route') return { kind: 'arrive' };
  // status === 'arrived'
  if (stop.stop_type === 'pickup' && stop.scanned_count < stop.parcel_count) {
    return { kind: 'scan', scanned: stop.scanned_count, total: stop.parcel_count };
  }
  return { kind: 'confirmDelivery' };
}

export function primaryActionLabel(action: StopAction): string {
  switch (action.kind) {
    case 'arrive':
      return 'Arrived';
    case 'scan':
      return `Scan (${action.scanned}/${action.total})`;
    case 'confirmDelivery':
      return 'Confirm delivery';
    case 'done':
      return 'Done';
  }
}
