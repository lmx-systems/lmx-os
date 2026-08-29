import { useState } from 'react'
import { api } from '../lib/api'
import type { ClientOrderSummaryView } from '../lib/types'
import { formatCents, formatDate, formatFailureReason, formatStatus, isFailedStatus } from '../lib/format'
import { TierBadge } from './TierBadge'

interface OrdersTableProps {
  orders: ClientOrderSummaryView[]
  onSelect: (orderId: string) => void
  query: string
  onQueryChange: (value: string) => void
  status: 'open' | 'all'
  onStatusChange: (value: 'open' | 'all') => void
  total: number
  limit: number
  offset: number
  onOffsetChange: (value: number) => void
}

/**
 * A client's orders: searchable, filterable, paged (docs/ROADMAP.md W5).
 *
 * Built for the counter person rather than the owner. CP-3's target is finding one order
 * in ten seconds while a customer is on the phone, which the previous version could not
 * do at any speed - it rendered every order the company had ever placed, in one
 * unsearchable list.
 *
 * The search box is the primary control and is placed first for that reason. It matches
 * our reference, **their** reference, the shop, the contact and the address; their own
 * reference matters most, because that is the number on the paperwork in front of them.
 */
export function OrdersTable({
  orders,
  onSelect,
  query,
  onQueryChange,
  status,
  onStatusChange,
  total,
  limit,
  offset,
  onOffsetChange,
}: OrdersTableProps) {
  const [exporting, setExporting] = useState(false)
  const [exportFailed, setExportFailed] = useState(false)
  const showing = orders.length
  const from = total === 0 ? 0 : offset + 1
  const to = offset + showing

  const controls = (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <input
        type="search"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Search by order number, shop, customer or address"
        aria-label="Search orders"
        className="min-w-[16rem] flex-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
      />
      {/* Exports the whole history rather than what is on screen - a page of a ledger
          is not a ledger, and a client asking for their data means all of it. */}
      <button
        type="button"
        onClick={() => {
          setExporting(true)
          api
            .downloadOrdersCsv()
            .catch(() => setExportFailed(true))
            .finally(() => setExporting(false))
        }}
        disabled={exporting}
        className="rounded-[var(--radius)] border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-secondary)] disabled:opacity-50"
      >
        {exporting ? 'Preparing…' : 'Export CSV'}
      </button>
      <div className="flex overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
        {(['open', 'all'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => onStatusChange(value)}
            aria-pressed={status === value}
            className={`px-3 py-2 text-sm ${
              status === value
                ? 'bg-[var(--accent)] font-medium text-white'
                : 'bg-[var(--surface)] text-[var(--text-secondary)]'
            }`}
          >
            {value === 'open' ? 'In progress' : 'All'}
          </button>
        ))}
      </div>
    </div>
  )

  const exportError = exportFailed ? (
    <p role="alert" className="mb-2 text-xs text-[var(--danger,#b3261e)]">
      We couldn&rsquo;t build that export. Please try again.
    </p>
  ) : null

  const pager =
    total > limit ? (
      <div className="mt-3 flex items-center justify-between text-xs text-[var(--text-muted)]">
        {/* Said explicitly, because a page that does not admit it is one reads as the
            whole list - which is how someone concludes an order is missing. */}
        <span>
          Showing {from}–{to} of {total}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onOffsetChange(Math.max(0, offset - limit))}
            disabled={offset === 0}
            className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-1 disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => onOffsetChange(offset + limit)}
            disabled={to >= total}
            className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-1 disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    ) : null

  if (orders.length === 0) {
    return (
      <div>
        {controls}
        {exportError}
        <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-8 text-center text-sm text-[var(--text-muted)]">
          {/* Three different empty states, because "no orders yet" told a counter person
              whose search simply missed that their company has never sent us anything. */}
          {query
            ? `Nothing matches “${query}”.`
            : status === 'open'
              ? 'Nothing in progress right now.'
              : 'No orders yet.'}
        </div>
      </div>
    )
  }

  return (
    <div>
      {controls}
      {exportError}
      <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
            <th className="px-4 py-2.5">Order</th>
            <th className="px-4 py-2.5">Tier</th>
            <th className="px-4 py-2.5">Shop</th>
            <th className="px-4 py-2.5">Status</th>
            <th className="px-4 py-2.5">Requested</th>
            <th className="px-4 py-2.5">Expected</th>
            <th className="px-4 py-2.5">Delivered</th>
            <th className="px-4 py-2.5 text-right">Fee</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.order_id}
              onClick={() => onSelect(order.order_id)}
              className="cursor-pointer border-b border-[var(--border)] transition-colors duration-150 last:border-0 hover:bg-[var(--surface-2)]"
            >
              <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">{order.external_order_ref}</td>
              <td className="px-4 py-2.5">
                <TierBadge tier={order.sla_tier} />
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{order.shop_name ?? '—'}</td>
              <td className="px-4 py-2.5">
                <span className={isFailedStatus(order.status) ? 'text-[var(--danger,#b4231f)]' : 'text-[var(--text-secondary)]'}>
                  {formatStatus(order.status)}
                </span>
                {order.status === 'delivery_failed' && order.failure_reason && (
                  <div className="text-xs text-[var(--text-muted)]">{formatFailureReason(order.failure_reason)}</div>
                )}
                {order.delivery_attempts > 1 && (
                  <div className="text-xs text-[var(--text-muted)]">Attempt {order.delivery_attempts}</div>
                )}
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(order.requested_at)}</td>
              {/* An ESTIMATE, and worded as one. Once the order is on a driver's route
                  this is the route-aware ETA - the same number the driver and the
                  recipient see - and a straight-line guess before then. Blank once
                  delivered, because the actual time is in the next column and showing
                  both invites reading the estimate as the record. */}
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">
                {order.delivered_at
                  ? '—'
                  : order.estimated_delivery_by
                    ? `~${formatDate(order.estimated_delivery_by)}`
                    : '—'}
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(order.delivered_at)}</td>
              <td className="px-4 py-2.5 text-right font-medium text-[var(--text-primary)]">
                {formatCents(order.fee_cents)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {pager}
    </div>
  )
}
