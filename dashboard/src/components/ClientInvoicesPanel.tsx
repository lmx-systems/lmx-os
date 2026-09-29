import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { AdminClient, ClientInvoice } from '../lib/types'

/**
 * Statements raised against a client, and raising one (docs/ROADMAP.md C3).
 *
 * **Nothing in this system had ever raised an invoice.** `generate_invoice`
 * has exactly one call site — `POST /admin/clients/{id}/invoices/generate` —
 * and that endpoint had no caller in any front end, no scheduler, and no
 * script. Meanwhile the client portal ships a complete invoice viewer: list,
 * detail and PDF, three endpoints reading a table nothing could write. A
 * read-only feature is the mirror image of what
 * `tests/test_no_write_only_columns.py` complains about, and just as empty.
 *
 * It was passing the reachability check on a collision: `/generate` matched two
 * dashboard comments about `docker/generate-env-config.sh`, a shell script.
 * Only visible once comments stopped counting as callers.
 *
 * ## The list is not a nicety, it is the safety
 *
 * `NoBillableOrdersError` protects the *orders* — an order with `invoice_id`
 * set is never swept twice — but it does not protect the operator from
 * believing a period is unbilled when it is not. Two adjacent periods
 * (`1–15`, `15–30`) bill different orders and both succeed, so a mistyped
 * boundary produces a real second statement with a real invoice number rather
 * than an error. The only thing that makes that visible before it happens is
 * seeing what is already there, which is why raising one from this panel is
 * impossible until the existing statements have loaded.
 *
 * ## Dates
 *
 * **`period_end` is exclusive**, matching `generate_invoice`. A month is the
 * 1st to the 1st, and the screen says so beside the field rather than in an
 * endpoint docstring nobody billing a client is reading. The default offered
 * is the previous whole month, because that is what settling a month means and
 * an operator should not have to work out two dates to do the ordinary thing.
 *
 * ## What this does not do
 *
 * No PDF, no send. `GET /client/invoices/{id}/pdf` exists on the client side
 * and an operator-facing copy is a separate question — a statement becomes a
 * document when somebody has decided to send it, and nobody has decided that
 * here yet.
 */

function money(cents: number): string {
  const sign = cents < 0 ? '-' : ''
  return `${sign}$${(Math.abs(cents) / 100).toFixed(2)}`
}

/** The previous whole month as [start, end-exclusive], both `YYYY-MM-DD`. */
export function previousMonth(today: Date): { start: string; end: string } {
  const firstOfThis = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))
  const firstOfLast = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1))
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  // End is the 1st of *this* month, not the 31st of last: the period is
  // half-open, and an inclusive-looking end date is how a month loses a day.
  return { start: iso(firstOfLast), end: iso(firstOfThis) }
}

