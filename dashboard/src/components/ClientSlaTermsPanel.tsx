import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { AdminClient, ClientSlaTerm } from '../lib/types'

/**
 * What a client was promised, and what missing it costs (docs/ROADMAP.md W3).
 *
 * **The half of the contract nobody could read back.** `GET` and
 * `PUT /admin/clients/{id}/sla-terms` both existed, both were tested, and
 * neither had a caller — terms were set by `scripts/set_client_sla_terms.py`
 * and after that the answer to *"what did we agree to?"* lived only in the
 * database. That is exactly the question a client asks when they dispute a
 * credit, and the moment an operator most needs an answer they can read off a
 * screen. Found by `tests/test_no_unreachable_routes.py`, which is now two
 * entries shorter.
 *
 * Beneath the rates panel deliberately: price and promise are one contract,
 * and a credit is computed from both — `credit_percent` is a percentage of the
 * fee the rate card produced. Reading one without the other tells you half of
 * what you owe.
 *
 * ## Three things a reader gets wrong without being told
 *
 * **The target runs from when the order reached us**, not from pickup. The
 * client knows when they sent it; they do not know when our driver happened to
 * collect it, and a promise measured from a moment they cannot see is not one
 * they can hold us to. It is also the only timestamp guaranteed present on
 * every order regardless of path.
 *
 * **A tier with no term is not a gap in the data.** It is a tier we made no
 * promise on, so nothing is credited and `credit_exposure` reports it apart
 * from nothing owed. The screen says "no promise" rather than a dash, because
 * a dash reads as missing and would invite somebody to invent a term.
 *
 * **Nothing here is retroactive.** A term recorded today governs breaches
 * assessed after it, and assessment reads the term in force. The natural
 * assumption on an editable table is the opposite.
 *
 * ## Why the credit column is mostly empty, and correctly so
 *
 * Phase `0.7` — whether LMX accepts outcome liability at all — is open, and
 * `DEC-1`'s row says the credit percentages wait on it. So a real client with
 * `0%` is not an oversight: it is the state the company is actually in until
 * three founders decide otherwise. `PLACEHOLDER_SLA_TERMS` in
 * `app/models/client_sla_term.py` carries a reasoned starting point and its own
 * warning that nobody agreed to it, which is why those figures are shown here
 * as greyed placeholder text in an empty input and never pre-filled. A
 * placeholder somebody saves by pressing Save becomes a term, and the whole
 * point of the constant is that it is not one.
 */

// Same four as the rate card, and the same reason: `sla_tier` is a plain string
// in both tables so a new tier does not need a migration before terms can be
// agreed for it. These are the four that exist today.
const TIERS = ['HOT_SHOT', 'T1', 'T2', 'T3'] as const

// From PLACEHOLDER_SLA_TERMS. Shown as input placeholders only — never a value,
// never saved by default. See the note above.
const SUGGESTED: Record<string, { minutes: number; percent: number }> = {
  HOT_SHOT: { minutes: 60, percent: 100 },
  T1: { minutes: 90, percent: 50 },
  T2: { minutes: 180, percent: 25 },
  T3: { minutes: 1440, percent: 0 },
}

function money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/**
 * Cents, `null` for a deliberately empty field, `undefined` for nonsense.
 *
 * Three states rather than two on purpose. Folding a mistyped figure into
 * `null` would store "no floor" for what somebody meant as a floor, and the
 * save would succeed — a silent wrong answer on a contract term, which is the
 * one kind this screen must not produce.
 */
