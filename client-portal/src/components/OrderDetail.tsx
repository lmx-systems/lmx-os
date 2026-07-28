import type { ClientOrderDetailView } from '../lib/types'
import { formatCents, formatDate, formatFailureReason, formatStatus, isFailedStatus } from '../lib/format'
import { TierBadge } from './TierBadge'

interface OrderDetailProps {
  order: ClientOrderDetailView
  onBack: () => void
}

export function OrderDetail({ order, onBack }: OrderDetailProps) {
  return (
    <div className="flex flex-col gap-4">
      <button
        onClick={onBack}
        className="w-fit text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:text-[var(--text-primary)]"
      >
        ← Back to orders
      </button>

      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">{order.external_order_ref}</div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">{order.shop_name ?? 'Unknown shop'}</div>
          </div>
          <TierBadge tier={order.sla_tier} />
        </div>

        {isFailedStatus(order.status) && (
          <div className="mt-4 rounded-[var(--radius)] border border-[var(--danger,#b4231f)] bg-[var(--surface-2)] px-3 py-2 text-sm">
            <div className="font-medium text-[var(--danger,#b4231f)]">
              {order.status === 'returned' ? 'Returned to shop' : 'Delivery could not be completed'}
            </div>
            {order.failure_reason && (
              <div className="text-[var(--text-secondary)]">Reason: {formatFailureReason(order.failure_reason)}</div>
            )}
            {order.delivery_attempts > 1 && (
              <div className="text-[var(--text-muted)]">{order.delivery_attempts} delivery attempts</div>
            )}
          </div>
        )}

        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Status</dt>
            <dd className="mt-0.5 font-medium text-[var(--text-primary)]">{formatStatus(order.status)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Fee</dt>
            <dd className="mt-0.5 font-medium text-[var(--text-primary)]">{formatCents(order.fee_cents)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Requested</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.requested_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Delivered</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.delivered_at)}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-[var(--text-muted)]">Delivery address</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{order.delivery_address ?? '—'}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-[var(--text-muted)]">Contact</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{order.delivery_contact_name ?? '—'}</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}
