import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { formatDate } from '../lib/format'
import type { ApiKeyView } from '../lib/types'

/**
 * API keys for sending us orders from your own system (docs/ORDER_API.md).
 *
 * The mirror of WebhooksPanel: that is how status leaves LMX, this is how orders
 * arrive.
 *
 * **Several live keys is the feature, not clutter.** One key cannot be rotated
 * without downtime — you would have to revoke and re-deploy in the same instant. So
 * the flow this UI is built around is: add a key, deploy it, watch "last used" move
 * to the new one, then revoke the old. That is why `last_used_at` is shown per key
 * rather than tucked away, and why the prefix is shown at all — revoking the wrong
 * key is otherwise a coin flip.
 */
export function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKeyView[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [newToken, setNewToken] = useState<string | null>(null)

  async function reload() {
    setError(null)
    try {
      setKeys(await api.listApiKeys())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load your API keys.')
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setCreating(true)
    setError(null)
    try {
      const created = await api.createApiKey(description || undefined)
      setNewToken(created.token)
      setDescription('')
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create a key.')
    } finally {
      setCreating(false)
    }
  }

  async function revoke(key: ApiKeyView) {
    // Irreversible, and the copy says so rather than relying on the word "revoke"
    // reading as final.
    if (!window.confirm(`Revoke ${key.token_prefix}…? Anything using it stops working immediately.`))
      return
    setBusyId(key.api_key_id)
    setError(null)
    try {
      await api.revokeApiKey(key.api_key_id)
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not revoke that key.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
          Sending us orders from your system
        </h2>
        <p className="mt-1 max-w-2xl text-[13px] text-[var(--text-muted)]">
          Your system POSTs an order to <code className="text-[12px]">/api/v1/orders</code> with an{' '}
          <code className="text-[12px]">X-LMX-Api-Key</code> header. Retries are safe &mdash; sending
          the same reference twice returns the order we already have rather than dispatching a
          second van.
        </p>
      </div>

      {error && <p className="text-[13px] text-[var(--danger,#b42318)]">{error}</p>}

      {newToken && (
        <div className="rounded-[var(--radius)] border border-[var(--accent)] p-4">
          <p className="text-[13px] font-medium text-[var(--text-primary)]">
            Copy this key now &mdash; we can&rsquo;t show it again.
          </p>
          <code className="mt-2 block break-all rounded-[var(--radius)] bg-[var(--surface-2,#f6f6f6)] px-2 py-1 text-[12px]">
            {newToken}
          </code>
          <p className="mt-2 text-[12px] text-[var(--text-muted)]">
            We only store a hash of it, so this really is the only time it exists here. Lost it?
            Create another and revoke this one.
          </p>
          <button
            onClick={() => setNewToken(null)}
            className="mt-3 text-[12px] text-[var(--text-secondary)] underline"
          >
            I&rsquo;ve copied it
          </button>
        </div>
      )}

      <form onSubmit={create} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[12px] text-[var(--text-muted)]">What will use it (optional)</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Counter system"
            className="rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
          />
        </label>
        <button
          type="submit"
          disabled={creating}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-60"
        >
          Create key
        </button>
      </form>

      {keys === null ? (
        <p className="text-[13px] text-[var(--text-muted)]">Loading&hellip;</p>
      ) : keys.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">
          No keys yet. Create one when you&rsquo;re ready to connect your own system.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-[13px]">
            <thead className="text-[12px] text-[var(--text-muted)]">
              <tr>
                <th className="py-1 pr-3 font-medium">Key</th>
                <th className="py-1 pr-3 font-medium">Used for</th>
                <th className="py-1 pr-3 font-medium">Last used</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 font-medium"></th>
              </tr>
            </thead>
            <tbody className="text-[var(--text-secondary)]">
              {keys.map((key) => (
                <tr key={key.api_key_id} className="border-t border-[var(--border)]">
                  <td className="py-2 pr-3">
                    <code className="text-[12px]">{key.token_prefix}…</code>
                  </td>
                  <td className="py-2 pr-3">{key.description ?? '—'}</td>
                  {/* The column rotation depends on. "Never" next to a key you
                      thought was live is the signal that your deploy didn't take. */}
                  <td className="py-2 pr-3">
                    {key.last_used_at ? formatDate(key.last_used_at) : 'Never'}
                  </td>
                  <td className="py-2 pr-3">
                    {key.is_active ? (
                      'Active'
                    ) : (
                      <span className="text-[var(--text-muted)]">
                        Revoked {key.revoked_at ? formatDate(key.revoked_at) : ''}
                      </span>
                    )}
                  </td>
                  <td className="py-2">
                    {key.is_active && (
                      <button
                        disabled={busyId === key.api_key_id}
                        onClick={() => revoke(key)}
                        className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-1 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                      >
                        Revoke
                      </button>
                    )}
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
