import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import type {
  ClientOrderBatchResult,
  ClientShopView,
  DeadlineChoice,
} from '../lib/types'
import { DeadlinePicker, PickupPicker } from './orderFields'

interface BulkPastePanelProps {
  onOrdersPlaced: () => void
}

// Matches MAX_BATCH_ROWS in app/schemas/client_order.py. Capped because every
// genuinely new address costs a call to a geocoder limited to one per second, so
// an unbounded paste would hold the request open for minutes.
const MAX_ROWS = 25

interface ParsedRow {
  drop_address: string
  reference?: string
  drop_contact_name?: string
}

/**
 * Parse a paste into rows.
 *
 * §2.2 principle 5 is "a dispatcher with six orders pastes six lines. Parse
 * them, show what was understood, let them fix it." The middle clause is the
 * important one - this parser is deliberately shallow, and everything it decided
 * is shown back for correction rather than trusted.
 *
 * One line is one order. Fields split on TAB first, because the realistic source
 * is a spreadsheet and tab-separated is what a copy from Excel actually
 * produces. Comma is the fallback for a hand-typed list - but only when there is
 * no tab, because addresses contain commas ("900 Congress Ave, Austin TX") and
 * splitting those on comma would shred them. That asymmetry is the whole trick.
 */
export function parsePaste(text: string): ParsedRow[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      if (line.includes('\t')) {
        const [address, reference, contact] = line.split('\t').map((f) => f.trim())
        return {
          drop_address: address,
          reference: reference || undefined,
          drop_contact_name: contact || undefined,
        }
      }
      // No tab: treat the whole line as the address. Commas inside an address
      // are far more common than a comma used as a field separator, and
      // guessing wrong here turns one order into two broken ones.
      return { drop_address: line }
    })
    .filter((row) => row.drop_address.length > 0)
}

