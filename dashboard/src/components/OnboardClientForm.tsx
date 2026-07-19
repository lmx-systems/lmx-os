import { useState, type FormEvent } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'

interface OnboardClientFormProps {
  hubId: string
  onToast: (message: string) => void
}

const SLA_TIERS = ['HOT_SHOT', 'T1', 'T2', 'T3'] as const

// Dollar-string rate inputs, keyed by tier - blank means "no rate
// configured for this tier yet" (see app/schemas/admin.py's
// RateOnboardingInput: only tiers with a non-blank value are submitted).
type RateInputs = Record<(typeof SLA_TIERS)[number], string>

const EMPTY_RATES: RateInputs = { HOT_SHOT: '', T1: '', T2: '', T3: '' }

/**
 * Phase 8 minimal client onboarding (docs/ROADMAP.md) - creates a Client,
 * its first shop, its per-tier billing rates, and its client-portal login
 * in one action via POST /admin/clients (app/api/admin_routes.py). There's
 * no separate multi-step admin UI yet, so this single form does all of it.
 */
export function OnboardClientForm({ hubId, onToast }: OnboardClientFormProps) {
  const [name, setName] = useState('')
  const [posSystem, setPosSystem] = useState('flat_file')
  const [shopName, setShopName] = useState('')
  const [shopAddress, setShopAddress] = useState('')
  const [shopLat, setShopLat] = useState('')
  const [shopLng, setShopLng] = useState('')
  const [shopExternalRef, setShopExternalRef] = useState('')
  const [shopPhone, setShopPhone] = useState('')
  const [rates, setRates] = useState<RateInputs>(EMPTY_RATES)
  const [portalEmail, setPortalEmail] = useState('')
  const [portalPassword, setPortalPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const disabled = hubId.length === 0

  function resetForm() {
    setName('')
    setShopName('')
    setShopAddress('')
    setShopLat('')
    setShopLng('')
    setShopExternalRef('')
    setShopPhone('')
    setRates(EMPTY_RATES)
    setPortalEmail('')
    setPortalPassword('')
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setResult(null)

    const lat = Number(shopLat)
    const lng = Number(shopLng)
    if (Number.isNaN(lat) || Number.isNaN(lng)) {
      onToast('Shop latitude/longitude must be numbers')
      return
    }

    const configuredRates = SLA_TIERS.filter((tier) => rates[tier].trim() !== '').map((tier) => ({
      sla_tier: tier,
      rate_per_drop_cents: Math.round(Number(rates[tier]) * 100),
    }))

    setSubmitting(true)
    try {
      const created = await api.onboardClient({
        hub_id: hubId,
        name,
        pos_system: posSystem,
        shops: [
          {
            name: shopName,
            address: shopAddress,
            lat,
            lng,
            external_ref: shopExternalRef,
            phone: shopPhone || undefined,
          },
        ],
        rates: configuredRates,
        portal_email: portalEmail,
        portal_password: portalPassword,
      })
      setResult(`Client ${created.client_id} onboarded with ${created.shop_ids.length} shop(s).`)
      onToast(`Onboarded "${name}" — portal login ready at ${portalEmail}`)
      resetForm()
    } catch (err) {
      onToast(`Onboarding failed: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card title="Onboard a new client" meta="Phase 8 — minimal">
      {disabled && (
        <p className="text-sm text-[var(--text-muted)]">Paste a hub UUID above to onboard a client to it.</p>
      )}

      {!disabled && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-4 text-[13px]">
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Client
            </legend>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Name" value={name} onChange={setName} required placeholder="Customer Warehouse" />
              <label className="flex flex-col gap-1 text-[var(--text-secondary)]">
                POS system
                <select
                  value={posSystem}
                  onChange={(e) => setPosSystem(e.target.value)}
                  className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
                >
                  <option value="flat_file">flat_file</option>
                  <option value="epicor">epicor</option>
                  <option value="mam">mam</option>
                  <option value="asa">asa</option>
                </select>
              </label>
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              First shop
            </legend>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Name" value={shopName} onChange={setShopName} required placeholder="Main Branch" />
              <Field
                label="External ref"
                value={shopExternalRef}
                onChange={setShopExternalRef}
                required
                placeholder="POS shop id"
              />
              <Field label="Address" value={shopAddress} onChange={setShopAddress} required className="col-span-2" />
              <Field label="Latitude" value={shopLat} onChange={setShopLat} required placeholder="34.05" />
              <Field label="Longitude" value={shopLng} onChange={setShopLng} required placeholder="-118.25" />
              <Field label="Phone (optional)" value={shopPhone} onChange={setShopPhone} placeholder="+1…" />
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Per-drop billing rates ($, leave blank if not set yet)
            </legend>
            <div className="grid grid-cols-4 gap-2">
              {SLA_TIERS.map((tier) => (
                <Field
                  key={tier}
                  label={tier === 'HOT_SHOT' ? 'Hot Shot' : tier}
                  value={rates[tier]}
                  onChange={(v) => setRates((r) => ({ ...r, [tier]: v }))}
                  placeholder="18.00"
                />
              ))}
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
              Client portal login
            </legend>
            <div className="grid grid-cols-2 gap-2">
              <Field
                label="Portal email"
                value={portalEmail}
                onChange={setPortalEmail}
                required
                type="email"
                placeholder="ap@customerwarehouse.example"
              />
              <Field
                label="Temporary password"
                value={portalPassword}
                onChange={setPortalPassword}
                required
                type="password"
              />
            </div>
          </fieldset>

          {result && <p className="text-[12.5px] text-[var(--green)]">{result}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-fit rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2 text-[13px] font-medium text-white disabled:opacity-60"
          >
            {submitting ? 'Onboarding…' : 'Onboard client'}
          </button>
        </form>
      )}
    </Card>
  )
}

function Field({
  label,
  value,
  onChange,
  required,
  placeholder,
  type = 'text',
  className = '',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
  placeholder?: string
  type?: string
  className?: string
}) {
  return (
    <label className={`flex flex-col gap-1 text-[var(--text-secondary)] ${className}`}>
      {label}
      <input
        type={type}
        required={required}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-2.5 py-1.5 text-[13px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
      />
    </label>
  )
}
