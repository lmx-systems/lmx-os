import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, ApiError } from '../lib/api'
import type { ClientOrderResult, ClientShopView, DeadlineChoice } from '../lib/types'

interface NewOrderFormProps {
  onOrderPlaced: () => void
}

// §2.2 principle 4: "Deadline as a choice, not a datetime picker. Nobody at a
// counter operates a calendar widget." These four map to urgency flags the SLA
// engine already reads - the client says how urgent, LMX decides the tier.
const DEADLINES: { value: DeadlineChoice; label: string; hint: string }[] = [
  { value: 'now', label: 'Now', hint: 'Straight there, no waiting' },
  { value: 'within_the_hour', label: 'Within the hour', hint: 'Urgent' },
  { value: 'today', label: 'Today', hint: 'Standard' },
  { value: 'tomorrow', label: 'Tomorrow', hint: 'Scheduled' },
]

/**
 * Place an order (docs/LMX_LINK_PLAN.md §2.2).
 *
 * The seven principles from §2.2 are the specification for this component, and
 * each one is load-bearing rather than decoration:
 *
 *  2. Address first, everything else optional. The drop address is the first
 *     field and the only required one beyond a pickup.
 *  3. Remember every shop. Previous pickups are one tap; a new address is typed
 *     once and then remembered forever.
 *  4. Deadline as a choice, not a datetime picker.
 *  6. Confirmation shows the commitment, not a spinner.
 *  7. Never block on a missing field - contact names, notes and weights are all
 *     optional, and the order goes through without them.
 *
 * NOT built here: principle 5, bulk paste ("a dispatcher with six orders pastes
 * six lines"). It needs a parser, a per-row review table and per-row error
 * handling - closer to the CSV adapter than to this form - and shipping it
 * badly would be worse than not having it. The single-order path is the one a
 * counter person uses all day; bulk is a back-office behaviour that belongs
 * with the manifest drop.
 *
 * §3.4 targets under 30 seconds per order from the second onward. That is
 * measured rather than assumed: `entry_seconds` starts on the first keystroke
 * and is submitted with the order, so the target is checked against real
 * entries instead of a stopwatch in a demo.
 */