function centsFrom(value: string): number | null | undefined {
  if (value.trim() === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return undefined
  return Math.round(parsed * 100)
}

/** "90 min" reads fine; "1440 min" does not. Hours and days where they help. */
function duration(minutes: number): string {
  if (minutes < 90) return `${minutes} min`
  if (minutes < 1440) {
    const hours = minutes / 60
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} hr`
  }
  const days = minutes / 1440
  return `${Number.isInteger(days) ? days : days.toFixed(1)} day${days === 1 ? '' : 's'}`
}

export function ClientSlaTermsPanel({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [clients, setClients] = useState<AdminClient[] | null>(null)
  const [clientId, setClientId] = useState('')
  const [terms, setTerms] = useState<ClientSlaTerm[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState({ minutes: '', percent: '', floor: '', ceiling: '' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    if (!hubId) return
    setClientId('')
    setTerms(null)
    api
      .listClients(hubId)
      .then((c) => live && setClients(c))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  async function loadTerms(id: string) {
    setError(null)
    try {
      setTerms(await api.listClientSlaTerms(id))
    } catch (e) {
      setError((e as Error).message)
      setTerms(null)
    }
  }

  useEffect(() => {
    if (clientId) void loadTerms(clientId)
    else setTerms(null)
  }, [clientId])

  function startEdit(tier: string, existing: ClientSlaTerm | undefined) {
    setEditing(tier)
    // Empty for a tier with no term, so the suggested figures stay placeholders.
    setDraft({
      minutes: existing ? String(existing.delivery_target_minutes) : '',
      percent: existing ? String(existing.credit_percent) : '',
      floor:
        existing && existing.credit_minimum_cents !== null
          ? (existing.credit_minimum_cents / 100).toFixed(2)
          : '',
      ceiling:
        existing && existing.credit_maximum_cents !== null
          ? (existing.credit_maximum_cents / 100).toFixed(2)
          : '',
    })
  }

  async function save(tier: string) {
    // Every one of these would 422. Checked here so the operator reads a
    // sentence rather than a Pydantic blob, and — the reason that matters —
    // so no mistyped figure is quietly stored as something else.
    const minutes = Number(draft.minutes)
    if (!Number.isFinite(minutes) || minutes <= 0) {
      setError('A delivery target in minutes is required — a credit needs something to breach.')
      return
    }
    const percent = draft.percent.trim() === '' ? 0 : Number(draft.percent)
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      setError('The credit must be between 0 and 100 percent of the order fee.')
      return
    }
    const floor = centsFrom(draft.floor)
    const ceiling = centsFrom(draft.ceiling)
    if (floor === undefined || ceiling === undefined) {
      setError('A credit floor or ceiling must be an amount, or left empty for none.')
      return
    }
    if (floor !== null && ceiling !== null && floor > ceiling) {
      setError("The credit minimum can't be more than the maximum.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.upsertClientSlaTerm(clientId, {
        sla_tier: tier,
        delivery_target_minutes: Math.round(minutes),
        credit_percent: Math.round(percent),
        credit_minimum_cents: floor,
        credit_maximum_cents: ceiling,
      })
      setEditing(null)
      await loadTerms(clientId)
      // Refresh the picker's count, so "no promise" clears the moment it stops
      // being true rather than at the next hub switch.
      api.listClients(hubId).then(setClients).catch(() => undefined)
      onToast(`${tier} recorded. It governs breaches assessed from now on, not past ones.`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const byTier = new Map((terms ?? []).map((term) => [term.sla_tier, term]))
  const chosen = clients?.find((c) => c.client_id === clientId)

  return (
    <Card
      title="What a client was promised"
      meta={clients ? `${clients.length} on this hub` : 'loading…'}
    >
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
            {client.sla_term_tiers === 0 ? ' — no promise on any tier' : ''}
            {client.signup_status !== 'active' ? ` (${client.signup_status})` : ''}
            {!client.active ? ' (inactive)' : ''}
          </option>
        ))}
      </select>

      {chosen && chosen.sla_term_tiers === 0 && (
        <p className="mb-2 text-[12px] text-[var(--text-muted)]">
          No delivery commitment on record for {chosen.name}. Nothing is credited when we
          are late, which may be exactly right — it is what the contract says, not a gap
          in the data.
        </p>
      )}

      {clientId && terms !== null && (
        <>
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-[var(--text-muted)]">
                <th className="py-1 pr-2 font-medium">Tier</th>
                <th className="py-1 pr-2 text-right font-medium">Deliver within</th>
                <th className="py-1 pr-2 text-right font-medium">Credit</th>
                <th className="py-1 pr-2 text-right font-medium">Floor</th>
                <th className="py-1 pr-2 text-right font-medium">Ceiling</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {TIERS.map((tier) => {
                const term = byTier.get(tier)
                if (editing === tier) {
                  const hint = SUGGESTED[tier]
                  return (
                    <tr key={tier} className="border-t border-[var(--border)]">
                      <td className="py-1 pr-2 text-[var(--text-primary)]">{tier}</td>
                      <td className="py-1 pr-2">
                        <input
                          inputMode="numeric"
                          aria-label={`${tier} delivery target in minutes`}
                          value={draft.minutes}
                          placeholder={hint ? String(hint.minutes) : 'min'}
                          onChange={(e) => setDraft({ ...draft, minutes: e.target.value })}
                          className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-right text-[12.5px] text-[var(--text-primary)]"
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <input
                          inputMode="numeric"
                          aria-label={`${tier} credit percent`}
                          value={draft.percent}
                          placeholder={hint ? `${hint.percent}%` : '%'}
                          onChange={(e) => setDraft({ ...draft, percent: e.target.value })}
                          className="w-full rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-right text-[12.5px] text-[var(--text-primary)]"
                        />
                      </td>
                      {(['floor', 'ceiling'] as const).map((field) => (
                        <td key={field} className="py-1 pr-2">
                          <input
                            inputMode="decimal"
                            aria-label={`${tier} credit ${field}`}
                            value={draft[field]}
                            placeholder="none"
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
                          onClick={() => {
                            setEditing(null)
                            setError(null)
                          }}
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
                      {term ? duration(term.delivery_target_minutes) : 'no promise'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {term ? `${term.credit_percent}%` : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {term && term.credit_minimum_cents !== null
                        ? money(term.credit_minimum_cents)
                        : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {term && term.credit_maximum_cents !== null
                        ? money(term.credit_maximum_cents)
                        : '—'}
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => startEdit(tier, term)}
                        className="text-[11px] text-[var(--text-muted)] underline"
                      >
                        {term ? 'Change' : 'Record'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <p className="mt-2 text-[11px] text-[var(--text-muted)]">
            The clock starts when the order <strong>reached us</strong>, not at pickup —
            the client knows when they sent it. A credit is that percentage of the order's
            own fee, so it scales with what the rate card charged. A term recorded now
            governs breaches assessed from now on; it does not reprice past ones.
          </p>
        </>
      )}
    </Card>
  )
}
