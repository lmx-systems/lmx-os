import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { truncateId } from '../lib/format'
import type { DecisionFact, OrderLookupRow } from '../lib/types'

/**
 * Find an order (docs/ROADMAP_1.5.md CON-1).
 *
 * **A dispatcher could not do this.** The hold queue's search filters the held
 * list, so an order already released, assigned or delivered was unfindable —
 * while the customer phoning about it could search their own orders all along.
 *
 * Searching is the start of the job, not the end of it: the caller wants to know
 * *why*, so a result opens straight into `AGT-4`'s explanation rather than
 * making somebody go and find it. Every line there cites the decision it came
 * from, and when the record cannot answer it says so.
 *
 * At the top of the dispatching column, above the pipeline. It is the thing
 * reached for when the phone rings, which is not a scheduled moment.
 */
export function OrderLookupPanel({ hubId }: { hubId: string }) {
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<OrderLookupRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<Error | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)

  useEffect(() => {
    const term = query.trim()
    if (term.length < 2) {
      setRows(null)
      setOpenId(null)
      return
    }
    // Debounced: a dispatcher types a reference at speed and every keystroke
    // would otherwise be a query against every order in the hub.
    let live = true
    const timer = setTimeout(() => {
      api
        .lookupOrders(hubId, term)
        .then((page) => {
          if (!live) return
          setRows(page.items)
          setTotal(page.total)
          setError(null)
        })
        .catch((e) => live && setError(e as Error))
    }, 250)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [query, hubId])

  return (
    <Card
      title="Find an order"
      meta={rows ? `${total} match${total === 1 ? '' : 'es'}` : undefined}
    >
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Reference, shop, contact or address…"
        className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
      />

      {error && <p className="mt-2 text-sm text-[var(--red)]">Couldn't search: {error.message}</p>}

      {rows !== null && rows.length === 0 && (
        <p className="mt-3 text-[12px] text-[var(--text-muted)]">
          Nothing in this hub matches. An order for another hub will not appear here.
        </p>
      )}

      {rows !== null && rows.length > 0 && (
        <ul className="mt-2 space-y-1">
          {rows.map((row) => (
            <li key={row.order_id}>
              <button
                onClick={() => setOpenId(openId === row.order_id ? null : row.order_id)}
                className="flex w-full items-baseline justify-between gap-3 rounded-[var(--radius)] px-2 py-1.5 text-left hover:bg-[var(--surface-2)]"
              >
                <span className="text-[13px] text-[var(--text-primary)]">
                  {row.external_ref}
                  {row.shop_name && (
                    <span className="text-[var(--text-muted)]"> · {row.shop_name}</span>
                  )}
                </span>
                <span className="flex-shrink-0 text-[11px] text-[var(--text-muted)]">
                  {row.status.replace(/_/g, ' ')}
                  {row.minutes_late !== null && (
                    <span className="ml-1.5 font-medium text-[var(--red)]">
                      {row.minutes_late} min late
                    </span>
                  )}
                </span>
              </button>
              {openId === row.order_id && <Why orderId={row.order_id} />}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/**
 * AGT-4's explanation, inline (see `app/record/explain.py`).
 *
 * Every line cites the decision snapshot it came from. When the record cannot
 * answer, it says so in the same weight as an answer rather than showing an
 * empty space — a dispatcher who cannot tell "held because no driver was on
 * shift" from "we have no idea" stops believing both.
 */
function Why({ orderId }: { orderId: string }) {
  const [facts, setFacts] = useState<DecisionFact[] | null>(null)
  const [unexplained, setUnexplained] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    api
      .orderExplanation(orderId)
      .then((d) => {
        if (!live) return
        setFacts(d.facts)
        setUnexplained(d.unexplained)
      })
      .catch(() => live && setUnexplained('Could not read the decision log.'))
    return () => {
      live = false
    }
  }, [orderId])

  return (
    <div className="mb-1 ml-2 border-l-2 border-[var(--border)] px-2.5 py-1.5">
      {facts === null && !unexplained && (
        <p className="text-[12px] text-[var(--text-muted)]">Reading the decision log…</p>
      )}
      {unexplained && (
        <p className="text-[12px] text-[var(--text-secondary)]">
          <span className="font-medium text-[var(--amber)]">Not recorded. </span>
          {unexplained}
        </p>
      )}
      {facts?.map((fact, i) => (
        <p key={`${fact.snapshot_id}-${i}`} className="text-[12px] leading-snug">
          <span className="mr-2 tabular-nums text-[var(--text-muted)]">
            {new Date(fact.at).toLocaleTimeString()}
          </span>
          <span className="text-[var(--text-primary)]">{fact.statement}</span>
          <span
            className="ml-2 font-mono text-[10.5px] text-[var(--text-muted)]"
            title={`Decision snapshot ${fact.snapshot_id}, engine ${fact.engine}`}
          >
            {truncateId(fact.snapshot_id)}
          </span>
        </p>
      ))}
    </div>
  )
}