export function NewOrderForm({ onOrderPlaced }: NewOrderFormProps) {
  const [shops, setShops] = useState<ClientShopView[] | null>(null)
  const [pickupShopId, setPickupShopId] = useState<string>('')
  const [pickupAddress, setPickupAddress] = useState('')
  const [dropAddress, setDropAddress] = useState('')
  const [dropContact, setDropContact] = useState('')
  const [dropPhone, setDropPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [reference, setReference] = useState('')
  const [deadline, setDeadline] = useState<DeadlineChoice>('today')
  const [showDetails, setShowDetails] = useState(false)

  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [placed, setPlaced] = useState<ClientOrderResult | null>(null)

  // Starts on the first keystroke, not on mount - a form left open in a tab
  // would otherwise report a wildly inflated entry time.
  const startedAt = useRef<number | null>(null)
  function markStarted() {
    if (startedAt.current === null) startedAt.current = Date.now()
  }

  useEffect(() => {
    api
      .myShops()
      .then(setShops)
      .catch(() => setShops([]))
  }, [])

  function reset() {
    setPickupShopId('')
    setPickupAddress('')
    setDropAddress('')
    setDropContact('')
    setDropPhone('')
    setNotes('')
    setReference('')
    setDeadline('today')
    setShowDetails(false)
    startedAt.current = null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const result = await api.submitOrder({
        pickup_shop_id: pickupShopId || null,
        pickup_address: pickupShopId ? null : pickupAddress || null,
        drop_address: dropAddress,
        drop_contact_name: dropContact || null,
        drop_contact_phone: dropPhone || null,
        access_notes: notes || null,
        reference: reference || null,
        deadline,
        entry_seconds:
          startedAt.current !== null ? Math.round((Date.now() - startedAt.current) / 1000) : null,
      })
      setPlaced(result)
      reset()
      onOrderPlaced()
    } catch (err) {
      // The 422s here are actionable - they name which address we couldn't
      // find - so they're shown verbatim rather than replaced with something
      // generic. A counter person can fix a typo in seconds.
      setError(
        err instanceof ApiError && err.status === 422
          ? err.message
          : err instanceof ApiError && err.status === 403
            ? "Your account is still being reviewed — you'll be able to send orders once it's approved."
            : 'Something went wrong. Please try again.',
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (placed) {
    return <OrderConfirmation result={placed} onNext={() => setPlaced(null)} />
  }

  return (
    <form onSubmit={handleSubmit} onInput={markStarted} className="flex flex-col gap-4">
      {/* Principle 2: address first. */}
      <label className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
        Deliver to
        <input
          required
          value={dropAddress}
          onChange={(e) => setDropAddress(e.target.value)}
          placeholder="900 Congress Ave, Austin TX"
          autoComplete="off"
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2.5 text-[15px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
        />
      </label>

      {/* Principle 3: remember every shop. */}
      <div className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
        Collect from
        {shops === null ? (
          <div className="text-xs text-[var(--text-muted)]">Loading your pickup locations…</div>
        ) : (
          <>
            {shops.length > 0 && (
              <div className="mb-1 flex flex-wrap gap-1.5">
                {shops.map((shop) => {
                  const selected = pickupShopId === shop.shop_id
                  return (
                    <button
                      key={shop.shop_id}
                      type="button"
                      onClick={() => {
                        markStarted()
                        setPickupShopId(selected ? '' : shop.shop_id)
                        setPickupAddress('')
                      }}
                      title={shop.address ?? undefined}
                      className={`rounded-full border px-3 py-1.5 text-[13px] transition-colors ${
                        selected
                          ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                          : 'border-[var(--border-strong)] text-[var(--text-secondary)] hover:border-[var(--accent)]'
                      }`}
                    >
                      {shop.name}
                    </button>
                  )
                })}
              </div>
            )}
            {!pickupShopId && (
              <input
                required={shops.length === 0}
                value={pickupAddress}
                onChange={(e) => setPickupAddress(e.target.value)}
                placeholder={shops.length > 0 ? 'Or type a new address' : '1200 E 6th St, Austin TX'}
                autoComplete="off"
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2.5 text-[15px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
            )}
            {!pickupShopId && (
              <span className="text-xs text-[var(--text-muted)]">
                We'll remember it — next time it's one tap.
              </span>
            )}
          </>
        )}
      </div>

      {/* Principle 4: deadline as a choice. */}
      <div className="flex flex-col gap-1.5 text-sm text-[var(--text-secondary)]">
        When
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {DEADLINES.map((option) => {
            const selected = deadline === option.value
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  markStarted()
                  setDeadline(option.value)
                }}
                className={`flex flex-col items-start rounded-[var(--radius)] border px-3 py-2 text-left transition-colors ${
                  selected
                    ? 'border-[var(--accent)] bg-[var(--accent)]/10'
                    : 'border-[var(--border-strong)] hover:border-[var(--accent)]'
                }`}
              >
                <span className="text-[14px] font-medium text-[var(--text-primary)]">{option.label}</span>
                <span className="text-[11px] text-[var(--text-muted)]">{option.hint}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Principle 7: everything below is optional and collapsed by default, so
          the fast path is three fields. */}
      <button
        type="button"
        onClick={() => setShowDetails((v) => !v)}
        className="self-start text-[13px] font-medium text-[var(--accent)] hover:underline"
      >
        {showDetails ? 'Hide details' : 'Add contact, notes or a reference'}
      </button>

      {showDetails && (
        <div className="flex flex-col gap-3 rounded-[var(--radius)] border border-[var(--border)] p-3">
          <Optional label="Who's receiving it" value={dropContact} onChange={setDropContact} placeholder="Name at the delivery address" />
          <Optional label="Their phone" value={dropPhone} onChange={setDropPhone} type="tel" placeholder="For the driver, if needed" />
          <Optional label="Access notes" value={notes} onChange={setNotes} placeholder="Round the back, ring the bell" />
          <Optional label="Your reference" value={reference} onChange={setReference} placeholder="We'll generate one if you skip this" />
        </div>
      )}

      {error && (
        <p role="alert" className="text-[13px] text-[var(--danger,#b3261e)]">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2.5 text-[15px] font-medium text-white disabled:opacity-60"
      >
        {submitting ? 'Sending…' : 'Send this delivery'}
      </button>
    </form>
  )
}

/**
 * Principle 6: "Confirmation shows the commitment, not a spinner. 'Picked up by
 * 2:40, delivered by 3:25.' That is what makes it feel like a carrier."
 *
 * The two times are NOT presented with equal weight, on purpose. Collection is a
 * real commitment from spec-verified SLA windows. The delivery time is an
 * estimate from straight-line distance at a placeholder speed - there is no
 * verified travel-time model until the routing integration makes a live call
 * (E1) - so it is labelled as an estimate and shown quieter. Do not promote it
 * to a promise before that lands.
 */
function OrderConfirmation({ result, onNext }: { result: ClientOrderResult; onNext: () => void }) {
  const time = (iso: string) =>
    new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })

  return (
    <div className="flex flex-col gap-4 rounded-[var(--radius-lg)] border border-[var(--accent)] bg-[var(--surface)] p-5">
      <div>
        <div className="text-[15px] font-semibold text-[var(--text-primary)]">
          Booked — we'll collect by {time(result.collect_by)}
        </div>
        {result.estimated_delivery_by && (
          <div className="mt-0.5 text-[13px] text-[var(--text-muted)]">
            Estimated delivery around {time(result.estimated_delivery_by)}
          </div>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[13px]">
        <dt className="text-[var(--text-muted)]">Reference</dt>
        <dd className="text-right font-mono text-[var(--text-primary)]">{result.reference}</dd>
        {result.fee_cents !== null && (
          <>
            <dt className="text-[var(--text-muted)]">Price</dt>
            <dd className="text-right text-[var(--text-primary)]">
              ${(result.fee_cents / 100).toFixed(2)}
            </dd>
          </>
        )}
      </dl>

      <button
        onClick={onNext}
        className="rounded-[var(--radius)] bg-[var(--accent)] px-4 py-2.5 text-[15px] font-medium text-white"
      >
        Send another
      </button>
    </div>
  )
}

function Optional({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
}) {
  return (
    <label className="flex flex-col gap-1 text-[13px] text-[var(--text-secondary)]">
      {label}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
      />
    </label>
  )
}
