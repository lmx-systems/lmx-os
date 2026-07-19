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
}

export function formatStatus(status: string): string {
  return STATUS_LABEL[status] ?? status
}
