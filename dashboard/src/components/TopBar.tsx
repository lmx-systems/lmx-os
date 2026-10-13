import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatSecondsAgo } from '../lib/format'
import type { HubSummary, OpsProfileView } from '../lib/types'

export interface PageSpec {
  id: string
  label: string
  /** A count beside the label - work waiting on that page. Omit or 0 for none. */
  badge?: number
}

interface TopBarProps {
  hubId: string
  // Changes when a hub is created, so the list is fetched again.
  hubsVersion: number
  onChangeHubId: (hubId: string) => void
  lastUpdatedAt: number | null
  opsProfile: OpsProfileView
  onLogout: () => void
  pages: PageSpec[]
  page: string
  onPage: (id: string) => void
}

/**
 * The console's one bar: the mark, the pages as a pill group, then the hub and
 * the person (docs/DESIGN_SYSTEM.md).
 *
 * GET /hubs (docs/ROADMAP.md D1) backs a real dropdown. Falls back to the old
 * raw-UUID text input if the list ever fails to load or comes back empty (e.g.
 * no hubs seeded yet) - ops shouldn't be blocked from targeting a hub just
 * because this convenience lookup had a bad moment.
 */
export function TopBar({
  hubId,
  hubsVersion,
  onChangeHubId,
  lastUpdatedAt,
  opsProfile,
  onLogout,
  pages,
  page,
  onPage,
}: TopBarProps) {
  const [secondsAgo, setSecondsAgo] = useState(0)
  const [hubs, setHubs] = useState<HubSummary[] | null>(null)

  useEffect(() => {
    (async () => {
      try {
        setHubs(await api.listHubs())
      } catch {
        setHubs([])
      }
    })()
  }, [hubsVersion])

  useEffect(() => {
    if (lastUpdatedAt === null) return
    setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    const id = setInterval(() => {
      setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [lastUpdatedAt])

  const useDropdown = hubs !== null && hubs.length > 0
  const field =
    'w-60 rounded-full bg-[var(--surface)] px-4 py-2 text-[13.5px] font-semibold text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:shadow-[inset_0_0_0_1.5px_var(--accent)]'

  return (
    <header className="mb-6 flex flex-wrap items-center gap-x-5 gap-y-3">
      <div className="flex items-center gap-2 text-xl font-extrabold tracking-[-0.04em]">
        <img src="/lmx-mark.png" alt="" className="h-[26px] w-[26px] rounded-[8px]" />
        lmx
      </div>

      {pages.length > 0 && (
        <nav aria-label="Console" className="flex flex-wrap gap-1 rounded-full bg-[var(--surface)] p-1">
          {pages.map((p) => {
            const selected = p.id === page
            return (
              <a
                key={p.id}
                href={`#${p.id}`}
                aria-current={selected ? 'page' : undefined}
                onClick={(e) => {
                  e.preventDefault()
                  onPage(p.id)
                }}
                className={`rounded-full px-4 py-1.5 text-[13.5px] transition-colors ${
                  selected
                    ? 'bg-[var(--accent)] font-semibold text-white'
                    : 'font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {p.label}
                {p.badge ? (
                  <span
                    className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[11px] font-bold tabular-nums ${
                      selected ? 'bg-[var(--lime)] text-[var(--lime-ink)]' : 'bg-[var(--needs-dim)] text-[var(--needs)]'
                    }`}
                  >
                    {p.badge}
                  </span>
                ) : null}
              </a>
            )
          })}
        </nav>
      )}

      <div className="ml-auto flex flex-wrap items-center gap-2.5">
        {useDropdown ? (
          <select
            id="hub-id"
            aria-label="Hub"
            value={hubId}
            onChange={(e) => onChangeHubId(e.target.value)}
            className={field}
          >
            <option value="">{opsProfile.role === 'admin' ? 'Select or create a hub…' : 'Select a hub…'}</option>
            {hubs!.map((hub) => (
              <option key={hub.hub_id} value={hub.hub_id}>
                {hub.name}
              </option>
            ))}
          </select>
        ) : (
          <input
            id="hub-id"
            type="text"
            aria-label="Hub ID"
            value={hubId}
            onChange={(e) => onChangeHubId(e.target.value)}
            placeholder="Paste a hub UUID"
            className={field}
          />
        )}

        {lastUpdatedAt !== null && (
          <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)]">
            <span className="h-[7px] w-[7px] rounded-full bg-[var(--green)] shadow-[0_0_0_3px_var(--green-dim)]" />
            Live · {formatSecondsAgo(secondsAgo)}
          </span>
        )}

        <span className="flex items-center gap-2 rounded-full bg-[var(--surface)] py-1 pl-1 pr-1 text-[13px] font-semibold">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--needs-dim)] text-[11px] font-bold text-[var(--needs)]"
          >
            {opsProfile.name.trim().charAt(0).toUpperCase() || '?'}
          </span>
          {opsProfile.name}
          {opsProfile.role !== 'admin' && (
            <span className="rounded-full bg-[var(--surface-3)] px-2 py-0.5 text-[11px] font-semibold text-[var(--text-secondary)]">
              {opsProfile.role === 'dispatcher' ? 'dispatch' : 'view only'}
            </span>
          )}
          <button
            onClick={onLogout}
            className="rounded-full px-3 py-1 text-xs font-semibold text-[var(--text-secondary)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
          >
            Sign out
          </button>
        </span>
      </div>
    </header>
  )
}
