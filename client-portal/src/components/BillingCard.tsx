import { useEffect, useState } from 'react'
import { api, downloadInvoicePdf } from '../lib/api'
import type { StatementView } from '../lib/types'

function dollars(cents: number): string {
  return `$${(cents / 100).toLocaleString('en-US', { minimumFractionDigits: 2 })}`
}

function tierLabel(tier: string): string {
  return tier === 'HOT_SHOT' ? 'Hot Shot' : tier
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/**
 * Current-month billing statement (roadmap item C3) - delivered orders
 * grouped by tier/rate, with the invoice PDF download. Data comes from
 * GET /client/billing/statements/{year}/{month}; ops sees the identical
 * numbers via the admin endpoint.
 */
export function BillingCard() {
  const now = new Date()
  const [year, setYear] = useState(now.getUTCFullYear())
  const [month, setMonth] = useState(now.getUTCMonth() + 1)
  const [statement, setStatement] = useState<StatementView | null>(null)
  const [error, setError] = useState(false)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setStatement(null)
    setError(false)
    api
      .myStatement(year, month)
      .then((result) => {
        if (!cancelled) setStatement(result)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
    return () => {
      cancelled = true
    }
  }, [year, month])

  function shiftMonth(delta: number) {
    const shifted = new Date(Date.UTC(year, month - 1 + delta, 1))
    setYear(shifted.getUTCFullYear())
    setMonth(shifted.getUTCMonth() + 1)
  }

  async function handleDownload() {
    setDownloading(true)
    try {
      await downloadInvoicePdf(year, month)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-[14.5px] font-medium text-[var(--text-primary)]">Billing</h2>
        <div className="flex-1" />
        <button
          onClick={() => shiftMonth(-1)}
          aria-label="Previous month"
          className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
        >
          ←
        </button>
        <span className="min-w-[120px] text-center text-[13px] font-medium text-[var(--text-secondary)]">
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button
          onClick={() => shiftMonth(1)}
          aria-label="Next month"
          className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
        >
          →
        </button>
      </div>

      <div className="p-4 text-[13px]">
        {error && <p className="text-[var(--text-muted)]">Couldn't load this statement.</p>}
        {!error && !statement && <p className="text-[var(--text-muted)]">Loading…</p>}
        {statement && (
          <>
            {statement.lines.length === 0 ? (
              <p className="text-[var(--text-muted)]">No billable deliveries this month.</p>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                    <th className="pb-2 font-semibold">Tier</th>
                    <th className="pb-2 text-right font-semibold">Rate/drop</th>
                    <th className="pb-2 text-right font-semibold">Deliveries</th>
                    <th className="pb-2 text-right font-semibold">Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => (
                    <tr key={`${line.sla_tier}-${line.rate_per_drop_cents}`} className="border-t border-[var(--border)]">
                      <td className="py-1.5 text-[var(--text-primary)]">{tierLabel(line.sla_tier)}</td>
                      <td className="py-1.5 text-right text-[var(--text-secondary)]">{dollars(line.rate_per_drop_cents)}</td>
                      <td className="py-1.5 text-right text-[var(--text-secondary)]">{line.order_count}</td>
                      <td className="py-1.5 text-right font-medium text-[var(--text-primary)]">{dollars(line.subtotal_cents)}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-[var(--border-strong)]">
                    <td colSpan={3} className="py-2 text-right font-semibold text-[var(--text-primary)]">
                      Total
                    </td>
                    <td className="py-2 text-right font-semibold text-[var(--text-primary)]">
                      {dollars(statement.total_cents)}
                    </td>
                  </tr>
                </tbody>
              </table>
            )}

            {statement.unbilled_order_count > 0 && (
              <p className="mt-3 rounded-[var(--radius)] bg-[var(--amber-dim,#fdf1de)] px-3 py-2 text-xs text-[var(--amber,#a15c07)]">
                {statement.unbilled_order_count} delivered order(s) this month have no billing rate
                configured yet and are not included in the total. LMX will follow up.
              </p>
            )}

            {statement.lines.length > 0 && (
              <button
                onClick={handleDownload}
                disabled={downloading}
                className="mt-4 rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
              >
                {downloading ? 'Preparing…' : 'Download invoice PDF'}
              </button>
            )}
          </>
        )}
      </div>
    </section>
  )
}