export function BulkPastePanel({ onOrdersPlaced }: BulkPastePanelProps) {
  const [shops, setShops] = useState<ClientShopView[] | null>(null)
  const [shopId, setShopId] = useState('')
  const [pickupAddress, setPickupAddress] = useState('')
  const [deadline, setDeadline] = useState<DeadlineChoice>('today')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<ClientOrderBatchResult | null>(null)

  const startedAt = useRef<number | null>(null)

  useEffect(() => {
    api
      .myShops()
      .then(setShops)
      .catch(() => setShops([]))
  }, [])

  const rows = useMemo(() => parsePaste(text), [text])
  const overCap = rows.length > MAX_ROWS

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (rows.length === 0) {
      setError('Paste one delivery address per line to get started.')
      return
    }
    if (overCap) {
      setError(`That's ${rows.length} lines — send up to ${MAX_ROWS} at a time.`)
      return
    }

    setSubmitting(true)
    try {
      const batch = await api.submitOrdersBatch({
        pickup_shop_id: shopId || null,
        pickup_address: shopId ? null : pickupAddress || null,
        deadline,
        rows,
        entry_seconds:
          startedAt.current !== null ? Math.round((Date.now() - startedAt.current) / 1000) : null,
      })
      setResult(batch)
      onOrdersPlaced()
      // Keep only the lines that failed, so "fix it and resend" is literally
      // pressing send again rather than re-pasting and deleting the ones that
      // already went.
      const failedAddresses = batch.results.filter((r) => r.error).map((r) => r.drop_address)
      setText(failedAddresses.join('\n'))
      if (failedAddresses.length === 0) startedAt.current = null
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "Your account is still being reviewed — you'll be able to send orders once it's approved."
          : 'Something went wrong. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {result && <BatchReport result={result} onDismiss={() => setResult(null)} />}

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
          Deliver to — one address per line
          <textarea
            value={text}
            onChange={(e) => {
              if (startedAt.current === null) startedAt.current = Date.now()
              setText(e.target.value)
            }}
            rows={6}
            placeholder={'900 Congress Ave, Austin TX\n500 W 2nd St, Austin TX\n1100 Red River St, Austin TX'}
            className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2.5 font-mono text-[13px] leading-relaxed text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          />
          <span className="text-xs text-[var(--text-muted)]">
            Pasting from a spreadsheet? Extra columns are read as your reference and the contact name.
          </span>
        </label>

        {/* Principle 5's "show what was understood". The count is the cheapest
            honest version of that - it tells a dispatcher immediately if a stray
            blank line or a wrapped address changed how many orders they're
            about to send. */}
        {rows.length > 0 && (
          <div
            className={`rounded-[var(--radius)] border px-3 py-2 text-[13px] ${
              overCap
                ? 'border-[var(--danger,#b3261e)] text-[var(--danger,#b3261e)]'
                : 'border-[var(--border)] text-[var(--text-secondary)]'
            }`}
          >
            {overCap
              ? `${rows.length} lines — that's over the ${MAX_ROWS} we can send at once.`
              : `Understood ${rows.length} ${rows.length === 1 ? 'delivery' : 'deliveries'}.`}
            {!overCap && rows.some((r) => r.reference) && ' References picked up from your columns.'}
          </div>
        )}

        <PickupPicker
          shops={shops}
          shopId={shopId}
          address={pickupAddress}
          onShopId={setShopId}
          onAddress={setPickupAddress}
        />
        <DeadlinePicker value={deadline} onChange={setDeadline} />

        {error && (
          <p role="alert" className="text-[13px] text-[var(--danger,#b3261e)]">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || rows.length === 0 || overCap}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2.5 text-[15px] font-medium text-white disabled:opacity-60"
        >
          {submitting
            ? 'Sending…'
            : `Send ${rows.length || ''} ${rows.length === 1 ? 'delivery' : 'deliveries'}`.trim()}
        </button>

        {submitting && rows.length > 4 && (
          <p className="text-xs text-[var(--text-muted)]">
            Addresses we haven't seen before take a moment to look up — this can take a few seconds.
          </p>
        )}
      </form>
    </div>
  )
}

/**
 * The per-line report.
 *
 * Partial success is the normal case, not an edge one, so this shows both halves
 * at once rather than either a success screen or an error screen. The failed
 * lines are left in the textarea above so fixing them is pressing send again.
 */
function BatchReport({
  result,
  onDismiss,
}: {
  result: ClientOrderBatchResult
  onDismiss: () => void
}) {
  const failed = result.results.filter((r) => r.error)

  return (
    <div
      className={`flex flex-col gap-3 rounded-[var(--radius-lg)] border p-4 ${
        failed.length === 0 ? 'border-[var(--accent)]' : 'border-[var(--border-strong)]'
      }`}
    >
      <div className="text-[15px] font-semibold text-[var(--text-primary)]">
        {failed.length === 0
          ? `Booked all ${result.accepted}.`
          : `Booked ${result.accepted} of ${result.accepted + result.failed}.`}
      </div>

      {failed.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="text-[13px] text-[var(--text-secondary)]">
            These lines need a look — they're still in the box above, so fix them and press send again:
          </div>
          <ul className="flex flex-col gap-1">
            {failed.map((row) => (
              <li key={row.index} className="text-[13px] text-[var(--text-primary)]">
                <span className="font-mono text-[12px] text-[var(--text-muted)]">
                  line {row.index + 1}
                </span>{' '}
                {row.drop_address}
                <div className="text-[12px] text-[var(--danger,#b3261e)]">{row.error}</div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.accepted > 0 && (
        <details className="text-[13px] text-[var(--text-secondary)]">
          <summary className="cursor-pointer">Show the {result.accepted} booked</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {result.results
              .filter((r) => r.order)
              .map((row) => (
                <li key={row.index} className="flex justify-between gap-3">
                  <span className="truncate">{row.drop_address}</span>
                  <span className="shrink-0 font-mono text-[12px] text-[var(--text-muted)]">
                    {row.order!.reference}
                  </span>
                </li>
              ))}
          </ul>
        </details>
      )}

      <button
        onClick={onDismiss}
        className="self-start text-[13px] font-medium text-[var(--accent)] hover:underline"
      >
        Dismiss
      </button>
    </div>
  )
}
