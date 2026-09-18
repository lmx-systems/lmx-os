import { useMemo, useState } from 'react'
import { Card } from './ui/Card'
import { Chip } from './ui/Chip'
import { TierBadge } from './ui/Badge'
import { truncateId } from '../lib/format'
import type { ExceptionItem, ExceptionQueue } from '../lib/types'

interface ExceptionsPanelProps {
  data: ExceptionQueue | null
  error: Error | null
  loading: boolean
}

/**
 * What to look at before the phone rings (docs/ROADMAP_1.5.md CON-4).
 *
 * The backend endpoint existed for a turn with nothing reading it, which is the
 * same orphan the Phase 2 audit found four of - a module with tests and no
 * caller looks finished from every angle except that one. This is the caller.
 *
 * **Placed above the hold queue deliberately.** The hold queue is work going to
 * plan; this is work that is not. A dispatcher scanning the board top-down
 * should meet the exceptions first, because everything below them is fine.
 *
 * **The clock is shown as minutes, never as a score.** The model that would
 * rank these properly is `M2` - P(a consequence | this order is late) - and it
 * needs hundreds of observed consequences that do not exist. A coloured
 * "urgency" badge would look exactly like that model and would be a guess
 * wearing its clothes, so the only emphasis here is red past an hour, which is
 * the clock and says so.
 */

const KIND_LABEL: Record<ExceptionItem['kind'], string> = {
  flagged_by_driver: 'Driver flagged',
  delivery_failed: 'Failed',
  past_promise: 'Past promise',
  released_but_unplaced: 'Nobody has it',
}

// The order the backend ranks ties in, mirrored so the filter chips read in the
// same sequence a dispatcher meets the rows.
const KINDS: (ExceptionItem['kind'] | 'all')[] = [
  'all',
  'flagged_by_driver',
  'delivery_failed',
  'past_promise',
  'released_but_unplaced',
]

// An hour past the promise is where a delivery stops being late and starts
// being a phone call. A threshold rather than a gradient because a gradient
// implies a model behind it.
const RED_AFTER_MINUTES = 60

function waited(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`
  const hours = Math.floor(minutes / 60)
  const rest = Math.round(minutes % 60)
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

export function ExceptionsPanel({ data, error, loading }: ExceptionsPanelProps) {
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all')

  const rows = useMemo(() => {
    if (!data) return []
    return kind === 'all' ? data.items : data.items.filter((i) => i.kind === kind)
  }, [data, kind])

  const meta = loading
    ? 'refreshing…'
    : data
      ? data.items.length === 0
        ? 'nothing outstanding'
        : `${data.items.length} to look at`
      : undefined

  return (
    <Card title="Exceptions" meta={meta}>
      {error && (
        <p className="text-sm text-[var(--red)]">Couldn't load exceptions: {error.message}</p>
      )}

      {!error && data && data.items.length === 0 && (
        // Said in words rather than shown as an empty table. "Nothing
        // outstanding" and "nothing was checked" look identical otherwise, and
        // only one of them means the dispatcher can look away.
        <p className="text-sm text-[var(--text-secondary)]">
          Nothing outstanding. Every order is either moving or delivered.
        </p>
      )}

      {!error && data && data.items.length > 0 && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {KINDS.map((k) => (
              <Chip
                key={k}
                label={
                  k === 'all'
                    ? `All ${data.items.length}`
                    : `${KIND_LABEL[k]} ${data.counts[k] ?? 0}`
                }
                active={kind === k}
                onClick={() => setKind(k)}
              />
            ))}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] text-xs text-[var(--text-muted)]">
                  <th className="py-2 pr-3 font-medium">Waiting</th>
                  <th className="py-2 pr-3 font-medium">Order</th>
                  <th className="py-2 pr-3 font-medium">Tier</th>
                  <th className="py-2 pr-3 font-medium">What happened</th>
                  <th className="py-2 font-medium">What to do</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr
                    key={item.order_id}
                    className="border-b border-[var(--border)] last:border-0 align-top"
                  >
                    <td
                      className={`whitespace-nowrap py-2.5 pr-3 font-medium tabular-nums ${
                        item.minutes_waiting >= RED_AFTER_MINUTES
                          ? 'text-[var(--red)]'
                          : 'text-[var(--text-primary)]'
                      }`}
                    >
                      {waited(item.minutes_waiting)}
                    </td>
                    <td className="whitespace-nowrap py-2.5 pr-3 text-[var(--text-secondary)]">
                      {item.external_ref || truncateId(item.order_id)}
                    </td>
                    <td className="py-2.5 pr-3">
                      {item.sla_tier ? <TierBadge tier={item.sla_tier} /> : null}
                    </td>
                    <td className="py-2.5 pr-3 text-[var(--text-secondary)]">
                      <span className="mr-1.5 font-medium text-[var(--text-primary)]">
                        {KIND_LABEL[item.kind]}
                      </span>
                      {item.detail}
                    </td>
                    <td className="py-2.5 text-[var(--text-secondary)]">{item.next_action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-3 text-xs text-[var(--text-muted)]">
            Ordered by how long each has been waiting. That is the clock, not a
            prediction of which will turn into a call — the model for that needs
            delivery outcomes we have not collected yet.
          </p>
        </>
      )}
    </Card>
  )
}
