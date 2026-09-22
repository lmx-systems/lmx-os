import { useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { EMPLOYMENT_TYPES, VEHICLE_TYPES } from '../lib/types'

/**
 * Provision a driver (docs/ROADMAP_AUDIT_2026-09.md).
 *
 * **Nothing created one before this.** Every `Driver` row was a hand-written
 * database insert — while `app/api/driver_routes.py`'s OTP path says in its own
 * comment that *"drivers are provisioned by ops, not self-registered"*. The
 * provisioning it referred to did not exist.
 *
 * Capacity has no pre-filled value on purpose. The column defaults to 1 and the
 * optimizer's capacity check reads it, so a driver provisioned without thinking
 * about it gets one order at a time — the safe direction, and the wrong answer.
 * An empty field asks the question; a pre-filled one answers it for you.
 *
 * The hourly rate is deliberately absent: `scripts/set_driver_rate.py` owns it,
 * and a second writer would be a second place for the number to be wrong. The
 * result says when payroll will fall back to a placeholder, so nobody discovers
 * it at month end.
 */
export function OnboardDriverForm({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [capacity, setCapacity] = useState('')
  const [employment, setEmployment] = useState('w2')
  const [vehicle, setVehicle] = useState('')
  const [plate, setPlate] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const capacityValue = Number(capacity)
  const ready =
    name.trim() !== '' &&
    phone.trim() !== '' &&
    capacity !== '' &&
    Number.isInteger(capacityValue) &&
    capacityValue >= 1

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      const result = await api.onboardDriver({
        hub_id: hubId,
        name: name.trim(),
        phone: phone.trim(),
        vehicle_capacity_units: capacityValue,
        employment_type: employment,
        vehicle_type: vehicle || null,
        plate_number: plate.trim() || null,
      })
      onToast(
        result.hourly_rate_is_placeholder
          ? `${result.name} added. No hourly rate yet — payroll will use the placeholder until scripts/set_driver_rate.py runs.`
          : `${result.name} added.`,
      )
      setName('')
      setPhone('')
      setCapacity('')
      setVehicle('')
      setPlate('')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card title="Add a driver">
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Name" value={name} onChange={setName} placeholder="Sam Okafor" />
          <Field
            label="Phone"
            value={phone}
            onChange={setPhone}
            placeholder="+1 555 555 0100"
            hint="How they log in"
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field
            label="Capacity (units)"
            value={capacity}
            onChange={setCapacity}
            placeholder="how many at once"
            hint="The router will not give them more than this"
          />
          <label className="block">
            <span className="mb-0.5 block text-[11px] font-medium text-[var(--text-muted)]">
              Employment
            </span>
            <select
              value={employment}
              onChange={(e) => setEmployment(e.target.value)}
              className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
            >
              {EMPLOYMENT_TYPES.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-0.5 block text-[11px] font-medium text-[var(--text-muted)]">
              Vehicle (optional)
            </span>
            <select
              value={vehicle}
              onChange={(e) => setVehicle(e.target.value)}
              className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
            >
              <option value="">They set this up in the app</option>
              {VEHICLE_TYPES.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <Field label="Plate (optional)" value={plate} onChange={setPlate} placeholder="" />
        </div>

        {error && <p className="text-[12px] text-[var(--red)]">{error}</p>}

        <button
          onClick={submit}
          disabled={!ready || saving}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? 'Adding…' : 'Add driver'}
        </button>
      </div>
    </Card>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-0.5 block text-[11px] font-medium text-[var(--text-muted)]">
        {label}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
      />
      {hint && <span className="mt-0.5 block text-[10.5px] text-[var(--text-muted)]">{hint}</span>}
    </label>
  )
}
