import { useState } from 'react'
import { api } from '../lib/api'
import type { OrderCancellationResult } from '../lib/types'

const HOW: Record<OrderCancellationResult['how'], string> = {
  before_collection: 'cancelled; it was still waiting to go out',
  offer_withdrawn: "cancelled; the driver's offer was withdrawn and the rest of it re-queued",
  stops_removed: 'cancelled; its stops are off the route and the driver has been told',
}

/**
 * Dispatch's cancel. A client's own cancel stops once a driver has the order and
 * tells them to call dispatch; until this there was nothing dispatch could do
 * with the call. Asks once more first, since nothing reopens an order; a 409's
 * message (the parts are already collected) is shown as the server put it.
 */
export function CancelOrderButton({
  orderId,
  label,
  onDone,
  onToast,
}: {
  orderId: string
  label: string
  onDone: () => void
  onToast: (message: string) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)

  async function cancel() {
    setBusy(true)
    setRefused(null)
    try {
      const result = await api.cancelOrderAsDispatch(orderId)
      onToast(`${label} ${HOW[result.how] ?? 'cancelled'}.`)
      onDone()
    } catch (e) {
      setRefused((e as Error).message)
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  const button =
    'rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--red)] hover:text-[var(--text-primary)] disabled:opacity-40'

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {confirming ? (
        <>
          <span className="text-[11px] text-[var(--text-primary)]">Cancel {label}? It can't be reopened.</span>
          <button disabled={busy} onClick={cancel} className={button}>
            {busy ? 'Cancelling…' : 'Yes, cancel it'}
          </button>
          <button disabled={busy} onClick={() => setConfirming(false)} className={button}>
            Keep it
          </button>
        </>
      ) : (
        <button disabled={busy} onClick={() => setConfirming(true)} className={button}>
          Cancel order
        </button>
      )}
      {refused && <span className="text-[11px] text-[var(--red)]">{refused}</span>}
    </span>
  )
}
