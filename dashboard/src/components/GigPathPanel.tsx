import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { GigDensityReport, GigJob } from '../lib/types'

/**
 * The gig demand path, and the one number that decides its future
 * (docs/ROADMAP.md G3 and G12).
 *
 * Both endpoints existed with nothing calling them
 * (`docs/ROADMAP_AUDIT_2026-09.md`): reachable by an admin with curl, which is
 * not the same as reachable.
 *
 * **One panel, not two.** The density report is a summary of exactly these jobs,
 * and split across two cards a reader would have to join them by eye — "is 12%
 * sequenced good?" is unanswerable without seeing that it is 3 jobs out of 25.
 *
 * **`sequenced_share` is the finding, so it is the headline.** It is the
 * fraction of delivered jobs the driver was holding concurrently with another —
 * the difference between *sequencing work* and *doing jobs one at a time,
 * faster*. The roadmap expects it to stay near zero until roughly 10–15 drivers,
 * and this panel exists so that expectation gets confirmed or overturned by data
 * rather than argued.
 *
 * **Numerator and denominator are shown beside every rate.** At these volumes
 * "1 of 3" and "33%" are very different things to read, and only one of them
 * invites over-interpretation. A rate with no denominator is how a pilot gets
 * described as a trend.
 *
 * These are not orders: no client, no SLA tier we assigned, no rate-table fee.
 * The platform set the windows and the pay, and we cannot hold one for a
 * cluster-mate — so nothing here offers an action, because there is none to
 * offer.
 */

const STATUSES = ['offered', 'accepted', 'picked_up', 'delivered', 'declined', 'cancelled'] as const

function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function share(value: number | null): string {
  // Null is not zero. "We accepted none of nothing" is not a 0% rate, and
  // rendering it as one would put a number on an empty window.
  return value === null ? 'not measured' : `${(value * 100).toFixed(0)}%`
}

export function GigPathPanel({ hubId }: { hubId: string }) {
  const [density, setDensity] = useState<GigDensityReport | null>(null)
  const [jobs, setJobs] = useState<GigJob[] | null>(null)
  const [status, setStatus] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    if (!hubId) return
    setError(null)
    api
      .gigDensity(hubId)
      .then((d) => live && setDensity(d))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  useEffect(() => {
    let live = true
    if (!hubId) return
    api
      .listGigJobs(hubId, status || undefined)
      .then((j) => live && setJobs(j))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId, status])

  // Nothing on this path at all is the ordinary state today, and an empty card
  // on every admin's board every day is how a board becomes scenery.
  if (density !== null && density.total_offers === 0 && (jobs?.length ?? 0) === 0) return null

  return (
    <Card
      title="Gig path"
      meta={density ? `last ${density.window_days} days` : 'loading…'}
    >
      {error && <p className="mb-2 text-sm text-[var(--red)]">Couldn't load: {error}</p>}

      {density && (
        <>
          <div className="mb-3 rounded-[var(--radius)] border border-[var(--border)] px-3 py-2">
            <p className="text-[11px] font-medium text-[var(--text-muted)]">
              Delivered as part of a sequence
            </p>
            <p className="text-[22px] font-medium leading-tight text-[var(--text-primary)]">
              {share(density.sequenced_share)}
            </p>
            <p className="text-[11.5px] text-[var(--text-secondary)]">
              {density.sequenced_delivered_count} of {density.measurable_delivered_count}{' '}
              measurable deliveries. Near zero is expected below roughly 10–15 drivers —
              it is the difference between sequencing work and doing jobs one at a time,
              faster.
            </p>
          </div>

          <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[12.5px] sm:grid-cols-3">
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Offers</dt>
              <dd className="text-[var(--text-primary)]">
                {density.total_offers}{' '}
                <span className="text-[var(--text-muted)]">
                  ({density.offers_per_day.toFixed(1)}/day)
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Accepted</dt>
              <dd className="text-[var(--text-primary)]">
                {density.accepted_count}{' '}
                <span className="text-[var(--text-muted)]">
                  ({share(density.acceptance_rate)})
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Declined</dt>
              <dd className="text-[var(--text-primary)]">{density.declined_count}</dd>
            </div>
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Delivered</dt>
              <dd className="text-[var(--text-primary)]">{density.delivered_count}</dd>
            </div>
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Active drivers</dt>
              <dd className="text-[var(--text-primary)]">{density.active_driver_count}</dd>
            </div>
            <div>
              <dt className="text-[11px] text-[var(--text-muted)]">Jobs/driver/day</dt>
              <dd className="text-[var(--text-primary)]">
                {density.jobs_per_driver_per_day === null
                  ? 'not measured'
                  : density.jobs_per_driver_per_day.toFixed(1)}{' '}
                <span className="text-[var(--text-muted)]">
                  (pilot {density.pilot_jobs_per_driver_per_day.toFixed(1)})
                </span>
              </dd>
            </div>
          </dl>
        </>
      )}

      <div className="mb-1.5 flex items-center gap-2">
        <label className="text-[11px] font-medium text-[var(--text-muted)]" htmlFor="gig-status">
          Jobs
        </label>
        <select
          id="gig-status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[11.5px] text-[var(--text-primary)]"
        >
          <option value="">all</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace('_', ' ')}
            </option>
          ))}
        </select>
      </div>

      {jobs === null && !error && (
        <p className="text-[12px] text-[var(--text-muted)]">loading…</p>
      )}
      {jobs?.length === 0 && (
        <p className="text-[12px] text-[var(--text-muted)]">
          No jobs{status ? ` with status ${status}` : ''} on this hub.
        </p>
      )}

      {jobs && jobs.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-[var(--text-muted)]">
                <th className="py-1 pr-2 font-medium">Platform</th>
                <th className="py-1 pr-2 font-medium">Pickup</th>
                <th className="py-1 pr-2 font-medium">Window opens</th>
                <th className="py-1 pr-2 text-right font-medium">Pay</th>
                <th className="py-1 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.gig_job_id} className="border-t border-[var(--border)]">
                  <td className="py-1 pr-2 text-[var(--text-secondary)]">
                    {job.source_platform}
                  </td>
                  <td className="max-w-[16rem] truncate py-1 pr-2 text-[var(--text-primary)]">
                    {job.pickup_address}
                  </td>
                  <td className="py-1 pr-2 tabular-nums text-[var(--text-secondary)]">
                    {new Date(job.pickup_window_open).toLocaleString()}
                  </td>
                  <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-primary)]">
                    {money(job.pay_cents)}
                  </td>
                  <td className="py-1 text-[var(--text-secondary)]">
                    {job.status.replace('_', ' ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
