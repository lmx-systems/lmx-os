// Invoice period_start/period_end (app/schemas/billing.py's `date` fields)
// serialize as plain "YYYY-MM-DD" - a calendar date, not a moment in time.
// `new Date("2026-06-01")` parses that as UTC midnight, but
// toLocaleDateString() then renders it in the *browser's local* timezone -
// west of UTC, that silently displays as May 31. Constructing via the
// (year, monthIndex, day) local-timezone Date constructor instead avoids
// the round trip through UTC entirely.
export function parseCalendarDate(isoDate: string): Date {
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(year, month - 1, day)
}

export function formatCents(cents: number | null): string {
  if (cents === null) return '—' // no ClientRate configured for this tier yet - never show $0.00
  return `$${(cents / 100).toFixed(2)}`
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

const STATUS_LABEL: Record<string, string> = {
  received: 'Received',
  classified: 'Classified',
  held: 'Preparing',
  queued: 'Queued',
  assigned: 'Out for delivery',
  delivered: 'Delivered',
  cancelled: 'Cancelled',
  delivery_failed: 'Delivery failed',
  returned: 'Returned to shop',
}

// Human-readable delivery-failure reasons (docs/ROADMAP.md R5). Maps the
// Stop.failure_reason codes surfaced on the order to client-friendly text.
const FAILURE_REASON_LABEL: Record<string, string> = {
  SHOP_CLOSED: 'Shop closed',
  ACCESS_ISSUE: 'Could not access delivery location',
  COD_DISPUTE: 'Payment issue at the door',
  PARTS_MISSING: 'Parts missing',
  REFUSED: 'Delivery refused',
}

export function formatFailureReason(reason: string | null): string | null {
  if (!reason) return null
  return FAILURE_REASON_LABEL[reason] ?? reason
}

export function isFailedStatus(status: string): boolean {
  return status === 'delivery_failed' || status === 'returned'
}

export function formatStatus(status: string): string {
  return STATUS_LABEL[status] ?? status
}

// Return/core statuses (docs/ROADMAP.md W1) - distinct from delivery
// statuses above; a core moves expected -> collected -> returned to shop.
const RETURN_STATUS_LABEL: Record<string, string> = {
  expected: 'Expected',
  ready_for_pickup: 'Ready for pickup',
  collected: 'Collected',
  returned_to_shop: 'Returned to shop',
  not_ready: 'Not ready',
  cancelled: 'Cancelled',
}

export function formatReturnStatus(status: string): string {
  return RETURN_STATUS_LABEL[status] ?? status
}

// The "age" on the awaiting-pickup list (docs/ROADMAP.md W1 slice 4):
// sub-hour shows minutes, under a day shows hours, otherwise days.
export function formatAge(hours: number): string {
  if (hours < 1) return `${Math.max(0, Math.round(hours * 60))}m`
  if (hours < 24) return `${Math.round(hours)}h`
  return `${Math.round(hours / 24)}d`
}
