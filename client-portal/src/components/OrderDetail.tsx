import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ClientOrderDetailView } from '../lib/types'
import { formatCents, formatDate, formatFailureReason, formatStatus, isFailedStatus } from '../lib/format'
import { TierBadge } from './TierBadge'

interface OrderDetailProps {
  order: ClientOrderDetailView
  onBack: () => void
  onCancelled: (order: ClientOrderDetailView) => void
}

// Before a driver has it. Mirrors app/orders/cancellation.py; the server is the
// judge, this only decides whether to show the button.
const CANCELLABLE = ['received', 'classified', 'held', 'queued']

const PROOF_METHOD: Record<string, string> = {
  photo: 'Photographed at drop-off',
  signature: 'Signed for at drop-off',
  pin: 'Confirmed with the PIN texted to the recipient',
}

// A `local-capture://` marker means nothing was stored (no bucket configured);
// a browser can't load it, and a broken image reads as missing proof.
function loadable(url: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null
}

export function OrderDetail({ order, onBack, onCancelled }: OrderDetailProps) {
  return (
    <div className="flex flex-col gap-4">
      <button
        onClick={onBack}
        className="w-fit text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:text-[var(--text-primary)]"
      >
        ← Back to orders
      </button>

      <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[15px] font-semibold text-[var(--text-primary)]">{order.external_order_ref}</div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">{order.shop_name ?? 'Unknown shop'}</div>
          </div>
          <TierBadge tier={order.sla_tier} />
        </div>

        {isFailedStatus(order.status) && (
          <div className="mt-4 rounded-[var(--radius)] border border-[var(--danger,#b4231f)] bg-[var(--surface-2)] px-3 py-2 text-sm">
            <div className="font-medium text-[var(--danger,#b4231f)]">
              {order.status === 'returned' ? 'Returned to shop' : 'Delivery could not be completed'}
            </div>
            {order.failure_reason && (
              <div className="text-[var(--text-secondary)]">Reason: {formatFailureReason(order.failure_reason)}</div>
            )}
            {order.delivery_attempts > 1 && (
              <div className="text-[var(--text-muted)]">{order.delivery_attempts} delivery attempts</div>
            )}
          </div>
        )}

        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Status</dt>
            <dd className="mt-0.5 font-medium text-[var(--text-primary)]">{formatStatus(order.status)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Fee</dt>
            <dd className="mt-0.5 font-medium text-[var(--text-primary)]">{formatCents(order.fee_cents)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Requested</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.requested_at)}</dd>
          </div>
          {/* The four times a client actually argues about, in the order they happen.
              Two are commitments and two are not, and they are labelled so that reads
              off the screen - the previous version showed a collect-by with no way to
              check it and never showed the delivery target that a service-level credit
              is assessed against. */}
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Collect by</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.collect_by)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Collected</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{formatDate(order.collected_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">Delivery promised by</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">
              {order.promised_delivery_by ? (
                formatDate(order.promised_delivery_by)
              ) : (
                <span title="No service level is on file for this tier, so nothing is promised.">
                  —
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--text-muted)]">
              {order.delivered_at ? 'Delivered' : 'Expected'}
            </dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">
              {order.delivered_at
                ? formatDate(order.delivered_at)
                : order.estimated_delivery_by
                  ? `~${formatDate(order.estimated_delivery_by)}`
                  : '—'}
            </dd>
          </div>
          {/* Their customer's own words, when there are any. Shown as a score out of
              five and the comment verbatim - React escapes it, and it arrives from an
              unauthenticated page, so it is never rendered as markup. */}
          {order.rating && (
            <div className="col-span-2">
              <dt className="text-xs text-[var(--text-muted)]">
                Recipient rating &middot; {formatDate(order.rating.submitted_at)}
              </dt>
              <dd className="mt-0.5 text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">
                  {order.rating.score} / 5
                </span>
                {order.rating.comment && (
                  <span className="mt-1 block italic">&ldquo;{order.rating.comment}&rdquo;</span>
                )}
              </dd>
            </div>
          )}
          {/* How the drop-off was proved, for when their customer says it never came.
              Shown only to the recipient, on the tracking page, until now. */}
          {order.proof && (
            <div className="col-span-2">
              <dt className="text-xs text-[var(--text-muted)]">Proof of delivery</dt>
              <dd className="mt-0.5 text-[var(--text-secondary)]">
                {PROOF_METHOD[order.proof.method ?? ''] ?? 'Recorded'}
                {order.proof.left_at && <> &middot; left at {order.proof.left_at}</>}
                <div className="mt-2 flex flex-wrap gap-3">
                  {order.proof.photo_urls.map(loadable).map(
                    (url) =>
                      url && (
                        <a key={url} href={url} target="_blank" rel="noreferrer">
                          <img
                            src={url}
                            alt="Photo taken at the delivery"
                            loading="lazy"
                            className="max-h-56 max-w-full rounded-[var(--radius)] border border-[var(--border)]"
                          />
                        </a>
                      ),
                  )}
                  {loadable(order.proof.signature_url) && (
                    <img
                      src={loadable(order.proof.signature_url)!}
                      alt="Signature captured at the delivery"
                      loading="lazy"
                      // White ground: a signature is dark ink on transparency, which
                      // disappears entirely on a dark theme.
                      className="max-h-32 max-w-full rounded-[var(--radius)] border border-[var(--border)] bg-white"
                    />
                  )}
                </div>
              </dd>
            </div>
          )}
          <div className="col-span-2">
            <dt className="text-xs text-[var(--text-muted)]">Delivery address</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{order.delivery_address ?? '—'}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-[var(--text-muted)]">Contact</dt>
            <dd className="mt-0.5 text-[var(--text-secondary)]">{order.delivery_contact_name ?? '—'}</dd>
          </div>
        </dl>

        {CANCELLABLE.includes(order.status) && (
          <CancelOrder order={order} onCancelled={onCancelled} />
        )}
      </div>
    </div>
  )
}

/**
 * Take the order back before it's collected. The only cancel anywhere was ops
 * resolving a failed delivery, so an order placed twice went out regardless.
 * Asks once more first, since nothing reopens it.
 */
function CancelOrder({
  order,
  onCancelled,
}: {
  order: ClientOrderDetailView
  onCancelled: (order: ClientOrderDetailView) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)

  async function cancel() {
    setBusy(true)
    setRefused(null)
    try {
      onCancelled(await api.cancelOrder(order.order_id))
    } catch (err) {
      // A 409 says why in words a client can act on ("call dispatch").
      setRefused(err instanceof ApiError ? err.message : 'Could not cancel the order. Try again.')
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-6 border-t border-[var(--border)] pt-4 text-sm">
      {confirming ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[var(--text-primary)]">Cancel this order? It won't be collected.</span>
          <button
            onClick={cancel}
            disabled={busy}
            className="rounded-[var(--radius)] bg-[var(--red)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
          >
            {busy ? 'Cancelling…' : 'Yes, cancel it'}
          </button>
          <button
            onClick={() => setConfirming(false)}
            disabled={busy}
            className="text-xs font-medium text-[var(--text-secondary)] underline"
          >
            Keep it
          </button>
        </div>
      ) : (
        <button
          onClick={() => setConfirming(true)}
          className="text-xs font-medium text-[var(--text-secondary)] underline hover:text-[var(--text-primary)]"
        >
          Cancel this order
        </button>
      )}
      {refused && <p className="mt-2 text-xs text-[var(--text-muted)]">{refused}</p>}
    </div>
  )
}
