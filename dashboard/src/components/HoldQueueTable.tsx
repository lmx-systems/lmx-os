import { Fragment, useEffect, useMemo, useState } from 'react'
import { Card } from './ui/Card'
import { Chip } from './ui/Chip'
import { TierBadge } from './ui/Badge'
import { AT_RISK_MINUTES, formatCountdown, minutesUntil, truncateId } from '../lib/format'
import { api } from '../lib/api'
import type { HeldOrderView, OrderExplanation } from '../lib/types'

interface HoldQueueTableProps {
  data: HeldOrderView[] | null
  error: Error | null
  loading: boolean
}

type SortKey = 'shop_name' | 'sla_tier' | 'held_since' | 'hold_deadline'

// HOT_SHOT (Phase 8) listed first - the highest-urgency tier, and the one
// hub staff most need to filter to at a glance.
const TIERS = ['all', 'HOT_SHOT', 'T1', 'T2', 'T3'] as const

export function HoldQueueTable({ data, error, loading }: HoldQueueTableProps) {
  const [search, setSearch] = useState('')
  const [tier, setTier] = useState<(typeof TIERS)[number]>('all')
  const [sortKey, setSortKey] = useState<SortKey>('hold_deadline')
  const [sortDir, setSortDir] = useState<1 | -1>(1)
  // One at a time. Several open explanations is a wall of text on the
  // screen somebody is using to decide what to do in the next minute.
  const [explaining, setExplaining] = useState<string | null>(null)

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 1 ? -1 : 1))
    } else {
      setSortKey(key)
      setSortDir(1)
    }
  }

  const rows = useMemo(() => {
    if (!data) return []
    const q = search.trim().toLowerCase()
    const filtered = data.filter(
      (o) =>
        (tier === 'all' || o.sla_tier === tier) &&
        (q === '' || o.order_id.toLowerCase().includes(q) || o.shop_name.toLowerCase().includes(q)),
    )
    return filtered.sort((a, b) => {
      let cmp = 0
      if (sortKey === 'shop_name') cmp = (a.shop_name || a.order_id).localeCompare(b.shop_name || b.order_id)
      else if (sortKey === 'sla_tier') cmp = a.sla_tier.localeCompare(b.sla_tier)
      else if (sortKey === 'held_since') cmp = new Date(a.held_since).getTime() - new Date(b.held_since).getTime()
      else cmp = minutesUntil(a.hold_deadline) - minutesUntil(b.hold_deadline)
      return cmp * sortDir
    })
  }, [data, search, tier, sortKey, sortDir])

  return (
    <Card title="Hold queue" meta={loading ? 'refreshing…' : data ? `${data.length} held` : undefined}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load hold queue: {error.message}</p>}

      {!error && (
        <>
          <div className="mb-3 flex items-center gap-2">
            <div className="flex flex-1 items-center gap-2 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5">
              <svg
                className="h-3.5 w-3.5 flex-shrink-0 text-[var(--text-muted)]"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="M21 21l-4.3-4.3" />
              </svg>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search shop or order id…"
                className="w-full bg-transparent text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
              />
            </div>
            {TIERS.map((t) => (
              <Chip
                key={t}
                label={t === 'all' ? 'All' : t === 'HOT_SHOT' ? 'Hot Shot' : t}
                active={tier === t}
                onClick={() => setTier(t)}
              />
            ))}
          </div>

          {data && data.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">
              Nothing currently held for this hub.
            </p>
          )}

          {data && data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead>
                  <tr className="text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                    <SortableHeader label="Shop" sortKey="shop_name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                    <SortableHeader label="SLA" sortKey="sla_tier" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                    <SortableHeader label="Held" sortKey="held_since" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                    <SortableHeader label="Deadline in" sortKey="hold_deadline" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                    <th className="py-0 pb-2 font-semibold" />
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-6 text-center text-[var(--text-muted)]">
                        No held orders match this filter.
                      </td>
                    </tr>
                  )}
                  {rows.map((order) => {
                    const minsLeft = minutesUntil(order.hold_deadline)
                    const risk = minsLeft <= AT_RISK_MINUTES
                    const open = explaining === order.order_id
                    return (
                      <Fragment key={order.order_id}>
                      <tr className={`border-t border-[var(--border)] ${risk ? 'shadow-[inset_3px_0_0_var(--red)]' : ''}`}>
                        <td className="py-2 pr-3">
                          <div className="font-medium text-[var(--text-primary)]">
                            {order.shop_name || <span className="text-[var(--text-muted)]">Unknown shop</span>}
                            {order.cluster_mate_ids.length > 0 && (
                              <span
                                className="ml-1.5 rounded-md bg-[var(--accent-dim)] px-1.5 py-0.5 text-[10.5px] font-medium text-[var(--accent)]"
                                title={`Within clustering radius of: ${order.cluster_mate_ids.join(', ')}`}
                              >
                                +{order.cluster_mate_ids.length} cluster mate{order.cluster_mate_ids.length > 1 ? 's' : ''}
                              </span>
                            )}
                          </div>
                          <div className="font-mono text-[11px] text-[var(--text-muted)]" title={order.order_id}>
                            {truncateId(order.order_id)}
                          </div>
                        </td>
                        <td className="py-2 pr-3">
                          <TierBadge tier={order.sla_tier} />
                        </td>
                        <td className="py-2 pr-3 text-[var(--text-secondary)]">
                          {new Date(order.held_since).toLocaleTimeString()}
                        </td>
                        <td
                          className={`py-2 pr-3 tabular-nums ${
                            risk ? 'font-semibold text-[var(--red)]' : minsLeft <= 15 ? 'text-[var(--amber)]' : 'text-[var(--text-primary)]'
                          }`}
                        >
                          {formatCountdown(order.hold_deadline)}
                        </td>
                        <td className="py-2 text-right">
                          <button
                            onClick={() => setExplaining(open ? null : order.order_id)}
                            className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                            aria-expanded={open}
                          >
                            {open ? 'Hide' : 'Why?'}
                          </button>
                        </td>
                      </tr>
                      {open && <ExplanationRow orderId={order.order_id} />}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function SortableHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onClick,
}: {
  label: string
  sortKey: SortKey
  activeKey: SortKey
  dir: 1 | -1
  onClick: (key: SortKey) => void
}) {
  const active = sortKey === activeKey
  return (
    <th className="cursor-pointer select-none py-0 pb-2 pr-3 font-semibold" onClick={() => onClick(sortKey)}>
      {label}
      {active && <span className="ml-1 text-[9px] opacity-70">{dir === 1 ? '▾' : '▴'}</span>}
    </th>
  )
}

/**
 * What the decision log says about one held order (docs/ROADMAP_1.5.md AGT-4).
 *
 * *"Every explanation cites `REC-1`'s decision log rather than narrating. No
 * explanation the record cannot support."* So this renders the snapshot id
 * beside every line, and when the record is silent it says so in the same
 * weight as an answer rather than hiding an empty panel. A dispatcher who
 * cannot tell "we held it because no driver was on shift" from "we have no idea
 * why we held it" will stop believing both.
 */
function ExplanationRow({ orderId }: { orderId: string }) {
  const [data, setData] = useState<OrderExplanation | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    api
      .orderExplanation(orderId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e as Error))
    return () => {
      live = false
    }
  }, [orderId])

  return (
    <tr className="bg-[var(--surface-2)]">
      <td colSpan={5} className="px-3 py-2.5">
        {error && <p className="text-[12px] text-[var(--red)]">Couldn't load: {error.message}</p>}
        {!error && !data && <p className="text-[12px] text-[var(--text-muted)]">Reading the decision log…</p>}

        {data && !data.is_explained && (
          <p className="text-[12px] text-[var(--text-secondary)]">
            <span className="font-medium text-[var(--amber)]">Not recorded. </span>
            {data.unexplained}
          </p>
        )}

        {data && data.is_explained && (
          <ol className="space-y-1.5">
            {data.facts.map((fact, i) => (
              <li key={`${fact.snapshot_id}-${i}`} className="text-[12px] leading-snug">
                <span className="mr-2 tabular-nums text-[var(--text-muted)]">
                  {new Date(fact.at).toLocaleTimeString()}
                </span>
                <span className="text-[var(--text-primary)]">{fact.statement}</span>
                <span
                  className="ml-2 font-mono text-[10.5px] text-[var(--text-muted)]"
                  title={`Decision snapshot ${fact.snapshot_id}, engine ${fact.engine}`}
                >
                  decision {fact.snapshot_id.slice(0, 8)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </td>
    </tr>
  )
}
