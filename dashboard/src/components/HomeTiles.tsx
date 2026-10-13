import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { PlaybookMode, PlaybookReport } from '../lib/types'

const REFRESH_MS = 60_000

const RULE_LABEL: Record<string, string> = {
  failed_delivery: 'Failed deliveries',
  unplaced: 'Orders nobody has',
}

const MODE_SHORT: Record<PlaybookMode, string> = {
  recommend: 'Suggests',
  veto: 'Acts unless stopped',
  act: 'Acts alone',
}

/** The playbook report for a window, refreshed once a minute: it moves with people's writes, not with the 5s fleet poll. */
function usePlaybook(hubId: string, days: number): { report: PlaybookReport | null; error: boolean } {
  const [report, setReport] = useState<PlaybookReport | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let live = true
    const load = () =>
      api
        .playbook(hubId, days)
        .then((r) => {
          if (!live) return
          setReport(r)
          setError(false)
        })
        .catch(() => live && setError(true))
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => {
      live = false
      clearInterval(id)
    }
  }, [hubId, days])
  return { report, error }
}

/**
 * The headline number of decision D-SEAT, on the one lime tile Home has:
 * person touches per 100 orders, goal zero (DEC-7). The last 24 hours, with
 * the week beside it for direction - the report has no daily series, so this
 * shows two windows rather than drawing a chart it would have to invent.
 */
export function TouchesTile({ hubId }: { hubId: string }) {
  const day = usePlaybook(hubId, 1)
  const week = usePlaybook(hubId, 7)
  const rate = day.report?.touches_per_100_orders
  const weekRate = week.report?.touches_per_100_orders

  return (
    <section
      aria-labelledby="touches-h"
      className="flex flex-col gap-4 rounded-[var(--radius-lg)] bg-[var(--lime)] p-7 text-[var(--text-primary)]"
    >
      <h2 id="touches-h" className="text-base font-bold">
        Person touches per 100 orders
      </h2>
      <div className="flex items-baseline gap-3">
        <span className="text-[80px] font-extrabold leading-[0.9] tracking-[-0.06em] tabular-nums">
          {day.error && !day.report ? '—' : rate === null || rate === undefined ? '—' : rate}
        </span>
        <span className="text-sm font-semibold">goal 0</span>
      </div>
      <p className="text-[13.5px] font-medium text-[var(--lime-ink)]">
        {day.error && !day.report
          ? "Couldn't load the last 24 hours."
          : day.report === null
            ? 'Loading…'
            : rate === null
              ? 'No live orders in the last 24 hours, so there is no rate yet.'
              : `Last 24 hours: ${day.report.touches} ${day.report.touches === 1 ? 'touch' : 'touches'} across ${day.report.orders} ${day.report.orders === 1 ? 'order' : 'orders'}.`}
        {weekRate !== null && weekRate !== undefined && ` Last 7 days: ${weekRate}.`}
      </p>
      <p className="mt-auto text-[13px] text-[var(--lime-ink)]">
        Every write a person makes to run the day or fix the record counts here. Rules that keep agreeing with
        people earn the right to act alone.
      </p>
    </section>
  )
}

/** Each rule's step on the way to acting alone, from the same report the Rules page reads in full. */
export function RulesTile({ hubId, onOpen }: { hubId: string; onOpen: () => void }) {
  const { report, error } = usePlaybook(hubId, 7)
  return (
    <section aria-labelledby="rules-h" className="flex flex-col gap-3.5 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
      <h2 id="rules-h" className="text-base font-bold">
        Rules
      </h2>
      {error && !report && <p className="text-sm text-[var(--red)]">Couldn't load the rules.</p>}
      {!error && !report && <p className="text-sm text-[var(--text-muted)]">Loading…</p>}
      {report?.rules.map((rule) => {
        const decided = rule.yes + rule.no
        return (
          <div key={rule.rule} className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                rule.mode === 'recommend' ? 'bg-[var(--amber)]' : 'bg-[var(--green)]'
              }`}
            />
            <div className="flex min-w-0 flex-col">
              <span className="text-[13.5px] font-bold">{RULE_LABEL[rule.rule] ?? rule.rule.replace(/_/g, ' ')}</span>
              <span className="text-[12.5px] text-[var(--text-secondary)]">
                {MODE_SHORT[rule.mode]}
                {decided > 0 && ` · ${rule.yes} of ${decided} agreed`}
              </span>
            </div>
          </div>
        )
      })}
      <button type="button" onClick={onOpen} className="mt-auto self-start text-sm font-bold hover:underline">
        See all rules →
      </button>
    </section>
  )
}
