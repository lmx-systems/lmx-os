import { useEffect, useState } from 'react'

import { api } from '../lib/api'
import type { LinkScorecard, MeasurementView, OperationsScorecard, RateView } from '../lib/types'

/**
 * The measurement endpoints, finally on a screen (docs/ROADMAP.md F7).
 *
 * `GET /operations/scorecard` (I4) and `GET /lmx-link/scorecard` (§3.4) both existed with
 * **no consumer at all** - the data layer was built twice and nothing surfaced it, which
 * is the same "captured and never read" pattern that made building I4 worth doing.
 *
 * **A refusal is rendered as a sentence, not as a blank or a zero.** Every metric here
 * can decline to answer, and the reason distinguishes "no data yet" - which traffic fixes
 * - from "nothing records this", which needs somebody to build something. Showing 0%
 * where the honest answer is "we cannot say" is the failure this whole reporting
 * vocabulary was shaped to avoid, and it would be undone here by rendering a null as a
 * dash.
 *
 * **No driver leaderboard**, though F7's row lists one. I4 computes DPH per driver-day
 * and deliberately never keys it to a named driver, and W4 gives a driver their own
 * figures against an anonymised hub median with a guard against a small fleet being
 * de-anonymised. A ranking here would undo both by accident; it belongs to a decision.
 */
export function MeasurementPanel() {
  const [ops, setOps] = useState<OperationsScorecard | null>(null)
  const [link, setLink] = useState<LinkScorecard | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    Promise.all([api.operationsScorecard(), api.linkScorecard()])
      .then(([o, l]) => {
        if (!live) return
        setOps(o)
        setLink(l)
      })
      .catch(() => {
        if (live) setError('Could not load the scorecards.')
      })
    return () => {
      live = false
    }
  }, [])

  if (error) {
    return <p className="text-sm text-[var(--danger,#b3261e)]">{error}</p>
  }
  if (!ops || !link) {
    return <p className="text-sm text-[var(--text-muted)]">Loading measurements…</p>
  }

  return (
    <div className="flex flex-col gap-6">
      <section>
        <header className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Operations</h2>
          <span className="text-xs text-[var(--text-muted)]">last {ops.window_days} days</span>
        </header>
        <div className="flex flex-col gap-2">
          {ops.measurements.map((m) => (
            <MeasurementRow key={m.name} metric={m} />
          ))}
          {ops.rates.map((r) => (
            <RateRow key={r.name} rate={r} />
          ))}
        </div>
      </section>

      <section>
        <header className="mb-2">
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Order intake</h2>
        </header>
        <div className="flex flex-col gap-2">
          {link.measurements.map((m) => (
            <MeasurementRow key={m.name} metric={m} />
          ))}
        </div>
      </section>
    </div>
  )
}

function Shell({
  name,
  target,
  children,
}: {
  name: string
  target: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm text-[var(--text-primary)]">{name}</span>
        <span className="text-xs text-[var(--text-muted)]">{target}</span>
      </div>
      <div className="mt-1">{children}</div>
    </div>
  )
}

/** "We cannot say, and here is why" - rendered as the answer it is. */
function NotMeasured({ reason }: { reason: string }) {
  return <p className="text-xs italic text-[var(--text-muted)]">{reason}</p>
}

function MeasurementRow({ metric }: { metric: MeasurementView }) {
  return (
    <Shell name={metric.name} target={metric.target}>
      {metric.not_measured ? (
        <NotMeasured reason={metric.not_measured} />
      ) : (
        <p className="text-sm text-[var(--text-secondary)]">
          <span className="text-base font-semibold text-[var(--text-primary)]">
            {metric.median ?? '—'}
          </span>{' '}
          {metric.unit} median
          <span className="text-[var(--text-muted)]">
            {' · '}p90 {metric.p90 ?? '—'} · n={metric.sample_size}
          </span>
        </p>
      )}
    </Shell>
  )
}

function RateRow({ rate }: { rate: RateView }) {
  return (
    <Shell name={rate.name} target={rate.target}>
      {rate.not_measured ? (
        <NotMeasured reason={rate.not_measured} />
      ) : (
        <p className="text-sm text-[var(--text-secondary)]">
          <span className="text-base font-semibold text-[var(--text-primary)]">
            {rate.percentage}%
          </span>
          <span className="text-[var(--text-muted)]">
            {' · '}
            {rate.numerator} of {rate.denominator}
          </span>
          {/* The denominator is already shown; this says out loud that it is too small to
              read as a result, because a bare "100%" is what gets quoted. */}
          {rate.is_thin && (
            <span className="ml-2 rounded bg-[var(--warn-bg,#faf0dc)] px-1.5 py-0.5 text-xs text-[var(--warn,#8a5a00)]">
              too few to be meaningful
            </span>
          )}
        </p>
      )}
    </Shell>
  )
}
