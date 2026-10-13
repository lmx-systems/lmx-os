import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'
import type { SiteFactDispute } from '../lib/types'

const FACT_LABELS: Record<SiteFactDispute['fact'], string> = {
  kind_of_site: 'Kind of business',
  setting: 'City centre or suburb',
  shares_address: 'Shares its address',
}

function shown(value: string | boolean | null): string {
  if (value === null) return 'unknown'
  if (value === true) return 'yes'
  if (value === false) return 'no'
  if (value === 'city_core') return 'city centre'
  return value.replace(/_/g, ' ')
}

/**
 * Answers that disagree (docs/ROADMAP_1.5.md DE-15).
 *
 * An answer at the door fills a site fact only while it is unknown or the
 * desk's guess. One that disagrees with a person's or the import's is kept
 * beside the fact rather than applied, so a phone replaying a stale offline
 * answer cannot undo a newer one. That is only safe if somebody reads them:
 * this is where they are settled - keep the fact, or take what the door said.
 * Either way the fact becomes a person's, and the disputes go to its history.
 *
 * Not hub-scoped: a site is a physical place. Renders nothing when nothing
 * disagrees. A viewer sees the list without the buttons.
 */
export function SiteFactDisputesPanel({ canDispatch }: { canDispatch: boolean }) {
  const [disputes, setDisputes] = useState<SiteFactDispute[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  async function load() {
    try {
      setDisputes(await api.siteFactDisputes())
    } catch (e) {
      setError(e as Error)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function settle(d: SiteFactDispute, take: string | boolean | null) {
    const key = `${d.location_id}:${d.fact}`
    setBusy(key)
    // Exactly the answers shown here: the server refuses unless they are the
    // ones still open, so one that came in since - or that a merge brought,
    // with its own older time - is never filed unseen.
    const seen = d.answers.map((a) => a.key)
    try {
      await api.settleSiteFact(d.location_id, d.fact, take, seen)
      setDisputes((current) =>
        (current ?? []).filter((x) => !(x.location_id === d.location_id && x.fact === d.fact)),
      )
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // Another answer came in (or someone settled it first): show what is
        // there now rather than deciding on what was.
        await load()
      }
      setError(e as Error)
    } finally {
      setBusy(null)
    }
  }

  if (disputes !== null && disputes.length === 0 && !error) return null

  return (
    <Card title="Answers that disagree" meta={disputes ? `${disputes.length} ${disputes.length === 1 ? 'fact' : 'facts'} to settle` : 'loading…'}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load: {error.message}</p>}
      <ul className="space-y-2">
        {(disputes ?? []).map((d) => {
          const key = `${d.location_id}:${d.fact}`
          const offered = Array.from(new Set(d.answers.map((a) => a.value))).filter((v) => v !== null)
          return (
            <li key={key} className="rounded-[20px] bg-[var(--surface-2)] px-2.5 py-2">
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <span className="text-[13px] text-[var(--text-primary)]">{d.address ?? 'No address'}</span>
                <span className="flex-shrink-0 text-[11px] text-[var(--text-muted)]">{FACT_LABELS[d.fact]}</span>
              </div>
              <p className="mb-1 text-[12px] text-[var(--text-secondary)]">
                Recorded: {shown(d.current)}
                {d.current_source && <span className="text-[var(--text-muted)]"> ({d.current_source})</span>}
                {' · '}At the door:{' '}
                {d.answers
                  .map((a) => `${shown(a.value)}${a.driver_name ? ` (${a.driver_name})` : ''}`)
                  .join(', ')}
              </p>
              {canDispatch && (
                <div className="flex flex-wrap gap-1">
                  <button
                    disabled={busy === key}
                    onClick={() => settle(d, null)}
                    className="rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40"
                  >
                    Keep {shown(d.current)}
                  </button>
                  {offered.map((value) => (
                    <button
                      key={String(value)}
                      disabled={busy === key}
                      onClick={() => settle(d, value)}
                      className="rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40"
                    >
                      Change to {shown(value)}
                    </button>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </Card>
  )
}
