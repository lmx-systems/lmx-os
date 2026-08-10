import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { ApiKeysPanel } from './ApiKeysPanel'
import { formatDate } from '../lib/format'
import type { WebhookDeliveryView, WebhookEndpointView } from '../lib/types'

/**
 * The integrations surface (docs/ROADMAP.md F4) - admin-only, like Team.
 *
 * Subscribe a URL and LMX POSTs every order status change to it. The two things
 * that make this usable rather than merely present:
 *
 * **The secret is shown once, and the page says so before you create anything.** It
 * signs every request we send, so it is the only way a consumer can tell our call
 * from anyone else's - and it is never returned again by any endpoint, because
 * listing it would put a live signing key in a response and an access log on every
 * page load. A client who loses it deletes the endpoint and makes a new one.
 *
 * **Delivery history is here, not just configuration.** "Did you actually send it?"
 * is the first question of every webhook integration, and without this the honest
 * answer is "check our logs", which a client cannot do. The status code and our
 * error string are shown so they can tell a handler that 500s from a URL that never
 * resolved.
 */
export function WebhooksPanel() {
  const [endpoints, setEndpoints] = useState<WebhookEndpointView[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  // Held in state only until the page is left. This is the one moment it exists
  // outside the database, and the copy below tells the client to save it now.
  const [newSecret, setNewSecret] = useState<string | null>(null)

  const [openHistoryFor, setOpenHistoryFor] = useState<string | null>(null)
  const [deliveries, setDeliveries] = useState<WebhookDeliveryView[] | null>(null)

  async function reload() {
    setError(null)
    try {
      setEndpoints(await api.listWebhooks())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load your integrations.')
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setCreating(true)
    try {
      const created = await api.createWebhook({ url, description: description || undefined })
      setNewSecret(created.secret)
      setUrl('')
      setDescription('')
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add that endpoint.')
    } finally {
      setCreating(false)
    }
  }

  async function toggle(endpoint: WebhookEndpointView) {
    setBusyId(endpoint.endpoint_id)
    setError(null)
    try {
      if (endpoint.is_active) await api.disableWebhook(endpoint.endpoint_id)
      else await api.enableWebhook(endpoint.endpoint_id)
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change that endpoint.')
    } finally {
      setBusyId(null)
    }
  }

  async function showHistory(endpointId: string) {
    if (openHistoryFor === endpointId) {
      setOpenHistoryFor(null)
      return
    }
    setOpenHistoryFor(endpointId)
    setDeliveries(null)
    try {
      setDeliveries(await api.listWebhookDeliveries(endpointId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load delivery history.')
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-[16px] font-semibold text-[var(--text-primary)]">Integrations</h1>

      {/* Orders in first, status out second - the order an integrator actually
          builds them in. */}
      <ApiKeysPanel />

      <hr className="border-[var(--border)]" />

      <div>
        <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">
          Getting status updates back
        </h2>
        <p className="mt-1 max-w-2xl text-[13px] text-[var(--text-muted)]">
          We&rsquo;ll POST a signed JSON message to your system every time one of your orders
          changes status &mdash; collected, on the way, delivered. Every request carries an{' '}
          <code className="text-[12px]">X-LMX-Signature</code> header you can verify with the
          signing secret, and we retry for up to three days if your endpoint is down.
        </p>
      </div>

      {error && <p className="text-[13px] text-[var(--danger,#b42318)]">{error}</p>}

      {newSecret && (
        <div className="rounded-[var(--radius)] border border-[var(--accent)] p-4">
          <p className="text-[13px] font-medium text-[var(--text-primary)]">
            Save this signing secret now &mdash; we can&rsquo;t show it again.
          </p>
          <code className="mt-2 block break-all rounded-[var(--radius)] bg-[var(--surface-2,#f6f6f6)] px-2 py-1 text-[12px]">
            {newSecret}
          </code>
          <p className="mt-2 text-[12px] text-[var(--text-muted)]">
            It never appears in any other response. If you lose it, delete the endpoint and add it
            again.
          </p>
          <button
            onClick={() => setNewSecret(null)}
            className="mt-3 text-[12px] text-[var(--text-secondary)] underline"
          >
            I&rsquo;ve saved it
          </button>
        </div>
      )}

      <form onSubmit={create} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1">
          <span className="text-[12px] text-[var(--text-muted)]">Endpoint URL</span>
          <input
            type="url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://your-system.example.com/lmx-webhook"
            className="rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[12px] text-[var(--text-muted)]">What is it (optional)</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Warehouse system"
            className="rounded-[var(--radius)] border border-[var(--border)] bg-transparent px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
          />
        </label>
        <button
          type="submit"
          disabled={creating}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-60"
        >
          Add endpoint
        </button>
      </form>
      <p className="-mt-4 text-[12px] text-[var(--text-muted)]">
        Must be an https:// address reachable from the internet.
      </p>

      {endpoints === null ? (
        <p className="text-[13px] text-[var(--text-muted)]">Loading&hellip;</p>
      ) : endpoints.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">
          No endpoints yet. Add one above and we&rsquo;ll start sending status updates.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {endpoints.map((endpoint) => (
            <div
              key={endpoint.endpoint_id}
              className="flex flex-col gap-2 rounded-[var(--radius)] border border-[var(--border)] p-3 text-[13px]"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="break-all font-medium text-[var(--text-primary)]">
                  {endpoint.url}
                </span>
                <span
                  className={
                    endpoint.is_active
                      ? 'text-[12px] text-[var(--text-secondary)]'
                      : 'text-[12px] font-medium text-[var(--danger,#b42318)]'
                  }
                >
                  {endpoint.is_active ? 'Active' : 'Paused'}
                </span>
              </div>
              {endpoint.description && (
                <span className="text-[12px] text-[var(--text-muted)]">{endpoint.description}</span>
              )}
              <span className="text-[12px] text-[var(--text-muted)]">
                Added {formatDate(endpoint.created_at)}
                {endpoint.last_success_at
                  ? ` · last delivered ${formatDate(endpoint.last_success_at)}`
                  : ' · nothing delivered yet'}
              </span>
              {/* Why an integration went quiet, said explicitly. Without this a
                  client whose endpoint we switched off concludes we stopped
                  sending, which is the wrong thing to go and debug. */}
              {!endpoint.is_active && endpoint.consecutive_failures > 0 && (
                <span className="text-[12px] text-[var(--danger,#b42318)]">
                  We paused this after {endpoint.consecutive_failures} failed deliveries in a row.
                  Fix your endpoint, then resume it.
                </span>
              )}

              <div className="flex gap-2">
                <button
                  disabled={busyId === endpoint.endpoint_id}
                  onClick={() => toggle(endpoint)}
                  className="rounded-[var(--radius)] border border-[var(--border-strong)] px-3 py-1 text-[12px] text-[var(--text-secondary)] disabled:opacity-60"
                >
                  {endpoint.is_active ? 'Pause' : 'Resume'}
                </button>
                <button
                  onClick={() => showHistory(endpoint.endpoint_id)}
                  className="rounded-[var(--radius)] border border-[var(--border-strong)] px-3 py-1 text-[12px] text-[var(--text-secondary)]"
                >
                  {openHistoryFor === endpoint.endpoint_id ? 'Hide' : 'Recent deliveries'}
                </button>
              </div>

              {openHistoryFor === endpoint.endpoint_id && (
                <div className="mt-1 overflow-x-auto">
                  {deliveries === null ? (
                    <p className="text-[12px] text-[var(--text-muted)]">Loading&hellip;</p>
                  ) : deliveries.length === 0 ? (
                    <p className="text-[12px] text-[var(--text-muted)]">
                      Nothing sent to this endpoint yet.
                    </p>
                  ) : (
                    <table className="w-full min-w-[520px] text-left text-[12px]">
                      <thead className="text-[var(--text-muted)]">
                        <tr>
                          <th className="py-1 pr-3 font-medium">When</th>
                          <th className="py-1 pr-3 font-medium">Result</th>
                          <th className="py-1 pr-3 font-medium">Tries</th>
                          <th className="py-1 font-medium">Detail</th>
                        </tr>
                      </thead>
                      <tbody className="text-[var(--text-secondary)]">
                        {deliveries.map((delivery) => (
                          <tr key={delivery.delivery_id} className="border-t border-[var(--border)]">
                            <td className="py-1 pr-3">{formatDate(delivery.created_at)}</td>
                            <td className="py-1 pr-3">{delivery.status}</td>
                            <td className="py-1 pr-3 tabular-nums">{delivery.attempts}</td>
                            <td className="py-1">
                              {delivery.last_status_code
                                ? `HTTP ${delivery.last_status_code}`
                                : (delivery.last_error ?? '—')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
