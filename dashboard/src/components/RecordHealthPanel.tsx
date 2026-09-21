import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { RecordHealth } from '../lib/types'

/**
 * Is the record being written? (docs/ROADMAP_1.5.md REC-1..REC-4.)
 *
 * Every writer in the record layer was wired in the last few changes, and
 * **a writer that silently stops looks exactly like a quiet week.** This is the
 * thing that would notice — which is why each one shows when it last wrote
 * rather than a green tick.
 *
 * Not a KPI and not the savings statement. Those are claims about the business
 * and have their own rules about what may be said; this answers a question
 * about us.
 *
 * Every number carries its denominator. "100% on time" means something entirely
 * different at n=3 and n=400, and a percentage alone cannot tell those apart —
 * so a thin sample is labelled as thin rather than quietly rounded.
 */
export function RecordHealthPanel({ hubId }: { hubId: string }) {
  const [data, setData] = useState<RecordHealth | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let live = true
    if (!hubId) return
    api
      .recordHealth(hubId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e as Error))
    return () => {
      live = false
    }
  }, [hubId])

  return (
    <Card title="The record" meta={data ? `last ${data.window_days} days` : 'loading…'}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load: {error.message}</p>}
      {!error && !data && <p className="text-[12px] text-[var(--text-muted)]">Reading…</p>}

      {data && (
        <>
          <div className="mb-3 border-b border-[var(--border)] pb-3">
            {data.on_time_not_measured ? (
              <p className="text-[13px] text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--amber)]">On time: not measured. </span>
                {data.on_time_not_measured}
              </p>
            ) : (
              <>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-semibold tabular-nums text-[var(--text-primary)]">
                    {data.on_time_percentage}%
                  </span>
                  <span className="text-[12px] text-[var(--text-muted)]">
                    on time · {data.on_time_numerator} of {data.on_time_denominator}
                  </span>
                </div>
                {data.on_time_interval && (
                  <p className="text-[11px] text-[var(--text-muted)]">
                    {data.on_time_interval[0]}–{data.on_time_interval[1]}% at 95% confidence
                    {data.on_time_is_thin && ' · too few deliveries for this to mean much yet'}
                  </p>
                )}
              </>
            )}
            <p className="mt-1 text-[11px] text-[var(--text-muted)]">
              Judged against the promise that applied at the time, not today&rsquo;s terms.
            </p>
          </div>

          <section className="mb-3">
            <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Writers
            </h3>
            <ul className="space-y-1">
              {data.writers.map((writer) => (
                <li key={writer.name} className="flex items-baseline justify-between gap-3 text-[12px]">
                  <span className="text-[var(--text-primary)]" title={writer.note}>
                    {writer.name}
                  </span>
                  <span className="flex-shrink-0 tabular-nums text-[var(--text-muted)]">
                    {writer.rows_in_window}
                    {writer.last_written_at ? (
                      <span className="ml-2">
                        last {new Date(writer.last_written_at).toLocaleDateString()}
                      </span>
                    ) : (
                      <span className="ml-2 text-[var(--amber)]">nothing yet</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
            {data.decision_link_percentage !== null && (
              <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                {data.decision_link_percentage}% of delivered outcomes cite the cycle that
                assigned them. Below 100% is expected — a route built from an offer or a live
                insertion has no recorded cycle to cite. A fall is the signal, not the level.
              </p>
            )}
          </section>

          {data.dwell_docks > 0 && (
            <section className="mb-3">
              <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Dwell known per dock
              </h3>
              <p className="text-[13px] text-[var(--text-primary)]">
                <span className="tabular-nums font-medium">{data.dwell_from_our_own}</span> from our
                own deliveries
                <span className="text-[var(--text-muted)]">
                  {' '}
                  · {data.dwell_inherited} inherited · {data.dwell_unknown} unknown, of{' '}
                  {data.dwell_docks} docks
                </span>
              </p>
              <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
                Inherited means a previous operator measured it at the same dock. Counted
                separately and never averaged with ours — they are different measurements, and
                which one answers is decided per dock rather than blended.
                {data.dwell_thin > 0 && (
                  <>
                    {' '}
                    <span className="text-[var(--amber)]">
                      {data.dwell_thin} rest on fewer than five stops
                    </span>{' '}
                    — shown anyway, because the alternative at those docks is no figure at all.
                  </>
                )}
              </p>
            </section>
          )}

          {data.geofence_coverage !== null && (
            <section className="mb-3">
              <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                Geofence
              </h3>
              <p className="text-[13px] text-[var(--text-primary)]">
                <span className="tabular-nums font-medium">
                  {Math.round(data.geofence_coverage * 100)}%
                </span>{' '}
                of completed stops saw a crossing
                {data.geofence_lead_p50_seconds !== null && (
                  <span className="text-[var(--text-muted)]">
                    {' '}
                    · fence fires {Math.round(data.geofence_lead_p50_seconds)}s before the tap
                  </span>
                )}
              </p>
              <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
                The 75m radius was a guess. This is the measurement that checks it, across{' '}
                {data.geofence_comparable_stops} stops with both a crossing and a tap.
              </p>
            </section>
          )}

          <section>
            <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Consequence labels
            </h3>
            <p className="text-[13px] text-[var(--text-primary)]">
              <span className="tabular-nums font-medium">{data.labels.observed_consequences}</span>{' '}
              observed
              <span className="text-[var(--text-muted)]">
                {' '}
                · {data.labels.silences} recorded as nothing happened
              </span>
            </p>
            <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">
              {data.labels.at_band_target
                ? 'Past the top of the band the brief asks for.'
                : data.labels.at_band_minimum
                  ? `Past the bottom of the band. ${data.labels.shortfall_to_target} more reaches the top.`
                  : `${data.labels.shortfall_to_minimum} more reaches the bottom of the ` +
                    `${data.labels.required_range_for_m2[0]}–${data.labels.required_range_for_m2[1]} band.`}{' '}
              The brief states a range and declines to say where inside it the model becomes
              trainable, so both ends are shown.
            </p>
            {data.open_flags > 0 && (
              <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                {data.open_flags} linkage question{data.open_flags === 1 ? '' : 's'} still open.
              </p>
            )}
          </section>
        </>
      )}
    </Card>
  )
}
