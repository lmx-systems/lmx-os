import { useEffect, useState } from 'react'
import { formatSecondsAgo } from '../lib/format'
import { api } from '../lib/api'
import type { HubView } from '../lib/types'

interface TopBarProps {
  hubId: string
  onChangeHubId: (hubId: string) => void
  lastUpdatedAt: number | null
}

/**
 * Hub selection is a real dropdown now that GET /hubs exists (roadmap item
 * D1) - previously a raw UUID text input. Falls back to a text input if the
 * hubs list can't be loaded (backend down, auth misconfigured), so the
 * dashboard is never *less* usable than it was before the dropdown existed.
 */
export function TopBar({ hubId, onChangeHubId, lastUpdatedAt }: TopBarProps) {
  const [secondsAgo, setSecondsAgo] = useState(0)
  const [hubs, setHubs] = useState<HubView[] | null>(null)
  const [hubsError, setHubsError] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listHubs()
      .then((result) => {
        if (!cancelled) setHubs(result)
      })
      .catch(() => {
        if (!cancelled) setHubsError(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (lastUpdatedAt === null) return
    setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    const id = setInterval(() => {
      setSecondsAgo(Math.round((Date.now() - lastUpdatedAt) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [lastUpdatedAt])

  // A previously-selected hub id (from localStorage) that isn't in the
  // fetched list - e.g. a hub deactivated since last session - still shows
  // as an option so the select doesn't silently display the wrong thing.
  const knownIds = hubs?.map((h) => h.hub_id) ?? []
  const staleSelection = hubId.length > 0 && hubs !== null && !knownIds.includes(hubId)

  return (
    <div className="mb-5 flex items-center gap-4 border-b border-[var(--border)] pb-4.5">
      <div className="flex items-center gap-2.5 text-[15px] font-medium">
        <div className="flex h-[26px] w-[26px] items-center justify-center rounded-[7px] bg-gradient-to-br from-[var(--accent)] to-[#0891b2] text-xs font-bold text-white">
          L
        </div>
        LMX OS
      </div>
      <span className="rounded-full border border-[var(--border)] bg-[var(--surface-2)] px-2.5 py-0.5 text-[11.5px] font-medium text-[var(--text-secondary)]">
        Orchestrator console
      </span>

      {hubs !== null && !hubsError ? (
        <select
          id="hub-id"
          aria-label="Hub"
          value={hubId}
          onChange={(e) => onChangeHubId(e.target.value)}
          className="w-72 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] font-medium text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none"
        >
          <option value="">Select a hub…</option>
          {hubs.map((hub) => (
            <option key={hub.hub_id} value={hub.hub_id}>
              {hub.name}
            </option>
          ))}
          {staleSelection && <option value={hubId}>Unknown hub ({hubId.slice(0, 8)}…)</option>}
        </select>
      ) : (
        <input
          id="hub-id"
          type="text"
          aria-label="Hub ID"
          value={hubId}
          onChange={(e) => onChangeHubId(e.target.value)}
          placeholder={hubsError ? 'Hub list unavailable — paste a hub UUID' : 'Loading hubs…'}
          className="w-72 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] px-3 py-1.5 text-[13.5px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none"
        />
      )}

      <div className="flex-1" />

      {lastUpdatedAt !== null && (
        <div className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <span className="h-[7px] w-[7px] rounded-full bg-[var(--green)] shadow-[0_0_0_3px_var(--green-dim)]" />
          Live · updated {formatSecondsAgo(secondsAgo)}
        </div>
      )}
    </div>
  )
}
