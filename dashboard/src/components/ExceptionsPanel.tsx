import { useMemo, useState } from 'react'
import { Clock, Copy, Flag, PackageX, Timer, UserX } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Card } from './ui/Card'
import { Chip } from './ui/Chip'
import { TierBadge } from './ui/Badge'
import { pillButton, primaryButton } from './ui/buttons'
import { api } from '../lib/api'
import { minutesUntil, truncateId } from '../lib/format'
import { AssignDriverControl } from './AssignDriverControl'
import { CancelOrderButton } from './CancelOrderButton'
import { ASSIGNABLE, DISPATCH_CANCELLABLE } from '../lib/orderStatus'
import { OverrideForm } from './OverrideForm'
import { isGoingAhead } from '../lib/exceptions'
import type { DriverState, ExceptionItem, ExceptionQueue, PlaybookCall, ResolutionAction } from '../lib/types'

interface ExceptionsPanelProps {
  data: ExceptionQueue | null
  error: Error | null
  loading: boolean
  // Which list this tile shows; the same polled queue feeds both.
  group: 'needs' | 'soon'
  // Resolving, cancelling, assigning and re-queueing are a dispatcher's on the server.
  canDispatch: boolean
  // The hub's roster, for the assign control's driver picker. The same polled
  // fleet the map and roster read, so all three agree about who is free.
  drivers: DriverState[] | null
  driversError: boolean
  onResolved: () => void
  onToast: (message: string) => void
}

/**
 * What a person has to look at (docs/ROADMAP_1.5.md CON-4), as the first tile
 * on Home.
 *
 * **The clock is shown as minutes, never as a score.** The model that would
 * rank these properly is `M2` - P(a consequence | this order is late) - and it
 * needs hundreds of observed consequences that do not exist. A coloured
 * "urgency" badge would look exactly like that model and would be a guess
 * wearing its clothes, so the only emphasis here is red past an hour, which is
 * the clock and says so.
 *
 * Each row is closed until somebody presses Decide: the list is for seeing
 * how much there is, the open row for doing one of them.
 */

const KIND_LABEL: Record<ExceptionItem['kind'], string> = {
  flagged_by_driver: 'Driver flagged',
  delivery_failed: 'Failed',
  past_promise: 'Past promise',
  released_but_unplaced: 'Nobody has it',
  possible_duplicate: 'Sent twice?',
}

const KIND_ICON: Record<ExceptionItem['kind'], LucideIcon> = {
  flagged_by_driver: Flag,
  delivery_failed: PackageX,
  past_promise: Clock,
  released_but_unplaced: UserX,
  possible_duplicate: Copy,
}

// The order the backend ranks ties in, mirrored so the filter chips read in the
// same sequence a person meets the rows.
const KINDS: (ExceptionItem['kind'] | 'all')[] = [
  'all',
  'flagged_by_driver',
  'delivery_failed',
  'past_promise',
  'released_but_unplaced',
  'possible_duplicate',
]

// An hour past the promise is where a delivery stops being late and starts
// being a phone call. A threshold rather than a gradient because a gradient
// implies a model behind it.
const RED_AFTER_MINUTES = 60

