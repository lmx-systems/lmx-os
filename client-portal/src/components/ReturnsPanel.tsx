import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { formatAge, formatDate, formatReturnStatus } from '../lib/format'
import type { ClientShopView, ReturnItemView } from '../lib/types'

// The counter-facing returns surface (docs/ROADMAP.md W1 slice 4): the
// awaiting-pickup list with an age column so a core never sits forgotten,
// plus a form to flag accumulated cores at a shop as ready for a standalone
// pickup. "Awaiting" hides cores already collected or back at the shop.
export function ReturnsPanel() {
  const [returns, setReturns] = useState<ReturnItemView[] | null>(null)
  const [shops, setShops] = useState<ClientShopView[]>([])
  const [awaitingOnly, setAwaitingOnly] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Flag-cores-ready form.
  const [shopId, setShopId] = useState('')
  const [manifest, setManifest] = useState('')
  const [flagging, setFlagging] = useState(false)

  async function reload() {
    setError(null)
    try {
      setReturns(await api.myReturns(awaitingOnly))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load returns.')
    }
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingOnly])

  useEffect(() => {
    api.myShops().then(setShops).catch(() => setShops([]))
  }, [])

  async function handleFlag(e: React.FormEvent) {
    e.preventDefault()
    setFlagging(true)
    setError(null)
    try {
      await api.flagShopReturns(shopId, manifest.trim())
      setManifest('')
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not flag the cores.')
    } finally {
      setFlagging(false)
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-[16px] font-semibold text-[var(--text-primary)]">Returns &amp; cores</h1>
        <label className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={awaitingOnly}
            onChange={(e) => setAwaitingOnly(e.target.checked)}
          />
          Awaiting pickup only
        </label>
      </div>

      {error && (
        <div className="mb-4 rounded-[var(--radius)] border border-[var(--danger-border,var(--border-strong))] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)]">
          {error}
        </div>
      )}

      <form
        onSubmit={handleFlag}
        className="mb-6 grid grid-cols-1 gap-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]"
      >
        <select
          required
          value={shopId}
          onChange={(e) => setShopId(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          <option value="" disabled>
            Select a shop…
          </option>
          {shops.map((s) => (
            <option key={s.shop_id} value={s.shop_id}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          type="text"
          required
          maxLength={500}
          placeholder="Cores ready for pickup (e.g. 3 alternator cores, 1 starter)"
          value={manifest}
          onChange={(e) => setManifest(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        />
        <button
          type="submit"
          disabled={flagging || !shopId || !manifest.trim()}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-60"
        >
          {flagging ? 'Flagging…' : 'Flag ready'}
        </button>
      </form>

      {returns === null ? (
        <div className="text-sm text-[var(--text-muted)]">Loading returns…</div>
      ) : returns.length === 0 ? (
        <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] px-4 py-8 text-center text-sm text-[var(--text-muted)]">
          {awaitingOnly ? 'Nothing awaiting pickup.' : 'No returns yet.'}
        </div>
      ) : (
        <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
                <th className="px-4 py-2.5">Manifest</th>
                <th className="px-4 py-2.5">Shop</th>
                <th className="px-4 py-2.5">From order</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5 text-right">Age</th>
              </tr>
            </thead>
            <tbody>
              {returns.map((r) => (
                <tr key={r.return_id} className="border-b border-[var(--border)] last:border-0">
                  <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">{r.manifest}</td>
                  <td className="px-4 py-2.5 text-[var(--text-secondary)]">{r.shop_name ?? '—'}</td>
                  <td className="px-4 py-2.5 text-[var(--text-secondary)]">{r.origin_order_ref || '—'}</td>
                  <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatReturnStatus(r.status)}</td>
                  <td
                    className="px-4 py-2.5 text-right text-[var(--text-secondary)]"
                    title={formatDate(r.created_at)}
                  >
                    {formatAge(r.age_hours)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
