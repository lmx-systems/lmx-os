import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { AdminClient, ClientRate } from '../lib/types'

/**
 * What a client is charged, and the ability to read it back (docs/ROADMAP.md F5).
 *
 * **A contract term nobody could look up.** The dashboard set rates exactly
 * once, inside signup approval, and `GET`/`PUT /admin/clients/{id}/rates` had
 * no surface at all — so after approval the answer to *"what are we charging
 * them?"* lived only in the database. That is the half that matters when a
 * customer disputes an invoice. Found by
 * `tests/test_no_unreachable_routes.py`.
 *
 * **Nothing listed clients either**, which is why this needed a backend change
 * rather than a form. Four endpoints take a `client_id` and the only way to
 * obtain one was to create a client or read the table — the same shape as the
 * driver-device gap: the action existed and the thing that hands you its
 * argument did not. `GET /admin/hubs/{id}/clients` is new.
 *
 * ## The two things a reader gets wrong without being told
 *
 * **An edit does not reprice anything already taken.** `fee_cents` and
 * `fee_breakdown` are frozen on the order at ingestion, so a card changed today
 * affects the next order and not the last hundred. The natural assumption is the
 * opposite, and an operator who believes it will expect this month's statement
 * to move. Said on the screen, not just in the endpoint's docstring.
 *
 * **The components are additive**, not alternatives:
 * `fee = base + miles×per_mile + pieces×per_piece + weight×per_weight`, floored
 * at the minimum. Courier rates are quoted that way — *"$8 plus $1.50 a mile,
 * minimum $12"* — and a form that implied one-of would make every hybrid
 * contract an approximation.
 *
 * **`rate_tiers: 0` is flagged on the picker.** An approved client with no rate
 * table can submit orders and cannot be invoiced for them, and nothing else on
 * the row would say so.
 */

const TIERS = ['T1', 'T2', 'T3', 'T4'] as const

function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function centsFrom(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed * 100) : 0
}

export function ClientRatesPanel({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [clients, setClients] = useState<AdminClient[] | null>(null)
  const [clientId, setClientId] = useState('')
  const [rates, setRates] = useState<ClientRate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState({ drop: '', mile: '', piece: '', minimum: '' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    if (!hubId) return
    setClientId('')
    setRates(null)
    api
      .listClients(hubId)
      .then((c) => live && setClients(c))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  async function loadRates(id: string) {
    setError(null)
    try {
      setRates(await api.listClientRates(id))
    } catch (e) {
      setError((e as Error).message)
      setRates(null)
    }
  }

  useEffect(() => {
    if (clientId) void loadRates(clientId)
    else setRates(null)
  }, [clientId])

  function startEdit(tier: string, existing: ClientRate | undefined) {
    setEditing(tier)
    setDraft({
      drop: existing ? (existing.rate_per_drop_cents / 100).toFixed(2) : '',
      mile: existing && existing.rate_per_mile_cents ? (existing.rate_per_mile_cents / 100).toFixed(2) : '',
      piece: existing && existing.rate_per_piece_cents ? (existing.rate_per_piece_cents / 100).toFixed(2) : '',
      minimum:
        existing && existing.minimum_charge_cents !== null
          ? (existing.minimum_charge_cents / 100).toFixed(2)
          : '',
    })
  }

  async function save(tier: string) {
    setBusy(true)
    setError(null)
    try {
      await api.upsertClientRate(clientId, {
        sla_tier: tier,
        rate_per_drop_cents: centsFrom(draft.drop),
        rate_per_mile_cents: centsFrom(draft.mile),
        rate_per_piece_cents: centsFrom(draft.piece),
        rate_per_weight_unit_cents: 0,
        minimum_charge_cents: draft.minimum.trim() === '' ? null : centsFrom(draft.minimum),
      })
      setEditing(null)
      await loadRates(clientId)
      onToast(`${tier} saved. It applies to the next order, not to orders already taken.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const byTier = new Map((rates ?? []).map((rate) => [rate.sla_tier, rate]))
  const chosen = clients?.find((c) => c.client_id === clientId)

  return (
    <Card title="What a client pays" meta={clients ? `${clients.length} on this hub` : 'loading…'}>
      {error && <p className="mb-2 text-sm text-[var(--red)]">{error}</p>}

      <select
        value={clientId}
        onChange={(e) => setClientId(e.target.value)}
        className="mb-2 w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
      >
        <option value="">Choose a client…</option>
        {(clients ?? []).map((client) => (
          <option key={client.client_id} value={client.client_id}>
            {client.name}
            {client.rate_tiers === 0 ? ' — no rates set' : ''}
            {client.signup_status !== 'active' ? ` (${client.signup_status})` : ''}
            {!client.active ? ' (inactive)' : ''}
          </option>
        ))}
      </select>

      {chosen && chosen.rate_tiers === 0 && (
        <p className="mb-2 text-[12px] text-[var(--amber)]">
          No rates in force. {chosen.name} can submit orders and cannot be invoiced for
          them.
        </p>
      )}

      {clientId && rates !== null && (
        <>
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-[var(--text-muted)]">
                <th className="py-1 pr-2 font-medium">Tier</th>
                <th className="py-1 pr-2 text-right font-medium">Per drop</th>
                <th className="py-1 pr-2 text-right font-medium">Per mile</th>
                <th className="py-1 pr-2 text-right font-medium">Per piece</th>
                <th className="py-1 pr-2 text-right font-medium">Minimum</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {TIERS.map((tier) => {
                const rate = byTier.get(tier)
                if (editing === tier) {
                  return (
                    <tr key={tier} className="border-t border-[var(--border)]">
                      <td className="py-1 pr-2 text-[var(--text-primary)]">{tier}</td>
                      {(['drop', 'mile', 'piece', 'minimum'] as const).map((field) => (
                        <td key={field} className="py-1 pr-2">
                          <input
                            inputMode="decimal"
                            value={draft[field]}
                            placeholder={field === 'minimum' ? 'none' : '0.00'}
                            onChange={(e) => setDraft({ ...draft, [field]: e.target.value })}
                            className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-right text-[12.5px] text-[var(--text-primary)]"
                          />
                        </td>
                      ))}
                      <td className="py-1 text-right">
                        <button
                          disabled={busy}
                          onClick={() => save(tier)}
                          className="mr-1 rounded-md bg-[var(--accent)] px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40"
                        >
                          Save
                        </button>
                        <button
                          onClick={() => setEditing(null)}
                          className="text-[11px] text-[var(--text-muted)] underline"
                        >
                          Cancel
                        </button>
                      </td>
                    </tr>
                  )
                }
                return (
                  <tr key={tier} className="border-t border-[var(--border)]">
                    <td className="py-1 pr-2 text-[var(--text-primary)]">{tier}</td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-primary)]">
                      {rate ? money(rate.rate_per_drop_cents) : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {rate && rate.rate_per_mile_cents ? money(rate.rate_per_mile_cents) : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {rate && rate.rate_per_piece_cents ? money(rate.rate_per_piece_cents) : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {rate && rate.minimum_charge_cents !== null
                        ? money(rate.minimum_charge_cents)
                        : '—'}
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => startEdit(tier, rate)}
                        className="text-[11px] text-[var(--text-muted)] underline"
                      >
                        {rate ? 'Change' : 'Set'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <p className="mt-2 text-[11px] text-[var(--text-muted)]">
            Components add up: base + miles + pieces, floored at the minimum. A change
            applies to the <strong>next</strong> order — fees are frozen on an order when
            it is taken, so editing a card never moves an invoice that has already been
            priced.
          </p>
        </>
      )}
    </Card>
  )
}
