import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ClientShopView, DeadlineChoice, ManifestUploadResult } from '../lib/types'
import { DeadlinePicker, PickupPicker } from './orderFields'

interface ManifestUploadPanelProps {
  onOrdersPlaced: () => void
}

/**
 * Upload a CSV manifest (docs/LMX_LINK_PLAN.md T3).
 *
 * The paste path covers a dispatcher with six orders in their clipboard. This covers the
 * one with a file — an export from their counter system, which is what they actually
 * have when the number gets past a handful.
 *
 * **The report is the feature, not the upload.** T3's exit criterion is "a 40-row
 * manifest imports, with bad rows reported and good rows dispatched", and the second
 * clause is the hard part: a dispatcher who uploads 40 lines and gets 38 deliveries with
 * no account of the other two has lost orders they still believe are coming. So every
 * line comes back, keyed on the line number in their own spreadsheet, and the failures
 * are listed first within that.
 *
 * The column mapping is shown too. The parser matches headers generously — `Ship To
 * Address`, `Customer Address`, `DESTINATION` — and a dispatcher should be able to see
 * which column we read as the destination rather than wonder why every delivery is going
 * to the same place.
 */
export function ManifestUploadPanel({ onOrdersPlaced }: ManifestUploadPanelProps) {
  const [file, setFile] = useState<File | null>(null)
  const [deadline, setDeadline] = useState<DeadlineChoice>('today')
  const [shops, setShops] = useState<ClientShopView[] | null>(null)
  const [shopId, setShopId] = useState('')
  const [typedPickup, setTypedPickup] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ManifestUploadResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.myShops().then(setShops).catch(() => setShops([]))
  }, [])

  const canSubmit = file !== null && (shopId !== '' || typedPickup.trim().length > 2)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const uploaded = await api.uploadManifest(file, {
        deadline,
        pickupShopId: shopId || null,
        pickupAddress: shopId ? null : typedPickup.trim(),
      })
      setResult(uploaded)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      if (uploaded.accepted > 0) onOrdersPlaced()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not read that file.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {result && <ManifestReport result={result} onDismiss={() => setResult(null)} />}

      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
          Manifest file
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv,text/plain"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-[13px] text-[var(--text-primary)]"
          />
          <span className="text-xs text-[var(--text-muted)]">
            A CSV with one delivery per row. We look for a delivery address column and read
            an order number and contact name if they&rsquo;re there.
          </span>
        </label>

        <PickupPicker
          shops={shops}
          shopId={shopId}
          address={typedPickup}
          onShopId={setShopId}
          onAddress={setTypedPickup}
        />
        <DeadlinePicker value={deadline} onChange={setDeadline} />

        {error && <p className="text-[13px] text-[var(--danger,#b3261e)]">{error}</p>}

        <button
          type="submit"
          disabled={!canSubmit || busy}
          className="self-start rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2 text-[13px] font-medium text-white disabled:opacity-60"
        >
          {busy ? 'Importing…' : 'Import manifest'}
        </button>
      </form>
    </div>
  )
}

function ManifestReport({
  result,
  onDismiss,
}: {
  result: ManifestUploadResult
  onDismiss: () => void
}) {
  const failed = result.results.filter((row) => row.error)

  return (
    <div
      className={`flex flex-col gap-3 rounded-[var(--radius-lg)] border p-4 ${
        failed.length === 0 ? 'border-[var(--accent)]' : 'border-[var(--border-strong)]'
      }`}
    >
      <div className="text-[15px] font-semibold text-[var(--text-primary)]">
        {failed.length === 0
          ? `${result.accepted} deliveries booked`
          : `${result.accepted} booked, ${failed.length} need attention`}
      </div>

      {/* Which column we read as what. The parser is generous about headers, so this is
          how a dispatcher confirms we understood their export. */}
      <div className="text-xs text-[var(--text-muted)]">
        Read{' '}
        {Object.entries(result.column_mapping)
          .map(([field, header]) => `“${header}” as ${field.replace(/_/g, ' ')}`)
          .join(', ')}
      </div>

      {failed.length > 0 && (
        <div className="flex flex-col gap-1">
          {/* Failures first, because they're the only part with anything to do. */}
          {failed.map((row) => (
            <div key={row.line_number} className="text-[13px] text-[var(--danger,#b3261e)]">
              <span className="font-medium">Row {row.line_number}</span>
              {row.drop_address ? ` · ${row.drop_address}` : ''} — {row.error}
            </div>
          ))}
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Fix these rows in your file and upload just those, or add them one at a time.
          </p>
        </div>
      )}

      <button onClick={onDismiss} className="self-start text-[13px] text-[var(--accent)] underline">
        Import another
      </button>
    </div>
  )
}
