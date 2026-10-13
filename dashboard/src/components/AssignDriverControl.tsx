import { useState } from 'react'
import { api } from '../lib/api'
import { truncateId } from '../lib/format'
import type { AssignmentResult, DriverState } from '../lib/types'

const STATUS_WORDS: Record<DriverState['status'], string> = {
  off_shift: 'off shift',
  available: 'available',
  offered: 'answering an offer',
  en_route: 'on a route',
  on_break: 'on break',
}

function nameOf(driver: DriverState): string {
  return driver.name || truncateId(driver.driver_id)
}

function hasRoom(driver: DriverState): boolean {
  // The server's test: one order's worth of room (app/orders/assignment.py).
  return driver.capacity_units - driver.load_units >= 1
}

// Whether the server will offer this driver the order, read off the same fields
// it reads. The server is still the judge; this decides what the picker offers.
function canBeOffered(driver: DriverState): boolean {
  return hasRoom(driver) && driver.status === 'available' && !driver.current_route_id
}

function why(driver: DriverState): string {
  if (!hasRoom(driver)) return 'van full'
  if (driver.status === 'available' && driver.current_route_id) return 'finishing a route'
  // Working routes are added to by the cycle's in-flight insertion, not by hand (D-APPEND).
  if (driver.status === 'en_route') return 'on a route - the cycle adds to working routes'
  return STATUS_WORDS[driver.status] ?? driver.status
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function doneText(label: string, driver: DriverState, result: AssignmentResult): string {
  const stale = result.driver_position_stale ? " (their app hasn't reported lately - call them)" : ''
  const bonus = result.bonus_cents ? ` with a ${dollars(result.bonus_cents)} bonus` : ''
  return `${label} offered to ${nameOf(driver)}${bonus} - expires ${clock(result.offer_expires_at)}${stale}.`
}

function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/**
 * Give an order nobody has to a driver (app/orders/assignment.py).
 *
 * The exceptions row said "assign it" and offered only Cancel. An idle driver
 * is *offered* the order and may decline it. Everyone else is listed but can't
 * be picked, with the reason - a driver on a route among them, since working
 * routes are added to by the cycle (D-APPEND) - so "nobody can take it" is something a
 * dispatcher can read rather than infer from an empty list.
 *
 * The inline confirm-then-act pattern of CancelOrderButton. A refusal (the
 * driver went on break since the roster was polled, documents lapsed, the order
 * was placed by the cycle meanwhile) is shown in red as the server put it.
 */
export function AssignDriverControl({
  orderId,
  label,
  drivers,
  driversError,
  onDone,
  onToast,
  offerBonusCents = null,
}: {
  orderId: string
  label: string
  drivers: DriverState[] | null
  // The roster fetch failed. With `drivers` null that means "we don't know who is
  // on shift" - not "nobody is", which is what an empty picker would say.
  driversError: boolean
  onDone: () => void
  onToast: (message: string) => void
  // The placeholder bonus an offer can carry (DEC-10), from the server. Null
  // hides the option.
  offerBonusCents?: number | null
}) {
  const [open, setOpen] = useState(false)
  const [chosenId, setChosenId] = useState('')
  const [withBonus, setWithBonus] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)

  const roster = [...(drivers ?? [])].sort((a, b) => nameOf(a).localeCompare(nameOf(b)))
  const offers = roster.filter(canBeOffered)
  const unavailable = roster.filter((d) => !canBeOffered(d))
  const chosen = roster.find((d) => d.driver_id === chosenId && canBeOffered(d))

  async function assign() {
    if (!chosen) return
    setBusy(true)
    setRefused(null)
    try {
      const result = await api.assignOrder(orderId, chosen.driver_id, withBonus)
      onToast(doneText(label, chosen, result))
      setOpen(false)
      setChosenId('')
      onDone()
    } catch (e) {
      setRefused(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const button =
    'rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40'

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className={button}>
        Assign to a driver
      </button>
    )
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <select
        value={chosenId}
        disabled={busy}
        onChange={(e) => {
          setChosenId(e.target.value)
          setRefused(null)
        }}
        className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[11px] text-[var(--text-primary)]"
      >
        <option value="">
          {drivers === null
            ? driversError
              ? "Couldn't load the roster - try again in a moment"
              : 'Loading drivers…'
            : roster.length === 0
              ? 'No drivers on the roster'
              : 'Choose a driver…'}
        </option>
        {offers.length > 0 && (
          <optgroup label="Offer to (they accept or decline)">
            {offers.map((d) => (
              <option key={d.driver_id} value={d.driver_id}>
                {nameOf(d)}
              </option>
            ))}
          </optgroup>
        )}
        {unavailable.length > 0 && (
          <optgroup label="Can't take work now">
            {unavailable.map((d) => (
              <option key={d.driver_id} value={d.driver_id} disabled>
                {nameOf(d)} — {why(d)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
      {chosen && (
        <>
          <span className="text-[11px] text-[var(--text-primary)]">
            {`Offer ${label} to ${nameOf(chosen)}? They can decline it.`}
          </span>
          {offerBonusCents ? (
            // For an order already turned down: the playbook's step-up. Owed to
            // the driver once delivered; the amount is a placeholder.
            <label className="inline-flex items-center gap-1 text-[11px] text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={withBonus}
                disabled={busy}
                onChange={(e) => setWithBonus(e.target.checked)}
              />
              {`Add a ${dollars(offerBonusCents)} bonus`}
            </label>
          ) : null}
          <button disabled={busy} onClick={assign} className={button}>
            {busy ? 'Assigning…' : 'Yes, offer it'}
          </button>
        </>
      )}
      <button
        disabled={busy}
        onClick={() => {
          setOpen(false)
          setChosenId('')
          setRefused(null)
        }}
        className={button}
      >
        Not now
      </button>
      {refused && <span className="text-[11px] text-[var(--red)]">{refused}</span>}
    </span>
  )
}
