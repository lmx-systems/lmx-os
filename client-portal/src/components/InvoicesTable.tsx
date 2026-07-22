import type { InvoiceSummaryView } from '../lib/types'
import { formatCents, formatDate, parseCalendarDate } from '../lib/format'

interface InvoicesTableProps {
  invoices: InvoiceSummaryView[]
  onSelect: (invoiceId: string) => void
}

function formatPeriod(periodStart: string, periodEnd: string): string {
  const start = parseCalendarDate(periodStart).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  // period_end is exclusive (app/billing/service.py) - display the last
  // included day, not the boundary itself, so a client never sees a date
  // range that reads like it includes a day nothing was actually billed for.
  const endInclusive = parseCalendarDate(periodEnd)
  endInclusive.setDate(endInclusive.getDate() - 1)
  const end = endInclusive.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  return `${start} – ${end}`
}

export function InvoicesTable({ invoices, onSelect }: InvoicesTableProps) {
  if (invoices.length === 0) {
    return (
      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-8 text-center text-sm text-[var(--text-muted)]">
        No invoices yet.
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
            <th className="px-4 py-2.5">Invoice</th>
            <th className="px-4 py-2.5">Period</th>
            <th className="px-4 py-2.5">Generated</th>
            <th className="px-4 py-2.5">Orders</th>
            <th className="px-4 py-2.5 text-right">Total</th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => (
            <tr
              key={invoice.invoice_id}
              onClick={() => onSelect(invoice.invoice_id)}
              className="cursor-pointer border-b border-[var(--border)] transition-colors duration-150 last:border-0 hover:bg-[var(--surface-2)]"
            >
              <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">
                INV-{String(invoice.invoice_number).padStart(6, '0')}
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">
                {formatPeriod(invoice.period_start, invoice.period_end)}
              </td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(invoice.generated_at)}</td>
              <td className="px-4 py-2.5 text-[var(--text-secondary)]">{invoice.order_count}</td>
              <td className="px-4 py-2.5 text-right font-medium text-[var(--text-primary)]">
                {formatCents(invoice.total_cents)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