export function ClientInvoicesPanel({
  hubId,
  onToast,
}: {
  hubId: string
  onToast: (message: string) => void
}) {
  const [clients, setClients] = useState<AdminClient[] | null>(null)
  const [clientId, setClientId] = useState('')
  const [invoices, setInvoices] = useState<ClientInvoice[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [period, setPeriod] = useState(() => previousMonth(new Date()))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    if (!hubId) return
    setClientId('')
    setInvoices(null)
    api
      .listClients(hubId)
      .then((c) => live && setClients(c))
      .catch((e) => live && setError((e as Error).message))
    return () => {
      live = false
    }
  }, [hubId])

  async function loadInvoices(id: string) {
    setError(null)
    try {
      setInvoices(await api.listClientInvoices(id))
    } catch (e) {
      setError((e as Error).message)
      setInvoices(null)
    }
  }

  useEffect(() => {
    if (clientId) void loadInvoices(clientId)
    else setInvoices(null)
  }, [clientId])

  async function generate() {
    if (period.start >= period.end) {
      setError('The period has to end after it starts, and the end date is not included.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const raised = await api.generateClientInvoice(clientId, period.start, period.end)
      await loadInvoices(clientId)
      onToast(`Statement #${raised.invoice_number} raised — ${money(raised.total_cents)} owed.`)
    } catch (e) {
      // A 404 here is not "missing", it is "nothing left to bill" — every
      // delivered order in the period already carries an invoice_id. Saying
      // "not found" would send an operator looking for a broken client.
      const message = (e as Error).message
      setError(
        /404|not found|no billable/i.test(message)
          ? 'Nothing to bill in that period — every delivered order in it is already on a statement.'
          : message,
      )
    } finally {
      setBusy(false)
    }
  }

  const chosen = clients?.find((c) => c.client_id === clientId)
  const overlapping = (invoices ?? []).filter(
    (i) => period.start < i.period_end && period.end > i.period_start,
  )

  return (
    <Card
      title="Statements"
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
            {client.rate_tiers === 0 ? ' — no rates set' : ''}
            {client.signup_status !== 'active' ? ` (${client.signup_status})` : ''}
          </option>
        ))}
      </select>

      {chosen && chosen.rate_tiers === 0 && (
        <p className="mb-2 text-[12px] text-[var(--amber)]">
          {chosen.name} has no rate card, so their delivered orders carry no fee and there
          is nothing to bill. Set rates first.
        </p>
      )}

      {clientId && invoices !== null && (
        <>
          {invoices.length === 0 ? (
            <p className="mb-3 text-[12.5px] text-[var(--text-muted)]">
              No statements raised yet.
            </p>
          ) : (
            <table className="mb-3 w-full text-left text-[12.5px]">
              <thead>
                <tr className="text-[11px] text-[var(--text-muted)]">
                  <th className="py-1 pr-2 font-medium">No.</th>
                  <th className="py-1 pr-2 font-medium">Period</th>
                  <th className="py-1 pr-2 text-right font-medium">Orders</th>
                  <th className="py-1 pr-2 text-right font-medium">Gross</th>
                  <th className="py-1 pr-2 text-right font-medium">Credits</th>
                  <th className="py-1 pr-2 text-right font-medium">Owed</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((invoice) => (
                  <tr key={invoice.invoice_id} className="border-t border-[var(--border)]">
                    <td className="py-1 pr-2 tabular-nums text-[var(--text-primary)]">
                      {invoice.invoice_number}
                    </td>
                    <td className="py-1 pr-2 tabular-nums text-[var(--text-secondary)]">
                      {invoice.period_start} → {invoice.period_end}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {invoice.order_count}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-secondary)]">
                      {money(invoice.gross_cents)}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--amber)]">
                      {invoice.credit_cents ? `−${money(invoice.credit_cents)}` : '—'}
                    </td>
                    <td className="py-1 pr-2 text-right tabular-nums text-[var(--text-primary)]">
                      {money(invoice.total_cents)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="flex flex-wrap items-end gap-2 border-t border-[var(--border)] pt-2">
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] text-[var(--text-muted)]">From</span>
              <input
                type="date"
                value={period.start}
                onChange={(e) => setPeriod({ ...period, start: e.target.value })}
                className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[12.5px] text-[var(--text-primary)]"
              />
            </label>
            <label className="flex flex-col gap-0.5">
              <span className="text-[11px] text-[var(--text-muted)]">Up to (not included)</span>
              <input
                type="date"
                value={period.end}
                onChange={(e) => setPeriod({ ...period, end: e.target.value })}
                className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[12.5px] text-[var(--text-primary)]"
              />
            </label>
            <button
              disabled={busy}
              onClick={generate}
              className="rounded-md bg-[var(--accent)] px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-40"
            >
              {busy ? 'Raising…' : 'Raise statement'}
            </button>
          </div>

          {overlapping.length > 0 && (
            <p className="mt-2 text-[12px] text-[var(--amber)]">
              That period overlaps statement{overlapping.length === 1 ? '' : 's'}{' '}
              {overlapping.map((i) => `#${i.invoice_number}`).join(', ')}. Orders already
              billed will be skipped, so this raises a statement only for whatever was
              missed — check that is what you mean.
            </p>
          )}

          <p className="mt-2 text-[11px] text-[var(--text-muted)]">
            Sweeps delivered, priced, not-yet-billed orders into a new statement. An order
            already on one is never picked up twice, so this is safe to re-run for a later
            period — but it will happily raise a <strong>second</strong> statement for a
            period you have already billed if anything new has landed since.
          </p>
        </>
      )}
    </Card>
  )
}