// Some details arrive as the driver app's reason code (SHOP_CLOSED); say it in words.
function plain(detail: string): string {
  if (!/^[A-Z][A-Z_]+$/.test(detail)) return detail
  const words = detail.toLowerCase().replace(/_/g, ' ').replace(/\bcod\b/g, 'COD')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function waited(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`
  const hours = Math.floor(minutes / 60)
  const rest = Math.round(minutes % 60)
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

export function ExceptionsPanel({
  data,
  error,
  loading,
  group,
  canDispatch,
  drivers,
  driversError,
  onResolved,
  onToast,
}: ExceptionsPanelProps) {
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all')
  const [openId, setOpenId] = useState<string | null>(null)

  const mine = useMemo(
    () => (data ? data.items.filter((i) => (group === 'soon') === isGoingAhead(i)) : []),
    [data, group],
  )
  const counts = useMemo(() => {
    const c: Partial<Record<ExceptionItem['kind'], number>> = {}
    for (const i of mine) c[i.kind] = (c[i.kind] ?? 0) + 1
    return c
  }, [mine])
  // The chips show only on a long list. A filter the list has outgrown - its
  // chips hidden, or its kind all resolved - falls back to everything, so a
  // stale choice can never leave the tile empty with no way to clear it.
  const showChips = group === 'needs' && mine.length > 3
  const filter = showChips && kind !== 'all' && counts[kind] ? kind : 'all'
  const rows = useMemo(() => (filter === 'all' ? mine : mine.filter((i) => i.kind === filter)), [mine, filter])

  const title = group === 'needs' ? 'Needs you' : 'Going ahead unless you choose first'
  const meta = loading && !data ? 'loading…' : data ? `${mine.length}` : undefined

  return (
    <Card title={title} meta={meta}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load exceptions: {error.message}</p>}

      {!error && data && mine.length === 0 && (
        // Said in words rather than shown as an empty list. "Nothing
        // outstanding" and "nothing was checked" look identical otherwise, and
        // only one of them means a person can look away.
        group === 'needs' ? (
          <div className="flex flex-col items-center gap-2 rounded-[22px] bg-[var(--surface-2)] px-6 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--lime)]" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
                <path d="m5 12 4 4 10-10" />
              </svg>
            </span>
            <span className="text-lg font-extrabold tracking-[-0.02em]">No orders need you</span>
            <span className="text-sm text-[var(--text-secondary)]">Every order is moving, delivered, or in the playbook's hands.</span>
          </div>
        ) : (
          <p className="text-sm text-[var(--text-secondary)]">Nothing waiting on a countdown.</p>
        )
      )}

      {!error && mine.length > 0 && (
        <>
          {showChips && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {KINDS.filter((k) => k === 'all' || counts[k]).map((k) => (
                <Chip
                  key={k}
                  label={k === 'all' ? `All ${mine.length}` : `${KIND_LABEL[k]} ${counts[k] ?? 0}`}
                  active={filter === k}
                  onClick={() => setKind(k)}
                />
              ))}
            </div>
          )}

          <ul className="flex flex-col gap-2.5">
            {rows.map((item) => {
              const open = openId === item.order_id
              const ref = item.external_ref || truncateId(item.order_id)
              const Icon = group === 'soon' ? Timer : KIND_ICON[item.kind]
              const due = item.playbook?.due_at ? Math.max(0, Math.round(minutesUntil(item.playbook.due_at))) : null
              return (
                <li
                  key={item.order_id}
                  className={`rounded-[22px] bg-[var(--surface-2)] px-4 py-4 ${
                    open ? 'shadow-[inset_0_0_0_1.5px_var(--accent)]' : ''
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
                    <span
                      className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] ${
                        group === 'soon'
                          ? 'bg-[var(--blue-dim)] text-[var(--blue)]'
                          : 'bg-[var(--needs-dim)] text-[var(--needs)]'
                      }`}
                      aria-hidden="true"
                    >
                      <Icon size={20} strokeWidth={2} />
                    </span>
                    <div className="flex min-w-[220px] flex-1 flex-col">
                      <span className="flex flex-wrap items-center gap-2 text-[15.5px] font-bold">
                        {KIND_LABEL[item.kind]} · {ref}
                        {item.sla_tier ? <TierBadge tier={item.sla_tier} /> : null}
                      </span>
                      <span className="text-[13px] text-[var(--text-secondary)]">{plain(item.detail)}</span>
                    </div>
                    {group === 'soon' && due !== null ? (
                      <span className="rounded-full bg-[var(--blue-dim)] px-2.5 py-1 text-xs font-bold tabular-nums text-[var(--blue)]">
                        {due > 0 ? `acts in ${due}m` : 'acts next pass'}
                      </span>
                    ) : (
                      <span
                        className={`text-xs font-semibold tabular-nums ${
                          item.minutes_waiting >= RED_AFTER_MINUTES ? 'text-[var(--red)]' : 'text-[var(--text-muted)]'
                        }`}
                      >
                        {waited(item.minutes_waiting)}
                      </span>
                    )}
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => setOpenId(open ? null : item.order_id)}
                      className={open ? pillButton : primaryButton}
                    >
                      {open ? 'Close' : group === 'soon' ? 'Look' : 'Decide'}
                    </button>
                  </div>

                  {open && (
                    <div className="mt-4 flex flex-wrap gap-5 border-t border-[var(--border)] pt-4">
                      <div className="flex min-w-[240px] flex-1 flex-col gap-1.5 text-[13.5px] text-[var(--text-secondary)]">
                        <span className="font-semibold text-[var(--text-primary)]">{item.next_action}</span>
                        {item.playbook && <PlaybookLine call={item.playbook} />}
                      </div>
                      <div className="flex min-w-[240px] flex-1 flex-col items-start gap-2">
                        {!canDispatch && (
                          <span className="text-[13px] text-[var(--text-muted)]">Your account can read this but not act on it.</span>
                        )}
                        {canDispatch && item.status === 'delivery_failed' && (
                          <Resolve item={item} onResolved={onResolved} onToast={onToast} />
                        )}
                        {canDispatch && ASSIGNABLE.includes(item.status) && (
                          // On any row whose order is still waiting for a driver,
                          // not only "nobody has it": a held order past its promise
                          // is listed under that kind first and needs a driver just
                          // the same.
                          <AssignDriverControl
                            orderId={item.order_id}
                            label={ref}
                            drivers={drivers}
                            driversError={driversError}
                            onDone={onResolved}
                            onToast={onToast}
                            offerBonusCents={data!.offer_bonus_cents}
                          />
                        )}
                        {canDispatch && item.status === 'queued' && (
                          <PutBack item={item} onResolved={onResolved} onToast={onToast} />
                        )}
                        {canDispatch &&
                          item.status !== 'delivery_failed' &&
                          DISPATCH_CANCELLABLE.includes(item.status) && (
                            // A failed delivery's cancel is one of its resolutions
                            // above; an order already collected can only be returned.
                            <CancelOrderButton orderId={item.order_id} label={ref} onDone={onResolved} onToast={onToast} />
                          )}
                      </div>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>

          {group === 'needs' && (
            <p className="mt-3 text-xs text-[var(--text-muted)]">
              Oldest first. That is the clock, not a prediction of which will turn into a call.
            </p>
          )}
        </>
      )}
    </Card>
  )
}

const ACTION_LABEL: Record<string, string> = {
  redeliver: 'redeliver',
  return_to_shop: 'return it to the shop',
  cancel: 'cancel',
  // The reason that follows names them.
  offer: 'offer it to a free driver',
  offer_bonus: 'offer it again with a bonus',
}

/**
 * The playbook's call on this order (docs/ROADMAP_1.5.md DEC-8).
 *
 * The rule that is replacing this decision says what it would do and why. In
 * recommend mode that is all it does - the buttons below are still the
 * decision, and choosing the same or otherwise is how the rule earns more
 * authority. In veto mode it says when it will act unless somebody does first.
 * An escalation is the rule saying this one is a person's.
 */
function PlaybookLine({ call }: { call: PlaybookCall }) {
  const action = ACTION_LABEL[call.action] ?? call.action.replace(/_/g, ' ')
  let lead: string
  if (call.outcome === 'escalated') {
    lead = 'Playbook: a person decides this one.'
  } else if (call.mode === 'recommend' || call.due_at === null) {
    lead = `Playbook recommends: ${action}.`
  } else {
    const minutes = Math.round(minutesUntil(call.due_at))
    lead =
      minutes > 0
        ? `Playbook will ${action} in ${minutes}m unless you choose first.`
        : `Playbook will ${action} on its next pass unless you choose first.`
  }
  return (
    <p className="text-[13px] text-[var(--text-secondary)]">
      <span className="font-semibold text-[var(--text-primary)]">{lead}</span> {call.reason}
    </p>
  )
}

const RESOLUTIONS: {
  action: ResolutionAction
  label: string
  done: string
  // Asked once more before it happens, because nothing reopens the order after.
  confirm: string | null
}[] = [
  { action: 'redeliver', label: 'Redeliver', done: 'is back in the hold queue for another attempt', confirm: null },
  {
    action: 'return_to_shop',
    label: 'Return to shop',
    done: 'is going back to the shop',
    confirm: 'Send the parts back to the shop? This closes the order.',
  },
  {
    action: 'cancel',
    label: 'Cancel order',
    done: 'is cancelled',
    confirm: "Cancel this order? It can't be reopened.",
  },
]

/**
 * What becomes of a failed delivery (docs/ROADMAP.md R5).
 *
 * The row told a dispatcher to "resolve it (retry, return to shop, or cancel)"
 * and offered no way to, though the endpoint existed: a failed order sat here
 * until somebody called the API by hand.
 */
function Resolve({
  item,
  onResolved,
  onToast,
}: {
  item: ExceptionItem
  onResolved: () => void
  onToast: (message: string) => void
}) {
  const [confirming, setConfirming] = useState<(typeof RESOLUTIONS)[number] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)

  async function resolve(choice: (typeof RESOLUTIONS)[number]) {
    setBusy(true)
    setFailed(null)
    try {
      await api.resolveOrder(item.order_id, choice.action)
      onToast(`${item.external_ref || truncateId(item.order_id)} ${choice.done}.`)
      onResolved()
    } catch (e) {
      setFailed((e as Error).message)
    } finally {
      setBusy(false)
      setConfirming(null)
    }
  }

  const button = pillButton

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {confirming ? (
        <>
          <span className="text-[12.5px] text-[var(--text-primary)]">{confirming.confirm}</span>
          <button disabled={busy} onClick={() => resolve(confirming)} className={button}>
            {busy ? 'Working…' : `Yes, ${confirming.label.toLowerCase()}`}
          </button>
          <button disabled={busy} onClick={() => setConfirming(null)} className={button}>
            Keep it
          </button>
        </>
      ) : (
        RESOLUTIONS.map((choice) => (
          <button
            key={choice.action}
            disabled={busy}
            onClick={() => (choice.confirm ? setConfirming(choice) : resolve(choice))}
            className={button}
          >
            {choice.label}
          </button>
        ))
      )}
      {failed && <span className="text-[12.5px] text-[var(--red)]">Couldn't resolve: {failed}</span>}
    </div>
  )
}

/**
 * Put a released order back under the hold queue's rules - the hold override
 * (docs/ROADMAP_1.5.md CON-2), with its mandatory reason. Only a `queued` order:
 * that is what a dispatcher's release produced, and the server refuses hold
 * from anything else.
 *
 * Behind a disclosure because it is the less common answer to "nobody has it",
 * and says so when it would do little: once the hold deadline has passed, the
 * next cycle releases the order again on its own.
 */
function PutBack({
  item,
  onResolved,
  onToast,
}: {
  item: ExceptionItem
  onResolved: () => void
  onToast: (message: string) => void
}) {
  const [open, setOpen] = useState(false)
  const label = item.external_ref || truncateId(item.order_id)
  const holdHasRunOut = item.hold_deadline !== null && minutesUntil(item.hold_deadline) <= 0

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className={pillButton}>
        Put back in the queue
      </button>
    )
  }
  return (
    <OverrideForm
      orderId={item.order_id}
      action="hold"
      warning={
        holdHasRunOut
          ? 'The hold has run out - the next cycle sends it straight back. This only undoes the release.'
          : undefined
      }
      onDone={() => {
        onToast(`${label} is back in the hold queue.`)
        onResolved()
      }}
    />
  )
}
