import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { AdminDriver, DriverDevice } from '../lib/types'

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
 * **The driver list is the hub's roster**, switched-off drivers included. It
 * used to be the fleet state, which never holds somebody who has left, so the
 * driver this panel most needed to reach was the one it could not list.
 *
 * **Switching a driver off** ends every session at once and refuses any new
 * sign-in. It is how ops removes somebody who has left; revoking devices one
 * at a time missed any phone nobody had listed. The server refuses it while
 * the driver holds a route or an unanswered offer, and says which.
 */

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

export function DriverDevicesPanel({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [drivers, setDrivers] = useState<AdminDriver[] | null>(null)
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

  async function loadRoster() {
    try {
      setDrivers(await api.listHubDrivers(hubId))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    setDriverId('')
    setDrivers(null)
    void loadRoster()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hubId])

  useEffect(() => {
    if (driverId) void load(driverId)
    else setDevices(null)
  }, [driverId])

  const selected = drivers?.find((d) => d.driver_id === driverId) ?? null

  async function switchOff(driver: AdminDriver) {
    const confirmed = window.confirm(
      `Switch ${driver.name} off?\n\n` +
        'Every phone they are signed in on stops working on its next request, and ' +
        'they cannot sign in again until somebody switches them back on. Use this ' +
        'when a driver leaves.',
    )
    if (!confirmed) return
    setBusy('driver')
    setError(null)
    try {
      await api.deactivateDriver(driver.driver_id)
      await Promise.all([loadRoster(), load(driver.driver_id)])
      onToast(`${driver.name} is switched off. Every session has ended.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function switchOn(driver: AdminDriver) {
    setBusy('driver')
    setError(null)
    try {
      await api.reactivateDriver(driver.driver_id)
      await loadRoster()
      onToast(`${driver.name} is switched back on. They sign in again on their phone.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function revoke(device: DriverDevice) {
    const confirmed = window.confirm(
      `Sign ${selected?.name ?? 'this driver'} out of ${device.device_name ?? 'this device'}?\n\n` +
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
        Sign a driver out of a device they can't reach themselves, or switch off
        somebody who has left.
      </p>

      <select
        value={driverId}
        onChange={(e) => setDriverId(e.target.value)}
        className="mb-2 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
      >
        <option value="">Choose a driver…</option>
        {(drivers ?? []).map((driver) => (
          <option key={driver.driver_id} value={driver.driver_id}>
            {driver.name}
            {driver.is_active ? '' : ' (switched off)'}
          </option>
        ))}
      </select>

      {error && <p className="mb-1.5 text-[12px] text-[var(--red)]">{error}</p>}

      {selected && (
        <div className="mb-2 flex items-baseline gap-2 text-[12px]">
          <p className="min-w-0 flex-1 text-[var(--text-secondary)]">
            {selected.is_active
              ? 'Active.'
              : `Switched off ${when(selected.deactivated_at)}. No session works and no sign-in succeeds.`}
          </p>
          {selected.is_active ? (
            <button
              disabled={busy === 'driver'}
              onClick={() => switchOff(selected)}
              className="shrink-0 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11px] font-medium text-[var(--red)] disabled:opacity-40"
            >
              Switch off
            </button>
          ) : (
            <button
              disabled={busy === 'driver'}
              onClick={() => switchOn(selected)}
              className="shrink-0 rounded-md border border-[var(--border)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-primary)] disabled:opacity-40"
            >
              Switch back on
            </button>
          )}
        </div>
      )}

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
