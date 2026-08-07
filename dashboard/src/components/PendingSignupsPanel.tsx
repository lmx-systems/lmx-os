import { useCallback, useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api, ApiError } from '../lib/api'
import type { PendingSignupView } from '../lib/types'

interface PendingSignupsPanelProps {
  hubId: string
  onToast: (message: string) => void
}

const TIERS = ['HOT_SHOT', 'T1', 'T2', 'T3'] as const

/**
 * Public-signup review (docs/LMX_LINK_PLAN.md).
 *
 * The gate that keeps a self-serve signup form compatible with LMX being an
 * operator rather than self-serve SaaS - roadmap item C5 recorded signup as
 * deliberately absent, and this is what preserves that decision's substance:
 * anyone can apply, nobody dispatches an LMX van until someone here says so.
 *
 * Rates are entered as part of approving rather than afterwards, and that is
 * deliberate. This is the only moment somebody is already looking at the client
 * and deciding commercial terms, and doing it here means an active client always
 * has rates - so their orders can never price as null (Order.fee_cents is
 * explicit that null must never look like a free delivery).
 */
export function PendingSignupsPanel({ hubId, onToast }: PendingSignupsPanelProps) {
  const [signups, setSignups] = useState<PendingSignupView[] | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [rates, setRates] = useState<Record<string, Record<string, string>>>({})

  const load = useCallback(async () => {
    try {
      setSignups(await api.listSignups('pending'))
    } catch (err) {
      onToast(`Could not load signups: ${err instanceof ApiError ? err.message : String(err)}`)
    }
  }, [onToast])

  useEffect(() => {
    void load()
  }, [load])

  function setRate(clientId: string, tier: string, value: string) {
    setRates((prev) => ({ ...prev, [clientId]: { ...(prev[clientId] ?? {}), [tier]: value } }))
  }

  async function approve(signup: PendingSignupView) {
    const entered = rates[signup.client_id] ?? {}
    const parsed = TIERS.map((tier) => ({ tier, raw: entered[tier] }))
      .filter((r) => r.raw !== undefined && r.raw.trim() !== '')
      .map((r) => ({ sla_tier: r.tier, rate_per_drop_cents: Math.round(Number(r.raw) * 100) }))

    // Checked here as well as server-side so the reason is immediate rather
    // than a 422 - approving a client we can't bill is the mistake this whole
    // panel exists to prevent.
    if (parsed.length === 0) {
      onToast('Set at least one per-tier rate before approving — an approved client must be billable.')
      return
    }
    if (parsed.some((r) => !Number.isFinite(r.rate_per_drop_cents) || r.rate_per_drop_cents < 0)) {
      onToast('Rates must be positive amounts in dollars.')
      return
    }

    setBusyId(signup.client_id)
    try {
      await api.approveSignup(signup.client_id, {
        rates: parsed,
        // The hub chosen at signup is provisional - hubs have no service-area
        // model, so this confirms it against whatever hub the operator is
        // currently looking at.
        hub_id: hubId.length > 0 ? hubId : undefined,
      })
      onToast(`${signup.company_name} approved — they can sign in and order now.`)
      await load()
    } catch (err) {
      onToast(`Could not approve: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  async function reject(signup: PendingSignupView) {
    setBusyId(signup.client_id)
    try {
      await api.rejectSignup(signup.client_id)
      onToast(`${signup.company_name} declined.`)
      await load()
    } catch (err) {
      onToast(`Could not decline: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title="Signup requests" meta="LMX Link — approve before they can order">
      {signups === null ? (
        <p className="text-sm text-[var(--text-muted)]">Loading signup requests…</p>
      ) : signups.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">
          No one waiting. New applicants from the signup link appear here before they can send orders.
        </p>
      ) : (
        <div className="flex flex-col gap-3 text-[13px]">
          {signups.map((signup) => (
            <div
              key={signup.client_id}
              className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] p-3"
            >
              <div className="flex flex-col gap-1">
                <div className="font-medium text-[var(--text-primary)]">{signup.company_name}</div>
                <div className="text-[12px] text-[var(--text-secondary)]">
                  {signup.contact_name}
                  {signup.contact_email ? ` · ${signup.contact_email}` : ''}
                  {signup.contact_phone ? ` · ${signup.contact_phone}` : ''}
                </div>
                <div className="text-[12px] text-[var(--text-muted)]">
                  Delivers around {signup.service_area ?? 'unspecified'} · applied{' '}
                  {new Date(signup.submitted_at).toLocaleDateString()}
                  {signup.terms_version ? ` · accepted terms ${signup.terms_version}` : ''}
                </div>
              </div>

              <div className="flex flex-wrap items-end gap-2">
                {TIERS.map((tier) => (
                  <label key={tier} className="flex flex-col gap-1">
                    <span className="text-[11px] uppercase tracking-wide text-[var(--text-muted)]">
                      {tier}
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="0.01"
                      inputMode="decimal"
                      placeholder="$ / drop"
                      value={rates[signup.client_id]?.[tier] ?? ''}
                      onChange={(e) => setRate(signup.client_id, tier, e.target.value)}
                      className="w-24 rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1 text-[12px] text-[var(--text-primary)]"
                    />
                  </label>
                ))}
              </div>

              <div className="flex gap-2">
                <button
                  disabled={busyId === signup.client_id}
                  onClick={() => approve(signup)}
                  className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-60"
                >
                  Approve &amp; set rates
                </button>
                <button
                  disabled={busyId === signup.client_id}
                  onClick={() => reject(signup)}
                  className="rounded-[var(--radius)] border border-[var(--border-strong)] px-3 py-1.5 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                >
                  Decline
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
