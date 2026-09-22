import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { CodDisputeReport } from '../lib/types'

/**
 * Which accounts dispute COD, and how often (docs/ROADMAP.md W2).
 *
 * *"A single dispute is a bad afternoon; the same account disputing every month
 * is a commercial problem"* — and it is invisible unless somebody counts. The
 * endpoint existed with nothing calling it
 * (`docs/ROADMAP_AUDIT_2026-09.md`), so the counting happened and nobody saw it.
 *
 * **Grouped by shop, not by client**, which is the endpoint's own decision and
 * the right one: a distributor can have forty branches, and *"your account has a
 * dispute problem"* is not actionable where *"the Riverside branch does"* is.
 *
 * **Rate and counts together, always.** One dispute out of one delivery is a
 * 100% dispute rate and means nothing. Sorting by rate alone would put that
 * account above a branch that disputed nine of forty, which is the conversation
 * actually worth having — so the table is ordered as the endpoint returns it
 * (worst first by its own reckoning) and every rate carries its denominator.
 *
 * **Un-escalated disputes are shown apart from the total**, because they break
 * the promise the feature makes — *"one tap escalates"* — and folded into a
 * total they would disappear. When no SMS provider is configured every dispute
 * is un-escalated by definition: that is one deployment-wide fact, and saying it
 * once is honest where showing it as N pieces of outstanding work is not.
 */

function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

export function CodDisputesPanel({ hubId }: { hubId: string }) {
  const [report, setReport] = useState<CodDisputeReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    if (!hubId) return
    setError(null)
    api
      .codDisputes(hubId)
      .then((r) => live && setReport(r))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  if (error) {
    return (
      <Card title="COD disputes">
        <p className="text-sm text-[var(--red)]">Couldn't load: {error}</p>
      </Card>
    )
  }
  if (!report) return null

  // No disputes at all is the good state and needs no card. A panel that says
  // "nothing here" every day trains a reader to skip it, and this is the one
  // they need to notice on the day it is not empty.
  if (report.disputed_count === 0) return null

  return (
    <Card
      title="COD disputes"
      meta={`${report.disputed_count} of ${report.disputed_count + report.collected_count} collections`}
    >
      <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
        {money(report.disputed_amount_cents)} disputed in the last 30 days. A single
        dispute is a bad afternoon; the same account every month is a commercial
        conversation.
      </p>

      {report.unescalated_count > 0 && (
        <p
          className={`mb-2 text-[12px] ${
            report.sms_configured ? 'text-[var(--amber)]' : 'text-[var(--text-muted)]'
          }`}
        >
          {report.sms_configured ? (
            <>
              {report.unescalated_count} dispute
              {report.unescalated_count === 1 ? ' was' : 's were'} never sent to the
              distributor. The feature promises one tap escalates.
            </>
          ) : (
            <>
              No SMS provider is configured, so all {report.unescalated_count} are
              un-escalated by definition. That is one deployment setting, not{' '}
              {report.unescalated_count} pieces of work.
            </>
          )}
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-[12.5px]">
          <thead>
            <tr className="text-[11px] text-[var(--text-muted)]">
              <th className="py-1 pr-2 font-medium">Account</th>
              <th className="py-1 pr-2 font-medium">Client</th>
              <th className="py-1 pr-2 text-right font-medium">Disputed</th>
              <th className="py-1 pr-2 text-right font-medium">Rate</th>
              <th className="py-1 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {report.shops.map((shop) => (
              <tr key={shop.shop_id} className="border-t border-[var(--border)]">
                <td className="max-w-[14rem] truncate py-1 pr-2 text-[var(--text-primary)]">
                  {shop.shop_name}
                </td>
                <td className="max-w-[10rem] truncate py-1 pr-2 text-[var(--text-secondary)]">
                  {shop.client_name}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-primary)]">
                  {shop.disputed_count} of {shop.disputed_count + shop.collected_count}
                </td>
                <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                  {(shop.dispute_rate * 100).toFixed(0)}%
                </td>
                <td className="py-1 text-right tabular-nums text-[var(--text-secondary)]">
                  {money(shop.disputed_amount_cents)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
