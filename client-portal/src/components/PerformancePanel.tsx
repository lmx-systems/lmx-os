import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { ClientPerformanceView } from '../lib/types'

/**
 * How LMX has actually performed for this distributor (docs/ROADMAP.md F7).
 *
 * F7 calls this the retention proof rather than an ops nicety, and that reading is
 * right: a distributor on per-drop pricing has given up their own view of a fleet they
 * used to run, so "how are you doing for me" is a question they will ask whether or not
 * there is a screen for it. Better that the answer is ours, computed the same way every
 * time, than reconstructed from memory of the deliveries that went wrong.
 *
 * **The on-time figure is the same computation that credits their invoice.** Through
 * `app/sla/commitment.py`, the number here, the number our own dashboard shows, and the
 * credit on their statement are three views of one calculation - so a client can never
 * be looking at 98% while being credited for a breach.
 *
 * **Their own figures only.** No benchmark against other distributors, because that
 * would tell them something about our other customers.
 */
export function PerformancePanel() {
  const [data, setData] = useState<ClientPerformanceView | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    api
      .myPerformance()
      .then((result) => {
        if (live) setData(result)
      })
      .catch(() => {
        if (live) setFailed(true)
      })
    return () => {
      live = false
    }
  }, [])

  if (failed) {
    return (
      <p className="text-sm text-[var(--text-muted)]">
        We couldn&rsquo;t load your delivery record just now.
      </p>
    )
  }
  if (!data) {
    return <p className="text-sm text-[var(--text-muted)]">Loading…</p>
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-[var(--text-primary)]">Your deliveries</h2>
        <p className="text-xs text-[var(--text-muted)]">Last {data.window_days} days</p>
      </div>

      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4">
        <p className="text-xs text-[var(--text-muted)]">Delivered</p>
        <p className="text-2xl font-semibold text-[var(--text-primary)]">
          {data.delivered_count}
        </p>
      </div>

      {data.hit_rates.map((rate) => (
        <div
          key={rate.name}
          className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4"
        >
          <p className="text-xs text-[var(--text-muted)]">{rate.name}</p>
          {rate.not_measured ? (
            /* A reason, not a zero. "We can't say yet" and "0% on time" are wildly
               different messages to put in front of a paying customer. */
            <p className="mt-0.5 text-sm italic text-[var(--text-secondary)]">
              {rate.not_measured}
            </p>
          ) : (
            <>
              <p className="text-2xl font-semibold text-[var(--text-primary)]">
                {rate.percentage}%
              </p>
              <p className="text-xs text-[var(--text-muted)]">
                {rate.numerator} of {rate.denominator} delivered within the agreed time
                {rate.is_thin && ' — too few so far to read much into'}
              </p>
            </>
          )}
        </div>
      ))}

      <p className="text-xs text-[var(--text-muted)]">
        On-time is measured against the delivery time in your service agreement — the same
        calculation that credits your invoice when we miss it.
      </p>
    </div>
  )
}
