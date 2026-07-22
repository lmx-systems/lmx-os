import type { ClientProfileView, InvoiceDetailView } from '../lib/types'
import { formatCents, formatDate, parseCalendarDate } from '../lib/format'
import { TierBadge } from './TierBadge'

interface InvoiceDetailProps {
  invoice: InvoiceDetailView
  profile: ClientProfileView
  onBack: () => void
}

function formatPeriodBoundary(isoDate: string): string {
  return parseCalendarDate(isoDate).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}

// period_end is exclusive (app/billing/service.py) - the invoice should
// read as covering the last actual day of the period, not the boundary
// date itself, which belongs to the *next* statement.
function periodEndInclusive(periodEnd: string): string {
  const d = parseCalendarDate(periodEnd)
  d.setDate(d.getDate() - 1)
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}

export function InvoiceDetail({ invoice, profile, onBack }: InvoiceDetailProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between print:hidden">
        <button
          onClick={onBack}
          className="w-fit text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:text-[var(--text-primary)]"
        >
          ← Back to invoices
        </button>
        <button
          onClick={() => window.print()}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-2)]"
        >
          Print / Save as PDF
        </button>
      </div>

      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-8 print:rounded-none print:border-0 print:p-0">
        <div className="flex items-start justify-between border-b border-[var(--border)] pb-6">
          <div>
            <div className="text-[13px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">Invoice</div>
            <div className="mt-1 text-[22px] font-semibold text-[var(--text-primary)]">
              INV-{String(invoice.invoice_number).padStart(6, '0')}
            </div>
          </div>
          <div className="text-right text-sm text-[var(--text-secondary)]">
            <div className="font-medium text-[var(--text-primary)]">{profile.name}</div>
            <div>{profile.portal_email}</div>
          </div>
        </div>

        <dl className="mt-6 grid grid-cols-3 gap-x-6 gap-y-4 text-sm">
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Billing period</dt>
            <dd className="mt-0.5 font-medium text-[var(--text-primary)]">
              {formatPeriodBoundary(invoice.period_start)} – {periodEndInclusive(invoice.period_end)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Generated</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(invoice.generated_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Deliveries billed</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{invoice.order_count}</dd>
          </div>
        </dl>

        <table className="mt-8 w-full text-left text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
              <th className="py-2">Order</th>
              <th className="py-2">Tier</th>
              <th className="py-2">Shop</th>
              <th className="py-2">Delivered</th>
              <th className="py-2 text-right">Fee</th>
            </tr>
          </thead>
          <tbody>
            {invoice.line_items.map((item) => (
              <tr key={item.order_id} className="border-b border-[var(--border)] last:border-0">
                <td className="py-2.5 font-medium text-[var(--text-primary)]">{item.external_order_ref}</td>
                <td className="py-2.5">
                  <TierBadge tier={item.sla_tier} />
                </td>
                <td className="py-2.5 text-[var(--text-secondary)]">{item.shop_name ?? '—'}</td>
                <td className="py-2.5 text-[var(--text-secondary)]">{formatDate(item.delivered_at)}</td>
                <td className="py-2.5 text-right text-[var(--text-secondary)]">{formatCents(item.fee_cents)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={4} className="pt-4 text-right text-sm font-semibold text-[var(--text-primary)]">
                Total
              </td>
              <td className="pt-4 text-right text-[15px] font-semibold text-[var(--text-primary)]">
                {formatCents(invoice.total_cents)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}
