import { useEffect, useMemo, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Card } from './ui/Card'
import type { DriverState } from '../lib/types'

interface FleetMapProps {
  data: DriverState[] | null
  error: Error | null
  loading: boolean
}

/**
 * Live fleet map (docs/ROADMAP.md F2).
 *
 * Purely front-end work: F1 put each driver's last reported position onto the
 * fleet roster response, so this consumes `DriverState.lat/lng` and adds no new
 * endpoint or polling. `App.tsx` already polls `fleetOverview` and passes the
 * same data to `FleetRoster`, so the list and the map can never disagree.
 *
 * **OpenStreetMap tiles, no account.** Same reasoning as the geocoder
 * (app/geocoding/nominatim.py): a keyed provider needs the Google Cloud project
 * that is still blocking E1, and waiting on procurement would have left the
 * dispatcher blind during a pilot. OSM's tile policy is fine for an internal
 * dashboard with a handful of viewers and requires attribution, which is set
 * below. Heavy or public-facing use would need a real tile provider - the same
 * pilot-decision caveat, in the same place in the stack.
 *
 * The view fits the drivers rather than centring on the hub, deliberately: a
 * dispatcher wants to see where their fleet actually is, and `HubSummary`
 * carries no coordinates anyway. With nobody reporting there is nothing to fit,
 * so the empty state says so instead of showing an arbitrary patch of map.
 */

// Status drives marker colour, matching how the roster already reads. Semantic,
// not decorative - "who can I send" is the question this map answers first.
const STATUS_COLOR: Record<DriverState['status'], string> = {
  available: '#0a6644',
  offered: '#a3671f',
  en_route: '#2e6e8e',
  on_break: '#6b7280',
  off_shift: '#9aa3ab',
}

const STATUS_LABEL: Record<DriverState['status'], string> = {
  available: 'Available',
  offered: 'Offer pending',
  en_route: 'En route',
  on_break: 'On break',
  off_shift: 'Off shift',
}

function ageLabel(iso: string | null): string {
  if (!iso) return 'no position'
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (Number.isNaN(seconds)) return 'no position'
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

/** A driver whose position is old enough that the map is lying to you. */
const STALE_AFTER_SECONDS = 5 * 60

function isStale(iso: string | null): boolean {
  if (!iso) return false
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  return seconds > STALE_AFTER_SECONDS
}

export function FleetMap({ data, error, loading }: FleetMapProps) {
  const container = useRef<HTMLDivElement | null>(null)
  const map = useRef<L.Map | null>(null)
  const markers = useRef<L.LayerGroup | null>(null)
  // Fit the view once per hub's worth of data rather than on every poll -
  // re-fitting every few seconds would yank the map out from under a dispatcher
  // mid-drag, which is worse than a slightly stale viewport.
  const hasFitted = useRef(false)

  const located = useMemo(
    () => (data ?? []).filter((d): d is DriverState & { lat: number; lng: number } =>
      d.lat !== null && d.lng !== null,
    ),
    [data],
  )
  const unlocated = useMemo(() => (data ?? []).filter((d) => d.lat === null || d.lng === null), [data])

  useEffect(() => {
    if (!container.current || map.current) return
    map.current = L.map(container.current, { zoomControl: true, attributionControl: true })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      // Required by OSM's tile usage policy, not decoration.
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map.current)
    markers.current = L.layerGroup().addTo(map.current)
    // Somewhere to be before any driver reports. Only ever visible for the
    // instant before the first fit.
    map.current.setView([30.267, -97.743], 11)
    return () => {
      map.current?.remove()
      map.current = null
      markers.current = null
      hasFitted.current = false
    }
  }, [])

  useEffect(() => {
    if (!map.current || !markers.current) return
    markers.current.clearLayers()

    for (const driver of located) {
      const stale = isStale(driver.location_recorded_at)
      const colour = STATUS_COLOR[driver.status]
      const marker = L.circleMarker([driver.lat, driver.lng], {
        radius: 8,
        color: colour,
        fillColor: colour,
        // A stale position is drawn hollow rather than hidden. Hiding it would
        // make a driver vanish from the map for no visible reason; a hollow
        // marker says "this is where they were, a while ago".
        fillOpacity: stale ? 0.15 : 0.85,
        weight: 2,
      })
      marker.bindTooltip(
        `<strong>${driver.name ?? 'Unnamed driver'}</strong><br>${STATUS_LABEL[driver.status]} · ${ageLabel(
          driver.location_recorded_at,
        )}${stale ? '<br><em>position may be out of date</em>' : ''}`,
        { direction: 'top' },
      )
      marker.addTo(markers.current)
    }

    if (located.length > 0 && !hasFitted.current) {
      const bounds = L.latLngBounds(located.map((d) => [d.lat, d.lng] as [number, number]))
      map.current.fitBounds(bounds, { padding: [32, 32], maxZoom: 14 })
      hasFitted.current = true
    }
  }, [located])

  const staleCount = located.filter((d) => isStale(d.location_recorded_at)).length

  return (
    <Card
      title="Fleet map"
      meta={
        loading && !data
          ? 'loading…'
          : data
            ? `${located.length} of ${data.length} reporting${staleCount > 0 ? ` · ${staleCount} stale` : ''}`
            : undefined
      }
    >
      {error && <p className="text-sm text-[var(--red)]">Couldn't load fleet state: {error.message}</p>}

      <div
        ref={container}
        className="h-[340px] w-full overflow-hidden rounded-[var(--radius)] border border-[var(--border)]"
      />

      {/* The list below is the part a dispatcher actually needs when something
          is wrong. A driver with no position is invisible to the optimizer -
          app/optimizer/service.py skips any driver whose location is None - so
          "nobody is being assigned work" and "nobody is reporting" are the same
          fact, and this is where that becomes visible instead of mysterious. */}
      {unlocated.length > 0 && (
        <div className="mt-3 rounded-[var(--radius)] border border-[var(--border)] p-3">
          <div className="text-[13px] font-medium text-[var(--text-primary)]">
            {unlocated.length} driver{unlocated.length === 1 ? '' : 's'} not reporting a position
          </div>
          <div className="mt-1 text-[12px] text-[var(--text-muted)]">
            These can't be assigned work — the optimizer skips a driver it can't locate. They report
            once they're on duty with the app open.
          </div>
          <ul className="mt-2 flex flex-col gap-1 text-[12px]">
            {unlocated.map((d) => (
              <li key={d.driver_id} className="flex justify-between gap-3">
                <span className="text-[var(--text-primary)]">{d.name ?? 'Unnamed driver'}</span>
                <span className="text-[var(--text-muted)]">{STATUS_LABEL[d.status]}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data && data.length === 0 && (
        <p className="mt-3 text-sm text-[var(--text-muted)]">
          No drivers on this hub yet.
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[var(--text-muted)]">
        {(Object.keys(STATUS_LABEL) as DriverState['status'][]).map((status) => (
          <span key={status} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: STATUS_COLOR[status] }}
            />
            {STATUS_LABEL[status]}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full border-2 border-[var(--text-muted)]" />
          Position over 5 min old
        </span>
      </div>
    </Card>
  )
}
