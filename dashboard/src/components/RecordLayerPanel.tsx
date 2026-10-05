import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import { truncateId } from '../lib/format'
import type { ConsequenceOption, LateOrder, LinkageFlag } from '../lib/types'

/**
 * The two things the record layer needs a person for (docs/ROADMAP_1.5.md REC-2, REC-4).
 *
 * **What happened after a late delivery**, and **the questions the linkage
 * detectors raised**. Both existed as tested modules with no caller and no
 * reader until `docs/ROADMAP_AUDIT_2026-09.md`.
 *
 * These are here rather than in the exception queue on purpose. The exception
 * queue is what to do *now* — it is read under pressure and sorted by a clock.
 * This is what to record about what already happened, and mixing them would put
 * a fourteen-day-old question in front of somebody deciding what to dispatch in
 * the next minute.
 *
 * Fetched on demand rather than polled on the dashboard tick. The flags change
 * once a night, and a late delivery stays on its list for two weeks, so loading
 * when somebody opens the tab is enough; polling would be a request every few
 * seconds for lists that barely move.
 */
export function RecordLayerPanel({ hubId, canDispatch }: { hubId: string; canDispatch: boolean }) {
  const [late, setLate] = useState<LateOrder[] | null>(null)
  const [flags, setFlags] = useState<LinkageFlag[] | null>(null)
  const [kinds, setKinds] = useState<ConsequenceOption[]>([])
  const [error, setError] = useState<Error | null>(null)

  async function load() {
    try {
      const [l, f, k] = await Promise.all([
        api.lateOrders(hubId),
        api.linkageFlags(hubId),
        api.consequenceKinds(),
      ])
      setLate(l)
      setFlags(f)
      setKinds(k)
    } catch (e) {
      setError(e as Error)
    }
  }

  useEffect(() => {
    if (hubId) void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hubId])

  const nothingToDo = late?.length === 0 && flags?.length === 0

  return (
    <Card
      title="To record"
      meta={
        late && flags
          ? `${late.length} to judge · ${flags.length} question${flags.length === 1 ? '' : 's'}`
          : 'loading…'
      }
    >
      {error && <p className="text-sm text-[var(--red)]">Couldn't load: {error.message}</p>}

      {nothingToDo && (
        <p className="py-4 text-center text-sm text-[var(--text-muted)]">
          Nothing to record. A late delivery appears here when it's delivered and stays for two weeks.
        </p>
      )}

      {late && late.length > 0 && (
        <section className="mb-4">
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            What happened after these ran late?
          </h3>
          <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
            {canDispatch
              ? 'Record a consequence when you hear about it. '
              : 'A dispatcher records a consequence when one is heard about. '}
            Two weeks after delivery, an order with nothing recorded is closed as &ldquo;nothing
            happened&rdquo;, even if somebody did complain.
          </p>
          <ul className="space-y-1.5">
            {late.map((order) => (
              <LateOrderRow
                key={order.order_id}
                order={order}
                kinds={canDispatch ? kinds : []}
                onDone={load}
              />
            ))}
          </ul>
        </section>
      )}

      {flags && flags.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            Worth a look
          </h3>
          <ul className="space-y-1.5">
            {flags.map((flag) => (
              <li
                key={flag.id}
                className="flex items-start justify-between gap-3 rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2"
              >
                <div>
                  <p className="text-[13px] text-[var(--text-primary)]">{flag.detail}</p>
                  <p className="text-[11px] text-[var(--text-muted)]">
                    {new Date(flag.detected_at).toLocaleDateString()}
                  </p>
                </div>
                {canDispatch && (
                  <button
                    onClick={async () => {
                      await api.resolveLinkageFlag(flag.id)
                      await load()
                    }}
                    className="flex-shrink-0 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                  >
                    Looked at it
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </Card>
  )
}

// The one consequence with a cost to record (app/record/consequences.py).
const CREDIT = 'credit_issued'

// Today in the browser's own calendar, as a date input writes it.
function localToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * One late delivery, and what happened after it.
 *
 * Picking a consequence asks when it happened and, for a credit, what it cost,
 * before anything is recorded. One tap sent only the kind, so a call taken on
 * Monday and recorded on Thursday was dated Thursday, and no credit had a cost -
 * in an append-only label set, where a wrong row is never corrected.
 */
function LateOrderRow({
  order,
  kinds,
  onDone,
}: {
  order: LateOrder
  kinds: ConsequenceOption[]
  onDone: () => void | Promise<void>
}) {
  const [saving, setSaving] = useState(false)
  const [choice, setChoice] = useState<ConsequenceOption | null>(null)
  const [when, setWhen] = useState(localToday())
  const [cost, setCost] = useState('')
  const [note, setNote] = useState('')
  const [failed, setFailed] = useState<string | null>(null)

  function choose(kind: ConsequenceOption) {
    setChoice(kind)
    setWhen(localToday())
    setCost('')
    setNote('')
    setFailed(null)
  }

  const dollars = Number(cost)
  const costProblem =
    cost.trim() !== '' && (!Number.isFinite(dollars) || dollars < 0)
      ? 'Enter the amount in dollars, like 45.00'
      : null

  async function record() {
    if (!choice || costProblem) return
    setSaving(true)
    setFailed(null)
    try {
      await api.recordConsequence(order.order_id, {
        kind: choice.code,
        // Today is left to the server's clock. An earlier day is sent as its
        // midday, so the date can't slip across midnight on the way.
        occurred_at: when === localToday() ? undefined : new Date(`${when}T12:00:00`).toISOString(),
        amount_cents:
          choice.code === CREDIT && cost.trim() !== '' ? Math.round(dollars * 100) : undefined,
        detail: note.trim() || undefined,
      })
      setChoice(null)
      await onDone()
    } catch (e) {
      setFailed((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const field =
    'rounded-md border border-[var(--border)] bg-[var(--surface)] px-1.5 py-0.5 text-[12px] text-[var(--text-primary)]'

  return (
    <li className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium text-[var(--text-primary)]">
          {order.external_ref}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">
          {order.minutes_late !== null ? `${order.minutes_late} min late` : 'late'}
          {order.delivered_at && ` · delivered ${new Date(order.delivered_at).toLocaleDateString()}`}
          {' · '}
          <span className="font-mono" title={order.order_id}>
            {truncateId(order.order_id)}
          </span>
        </span>
      </div>
      {choice ? (
        <div className="space-y-1.5 rounded-[var(--radius)] bg-[var(--surface-2)] p-2">
          <p className="text-[12px] font-medium text-[var(--text-primary)]">{choice.label}</p>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-0.5 text-[11px] text-[var(--text-muted)]">
              When
              <input
                type="date"
                value={when}
                max={localToday()}
                onChange={(e) => setWhen(e.target.value || localToday())}
                className={field}
              />
            </label>
            {choice.code === CREDIT && (
              <label className="flex flex-col gap-0.5 text-[11px] text-[var(--text-muted)]">
                What it cost us ($)
                <input
                  inputMode="decimal"
                  value={cost}
                  placeholder="45.00"
                  onChange={(e) => setCost(e.target.value)}
                  className={`${field} w-24 text-right`}
                />
              </label>
            )}
            <label className="flex min-w-[10rem] flex-1 flex-col gap-0.5 text-[11px] text-[var(--text-muted)]">
              Note (optional)
              <input
                value={note}
                maxLength={500}
                onChange={(e) => setNote(e.target.value)}
                className={field}
              />
            </label>
          </div>
          {(costProblem || failed) && (
            <p className="text-[11px] text-[var(--red)]">{costProblem ?? `Couldn't record: ${failed}`}</p>
          )}
          <div className="flex gap-1">
            <button
              disabled={saving || !!costProblem}
              onClick={record}
              className="rounded-md bg-[var(--accent)] px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40"
            >
              {saving ? 'Recording…' : 'Record'}
            </button>
            <button
              disabled={saving}
              onClick={() => setChoice(null)}
              className="text-[11px] text-[var(--text-muted)] underline"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-1">
          {kinds.map((kind) => (
            <button
              key={kind.code}
              disabled={saving}
              onClick={() => choose(kind)}
              className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40"
            >
              {kind.label}
            </button>
          ))}
        </div>
      )}
    </li>
  )
}
