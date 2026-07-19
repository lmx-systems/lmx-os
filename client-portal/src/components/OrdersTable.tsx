import type { ClientOrderSummaryView } from '../lib/types'
import { formatCents, formatDate, formatStatus } from '../lib/format'
import { TierBadge } from './TierBadge'

interface OrdersTableProps {
  orders: ClientOrderSummaryView[]
  onSelect: (orderId: string) => void
}

export function OrdersTable({ orders, onSelect }: OrdersTableProps) {
  if (orders.length === 0) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-8 text-center text-sm text-[var(--text-muted)]">
        No orders yet.
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
            <th className="px-4 py-2.5">Order</th>
            <th className="px-4 py-2.5">Tier</th>
            <th className="px-4 py-2.5">Shop</th>
            <th className="px-4 py-2.5">Status</th>
            <th className="px-4 py-2.5">Requested</th>
            <th className="px-4 py-2.5">Delivered</th>
            <th className="px-4 py-2.5 text-right">Fee</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order) => (
            <tr
              key={order.order_id}
              onClick={() => onSelect(order.order_id)}
              className="cursor-pointer border-b border-[var(--border)] last:border-0 hover:bg-[var(--surface-2)]"
            >
              <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">{order.external_order_ref}</td>
              <td className="px-4 py-2.5">
                <TierBadge tier={order.sla_tier} />
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{order.shop_name ?? '—'}</td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatStatus(order.status)}</td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(order.requested_at)}</td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(order.delivered_at)}</td>
              <td className="px-4 py-2.5 text-right font-medium text-[var(--text-primary)]">
                {formatCents(order.fee_cents)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
