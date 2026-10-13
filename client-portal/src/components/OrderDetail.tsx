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
  pin: 'Confirmed with the code on your customer’s tracking page',
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

      <div className="rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-[clamp(26px,3.5vw,36px)] font-extrabold leading-[1.05] tracking-[-0.045em] text-[var(--text-primary)]">
              {order.external_order_ref}
            </h1>
            <div className="mt-1 text-[15px] text-[var(--text-secondary)]">{order.shop_name ?? 'Unknown shop'}</div>
          </div>
          <TierBadge tier={order.sla_tier} />
        </div>

        <DeliveryTrack order={order} />

        {order.duplicate_question_open && (
          <SentTwice order={order} onAnswered={onCancelled} />
        )}

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
          {/* The customer's tracking link. LMX sends no texts, so the client is
              who gets it to their customer; the page also shows the code the
              customer gives the driver at the door. */}
          {order.tracking_url && (
            <div className="col-span-2">
              <dt className="text-xs text-[var(--text-muted)]">Tracking link for your customer</dt>
              <dd className="mt-0.5 flex flex-wrap items-center gap-2 text-[var(--text-secondary)]">
                <a
                  href={order.tracking_url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-[var(--accent)] underline"
                >
                  {order.tracking_url}
                </a>
                <button
                  type="button"
                  onClick={() => void navigator.clipboard?.writeText(order.tracking_url ?? '')}
                  className="rounded-md border border-[var(--border)] px-2 py-0.5 text-xs"
                >
                  Copy
                </button>
              </dd>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                Send this to your customer. It shows where their delivery is and the
                code to give the driver.
              </p>
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
 * "Was this sent twice?" (docs/ROADMAP_1.5.md DEC-13, D-DUP).
 *
 * An order with no reference of its own that matches one you sent minutes
 * earlier, to the same pickup and drop-off. We book both and ask, because only
 * you know: cancel this one if it was a repeat, or say both are real so nobody
 * asks again. Unanswered once a driver is on the way, dispatch calls you instead.
 */
function SentTwice({
  order,
  onAnswered,
}: {
  order: ClientOrderDetailView
  onAnswered: (order: ClientOrderDetailView) => void
}) {
  const [busy, setBusy] = useState<'cancel' | 'keep' | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const canCancel = CANCELLABLE.includes(order.status)

  async function answer(kind: 'cancel' | 'keep') {
    setBusy(kind)
    setRefused(null)
    try {
      onAnswered(kind === 'cancel' ? await api.cancelOrder(order.order_id) : await api.notADuplicate(order.order_id))
    } catch (err) {
      setRefused(err instanceof ApiError ? err.message : 'Could not save your answer. Try again.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mt-4 rounded-[var(--radius)] border border-[var(--amber)] bg-[var(--amber-dim)] px-3 py-2 text-sm">
      <div className="font-medium text-[var(--text-primary)]">Sent twice?</div>
      <div className="text-[var(--text-secondary)]">
        This looks like a repeat of {order.possible_duplicate_of}, booked minutes earlier to the same address.
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {canCancel && (
          <button
            onClick={() => answer('cancel')}
            disabled={busy !== null}
            className="rounded-full bg-[var(--red)] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
          >
            {busy === 'cancel' ? 'Cancelling…' : 'Yes - cancel this one'}
          </button>
        )}
        <button
          onClick={() => answer('keep')}
          disabled={busy !== null}
          className="rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-1.5 text-xs font-semibold text-[var(--text-primary)] disabled:opacity-60"
        >
          {busy === 'keep' ? 'Saving…' : 'No - both are real'}
        </button>
      </div>
      {!canCancel && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">A driver already has it; to cancel, call dispatch.</p>
      )}
      {refused && <p className="mt-2 text-xs text-[var(--text-muted)]">{refused}</p>}
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
            className="rounded-full bg-[var(--red)] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-60"
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

// Where an order is, as the four moments a client cares about. Every status
// before collection is "Booked" from the client's side: held, queued, assigned
// and a driver on the way to collect are our business, not a step of theirs.
const TRACK = ['Booked', 'Collected', 'On the way', 'Delivered'] as const
const TRACK_STEP: Record<string, number> = {
  picked_up: 1,
  en_route_drop: 2,
  delivered: 3,
}
const OFF_TRACK = new Set(['cancelled', 'delivery_failed', 'returned'])

/**
 * The order's progress as four steps (docs/DESIGN_SYSTEM.md): done in lime,
 * the current one in ink, the rest outlined. Not shown for an order that left
 * the track - a failure or a cancellation says so in words above.
 */
function DeliveryTrack({ order }: { order: ClientOrderDetailView }) {
  if (OFF_TRACK.has(order.status)) return null
  const at = TRACK_STEP[order.status] ?? 0
  const when: (string | null)[] = [
    formatDate(order.requested_at),
    // Only once the track says collected: after a failed delivery the order is
    // back to held and the old pickup's time would describe the last attempt.
    at >= 1 && order.collected_at ? formatDate(order.collected_at) : null,
    null,
    order.delivered_at
      ? formatDate(order.delivered_at)
      : order.estimated_delivery_by
        ? `Expected ~${formatDate(order.estimated_delivery_by)}`
        : null,
  ]
  return (
    <ol className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Delivery progress">
      {TRACK.map((label, i) => {
        const done = i < at || (i === at && order.status === 'delivered')
        const current = i === at && !done
        return (
          <li
            key={label}
            aria-current={current ? 'step' : undefined}
            className={`flex flex-col gap-0.5 rounded-[18px] px-4 py-3 ${
              done
                ? 'bg-[var(--lime)] text-[var(--text-primary)]'
                : current
                  ? 'bg-[var(--accent)] text-white'
                  : 'bg-[var(--surface)] text-[var(--text-secondary)] shadow-[inset_0_0_0_1.5px_var(--border)]'
            }`}
          >
            <span className="text-[14px] font-bold">{label}</span>
            <span className={`text-[12px] ${done ? 'text-[var(--lime-ink)]' : current ? 'text-white/80' : 'text-[var(--text-muted)]'}`}>
              {when[i] ?? (current ? formatStatus(order.status) : '\u00a0')}
            </span>
          </li>
        )
      })}
    </ol>
  )
}
