import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { ReturnItem } from '../lib/types'

/**
 * Cores on the way back, and the two ways an operator closes one out
 * (docs/ROADMAP.md W1).
 *
 * **The half of `W1` that shipped without a front end.** `W1` reads *"Done (PRs
 * #13–#16)"* with four slices listed; the client portal got its panel and the
 * ops side never did, so `mark-returned` — slice 3's *"ops manual mark"* — and
 * slice 4's `not_ready → ready_for_pickup` reschedule were reachable only by
 * curl. Found by `tests/test_no_unreachable_routes.py`, along with the driver
 * screens that are the other missing third.
 *
 * **Oldest first, with an age on every row**, which is the endpoint's own
 * ordering and the reason the column exists. A core that has been sitting for
 * nine days is the one that turns into a phone call, and it looks exactly like
 * a core from this morning in any list sorted by anything else.
 *
 * **The action offered depends on the status, because the server refuses the
 * wrong one.** `reschedule` is a 409 unless the core is `not_ready`;
 * `mark-returned` is a 409 once it is already `returned_to_shop` or
 * `cancelled`. Showing a button that returns 409 teaches an operator that the
 * panel is unreliable, which is a more expensive lesson than the one missing
 * button.
 *
 * **`returned_to_shop` rows are not listed.** `awaiting=true` asks the server
 * for what is still outstanding; a closed core is not work, and a list that
 * accumulates every core ever handled stops being a worklist by the second
 * month.
 */

function ageLabel(hours: number): string {
  if (hours < 1) return 'under an hour'
  if (hours < 48) return `${Math.round(hours)}h`
  return `${Math.round(hours / 24)}d`
}

export function ReturnsPanel({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [items, setItems] = useState<ReturnItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function load() {
    try {
      setItems(await api.listReturns(hubId, true))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    if (hubId) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hubId])

  async function act(item: ReturnItem, what: 'returned' | 'reschedule') {
    setBusy(item.return_id)
    setError(null)
    try {
      if (what === 'returned') {
        await api.markReturnReturned(item.return_id)
        onToast(`${item.manifest} marked returned to ${item.shop_name ?? 'the shop'}.`)
      } else {
        await api.rescheduleReturn(item.return_id)
        onToast(`${item.manifest} is back on the list to collect.`)
      }
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  // Nothing outstanding is the good state and needs no card. A panel that reads
  // "nothing here" every day is one an operator learns to skip, and this is the
  // one they need to notice on the day it is not empty.
  if (items !== null && items.length === 0) return null

  return (
    <Card title="Cores on the way back" meta={items ? `${items.length} outstanding` : 'loading…'}>
      {error && <p className="mb-2 text-sm text-[var(--red)]">{error}</p>}

      <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
        Oldest first. A core sitting for a week is the one that becomes a phone call.
      </p>

      <ul className="space-y-1.5">
        {(items ?? []).map((item) => (
          <li
            key={item.return_id}
            className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-[var(--border)] pt-1.5 text-[12.5px]"
          >
            <span className="min-w-0 flex-1 truncate text-[var(--text-primary)]">
              {item.manifest}
            </span>
            <span className="truncate text-[var(--text-secondary)]">
              {item.shop_name ?? '—'}
            </span>
            <span
              className={`tabular-nums ${
                item.age_hours >= 168 ? 'text-[var(--amber)]' : 'text-[var(--text-muted)]'
              }`}
              title={`${item.status} since ${new Date(item.created_at).toLocaleString()}`}
            >
              {ageLabel(item.age_hours)}
            </span>
            <span className="text-[11px] text-[var(--text-muted)]">
              {item.status.replace(/_/g, ' ')}
            </span>

            {/* Only the action the server will accept. A button that 409s
                teaches an operator the panel is unreliable. */}
            {item.status === 'not_ready' && (
              <button
                disabled={busy === item.return_id}
                onClick={() => act(item, 'reschedule')}
                className="shrink-0 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-primary)] disabled:opacity-40"
              >
                Collect again
              </button>
            )}
            {item.status !== 'cancelled' && item.status !== 'returned_to_shop' && (
              <button
                disabled={busy === item.return_id}
                onClick={() => act(item, 'returned')}
                className="shrink-0 rounded-md bg-[var(--accent)] px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40"
              >
                Returned
              </button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
