import type { ClientShopView, DeadlineChoice } from '../lib/types'

// §2.2 principle 4: "Deadline as a choice, not a datetime picker. Nobody at a
// counter operates a calendar widget."
//
// Shared between the single-order form and the paste panel deliberately. These
// four values map onto urgency flags the SLA engine reads server-side, so a
// second copy that drifted - a relabelled option, a fifth choice - would quietly
// change which tier a client's order lands in.
export const DEADLINES: { value: DeadlineChoice; label: string; hint: string }[] = [
  { value: 'now', label: 'Now', hint: 'Straight there, no waiting' },
  { value: 'within_the_hour', label: 'Within the hour', hint: 'Urgent' },
  { value: 'today', label: 'Today', hint: 'Standard' },
  { value: 'tomorrow', label: 'Tomorrow', hint: 'Scheduled' },
]

interface DeadlinePickerProps {
  value: DeadlineChoice
  onChange: (v: DeadlineChoice) => void
}

export function DeadlinePicker({ value, onChange }: DeadlinePickerProps) {
  return (
    <div className="flex flex-col gap-1.5 text-sm text-[var(--text-secondary)]">
      When
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {DEADLINES.map((option) => {
          const selected = value === option.value
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
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
  )
}

interface PickupPickerProps {
  shops: ClientShopView[] | null
  shopId: string
  address: string
  onShopId: (v: string) => void
  onAddress: (v: string) => void
}

/**
 * §2.2 principle 3: "Remember every shop. Second order to the same shop is two
 * taps. Most distributors deliver to the same 40-80 shops forever."
 *
 * Chips rather than a dropdown because the realistic count is small and a chip is
 * one tap where a select is three. Includes shops auto-created from a typed
 * address, which is the point - an address typed once is never typed again.
 */
export function PickupPicker({ shops, shopId, address, onShopId, onAddress }: PickupPickerProps) {
  return (
    <div className="flex flex-col gap-1 text-sm text-[var(--text-secondary)]">
      Collect from
      {shops === null ? (
        <div className="text-xs text-[var(--text-muted)]">Loading your pickup locations…</div>
      ) : (
        <>
          {shops.length > 0 && (
            <div className="mb-1 flex flex-wrap gap-1.5">
              {shops.map((shop) => {
                const selected = shopId === shop.shop_id
                return (
                  <button
                    key={shop.shop_id}
                    type="button"
                    onClick={() => {
                      onShopId(selected ? '' : shop.shop_id)
                      onAddress('')
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
          {!shopId && (
            <>
              <input
                required={shops.length === 0}
                value={address}
                onChange={(e) => onAddress(e.target.value)}
                placeholder={shops.length > 0 ? 'Or type a new address' : '1200 E 6th St, Austin TX'}
                autoComplete="off"
                className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface)] px-3 py-2.5 text-[15px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
              />
              <span className="text-xs text-[var(--text-muted)]">
                We'll remember it — next time it's one tap.
              </span>
            </>
          )}
        </>
      )}
    </div>
  )
}
