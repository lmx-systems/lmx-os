import { useState, type FormEvent } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { HUB_TIME_ZONES, US_STATE_CODES } from '../lib/types'
import type { HubSettings } from '../lib/types'

const FIELD =
  'w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60'
const LABEL = 'mb-0.5 block text-[11px] font-medium text-[var(--text-muted)]'

/**
 * Create a hub (`POST /admin/hubs`).
 *
 * **A fresh deploy had no way in.** The console shows nothing until a hub is
 * selected, and the only way to make the first one was an API call with an ops
 * token, spelled out in the runbook. Shown wherever no hub is selected, so it is
 * also how a second hub gets added.
 *
 * Coordinates rather than an address: they are what dispatch measures every
 * distance from, and a geocoded guess would put the hub wherever the geocoder
 * thought the street was. The time zone is chosen from a list for the reason the
 * state is: the hub's clock decides its pay periods, invoices and closed days.
 */
export function CreateHubPanel({
  isAdmin,
  onCreated,
}: {
  isAdmin: boolean
  onCreated: (hub: HubSettings) => void
}) {
  const [name, setName] = useState('')
  const [timezone, setTimezone] = useState('')
  const [lat, setLat] = useState('')
  const [lng, setLng] = useState('')
  const [stateCode, setStateCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!isAdmin) {
    return (
      <p className="text-center text-[12px] text-[var(--text-muted)]">
        If the hub you need isn&rsquo;t listed, an admin can create it here.
      </p>
    )
  }

  const latitude = Number(lat)
  const longitude = Number(lng)
  const coordinatesValid =
    lat.trim() !== '' &&
    lng.trim() !== '' &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    Math.abs(latitude) <= 90 &&
    Math.abs(longitude) <= 180
  const ready = name.trim() !== '' && timezone !== '' && coordinatesValid

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!ready) return
    setSaving(true)
    setError(null)
    try {
      const hub = await api.createHub({
        name: name.trim(),
        timezone,
        lat: latitude,
        lng: longitude,
        state_code: stateCode || null,
      })
      onCreated(hub)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="Create a hub">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <label className="block sm:col-span-2" htmlFor="new-hub-name">
          <span className={LABEL}>Name</span>
          <input
            id="new-hub-name"
            value={name}
            maxLength={120}
            disabled={saving}
            onChange={(e) => setName(e.target.value)}
            className={FIELD}
          />
        </label>

        <label className="block" htmlFor="new-hub-lat">
          <span className={LABEL}>Latitude</span>
          <input
            id="new-hub-lat"
            inputMode="decimal"
            placeholder="40.7128"
            value={lat}
            disabled={saving}
            onChange={(e) => setLat(e.target.value)}
            className={FIELD}
          />
        </label>
        <label className="block" htmlFor="new-hub-lng">
          <span className={LABEL}>Longitude</span>
          <input
            id="new-hub-lng"
            inputMode="decimal"
            placeholder="-74.0060"
            value={lng}
            disabled={saving}
            onChange={(e) => setLng(e.target.value)}
            className={FIELD}
          />
        </label>
        <p className="text-[11px] text-[var(--text-muted)] sm:col-span-2">
          Where drivers leave from and come back to. Right-click the building in a map app to
          copy its coordinates.
        </p>

        <label className="block" htmlFor="new-hub-timezone">
          <span className={LABEL}>Time zone</span>
          <select
            id="new-hub-timezone"
            value={timezone}
            disabled={saving}
            onChange={(e) => setTimezone(e.target.value)}
            className={FIELD}
          >
            <option value="">Choose…</option>
            {HUB_TIME_ZONES.map(({ zone, label }) => (
              <option key={zone} value={zone}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="block" htmlFor="new-hub-state">
          <span className={LABEL}>State</span>
          <select
            id="new-hub-state"
            value={stateCode}
            disabled={saving}
            onChange={(e) => setStateCode(e.target.value)}
            className={FIELD}
          >
            <option value="">Not set</option>
            {US_STATE_CODES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </select>
        </label>
        <p className="text-[11px] text-[var(--text-muted)] sm:col-span-2">
          The time zone sets the hub&rsquo;s day for pay periods, invoices and closed days. The
          state picks the drivers&rsquo; overtime rule; it can be set later in hub settings.
        </p>

        {error && <p className="text-[12px] text-[var(--red)] sm:col-span-2">{error}</p>}

        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={!ready || saving}
            className="rounded-[var(--radius)] border border-[var(--accent)] bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-50"
          >
            {saving ? 'Creating…' : 'Create hub'}
          </button>
        </div>
      </form>
    </Card>
  )
}
