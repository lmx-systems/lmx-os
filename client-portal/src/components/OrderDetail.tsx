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
          {/* The four times a client actually argues about, in the order they happen.
              Two are commitments and two are not, and they are labelled so that reads
              off the screen - the previous version showed a collect-by with no way to
              check it and never showed the delivery target that a service-level credit
              is assessed against. */}
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Collect by</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.collect_by)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Collected</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.collected_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Delivery promised by</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">
              {order.promised_delivery_by ? (
                formatDate(order.promised_delivery_by)
              ) : (
                <span title="No service level is on file for this tier, so nothing is promised.">
                  —
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">
              {order.delivered_at ? 'Delivered' : 'Expected'}
            </dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">
              {order.delivered_at
                ? formatDate(order.delivered_at)
                : order.estimated_delivery_by
                  ? `~${formatDate(order.estimated_delivery_by)}`
                  : '—'}
            </dd>
          </div>
          {/* Their customer's own words, when there are any. Shown as a score out of
              five and the comment verbatim - React escapes it, and it arrives from an
              unauthenticated page, so it is never rendered as markup. */}
          {order.rating && (
            <div className="col-span-2">
              <dt className="text-xs text-[var(--text-muted)]">
                Recipient rating &middot; {formatDate(order.rating.submitted_at)}
              </dt>
              <dd className="mt-0.5 text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">
                  {order.rating.score} / 5
                </span>
                {order.rating.comment && (
                  <span className="mt-1 block italic">&ldquo;{order.rating.comment}&rdquo;</span>
                )}
              </dd>
            </div>
          )}
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
