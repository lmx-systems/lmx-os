import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { DriverDevice, DriverState } from '../lib/types'

/**
 * Which devices a driver is signed in on, and revoking one for them
 * (`docs/ROADMAP_AUDIT_2026-09.md`).
 *
 * **The path this exists for could not be walked.**
 * `DELETE /admin/drivers/{id}/devices/{device_id}` describes itself as *"the
 * driver calls dispatch, ops revokes on their behalf — for when the driver
 * themselves can't (lost phone, no app access)"*. But nothing listed a driver's
 * devices to an admin: `GET /driver/me/devices` is driver-authenticated, and a
 * driver who has lost their phone cannot use it. So ops had to already know a
 * device id they could only have got from the driver — in the one case the
 * route was written for.
 *
 * **Revoking is immediate and cannot be undone from here.** The device id goes
 * into the Redis revocation set and the next request with that token fails; the
 * driver signs in again to get a new one. That is the intended outcome, and it
 * is also why the button asks first: revoking the wrong row signs a working
 * driver out mid-shift.
 *
 * Revoked devices stay listed, dimmed. *"No device"* and *"a device that was
 * revoked on Tuesday"* are different answers to *"why can't this driver sign
 * in"*, and hiding the second leaves somebody guessing.
 *
 * **The driver list is the hub's fleet state**, which holds off-shift drivers but
 * not somebody who has left. Revoking a departed driver's device is the case
 * this cannot reach, and it is named here rather than discovered: it needs a
 * roster read that does not exist yet.
 */

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

export function DriverDevicesPanel({
  drivers,
  onToast,
}: {
  drivers: DriverState[] | null
  onToast: (message: string) => void
}) {
  const [driverId, setDriverId] = useState('')
  const [devices, setDevices] = useState<DriverDevice[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  async function load(id: string) {
    setError(null)
    try {
      setDevices(await api.listDriverDevices(id))
    } catch (e) {
      setError((e as Error).message)
      setDevices(null)
    }
  }

  useEffect(() => {
    if (driverId) void load(driverId)
    else setDevices(null)
  }, [driverId])

  async function revoke(device: DriverDevice) {
    const driver = drivers?.find((d) => d.driver_id === driverId)
    const confirmed = window.confirm(
      `Sign ${driver?.name ?? 'this driver'} out of ${device.device_name ?? 'this device'}?\n\n` +
        'They will have to sign in again. If they are mid-shift, they will lose ' +
        'nothing queued — the outbox survives a sign-out — but they cannot send ' +
        'until they are back in.',
    )
    if (!confirmed) return

    setBusy(device.device_id)
    setError(null)
    try {
      await api.revokeDriverDevice(driverId, device.device_id)
      await load(driverId)
      onToast('Device revoked. The driver signs in again to get a new session.')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title="Driver devices" meta="lost phone, stolen phone, ex-driver">
      <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
        Sign a driver out of a device they can't reach themselves. Drivers on this
        hub's roster only — somebody who has left does not appear.
      </p>

      <select
        value={driverId}
        onChange={(e) => setDriverId(e.target.value)}
        className="mb-2 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
      >
        <option value="">Choose a driver…</option>
        {(drivers ?? []).map((driver) => (
          <option key={driver.driver_id} value={driver.driver_id}>
            {driver.name ?? driver.driver_id}
          </option>
        ))}
      </select>

      {error && <p className="mb-1.5 text-[12px] text-[var(--red)]">{error}</p>}

      {driverId && devices === null && !error && (
        <p className="text-[12px] text-[var(--text-muted)]">loading…</p>
      )}
      {devices?.length === 0 && (
        <p className="text-[12px] text-[var(--text-muted)]">
          No devices. This driver has never signed in on the app.
        </p>
      )}

      {devices && devices.length > 0 && (
        <ul className="space-y-1.5">
          {devices.map((device) => (
            <li
              key={device.device_id}
              className={`flex items-baseline gap-2 border-t border-[var(--border)] pt-1.5 text-[12.5px] ${
                device.revoked_at ? 'text-[var(--text-muted)]' : ''
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[var(--text-primary)]">
                  {device.device_name ?? 'unnamed device'}
                  {device.revoked_at && (
                    <span className="ml-1.5 text-[11px] text-[var(--text-muted)]">
                      revoked {when(device.revoked_at)}
                    </span>
                  )}
                </p>
                <p className="text-[11px] text-[var(--text-muted)]">
                  last seen {when(device.last_seen_at)} · registered{' '}
                  {when(device.registered_at)}
                </p>
              </div>
              {!device.revoked_at && (
                <button
                  disabled={busy === device.device_id}
                  onClick={() => revoke(device)}
                  className="shrink-0 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11px] font-medium text-[var(--red)] disabled:opacity-40"
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
