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
 * Fetched on demand rather than polled on the dashboard tick: neither list
 * changes more than once a night, and polling them would be a request every few
 * seconds for data that moves daily.
 */
export function RecordLayerPanel({ hubId }: { hubId: string }) {
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
          Nothing waiting. Late deliveries appear here once their two-week window closes.
        </p>
      )}

      {late && late.length > 0 && (
        <section className="mb-4">
          <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            What happened after these ran late?
          </h3>
          <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
            Recording &ldquo;nothing happened&rdquo; matters as much as recording a complaint — if
            only the complaints are written down, everything we learn from this says late deliveries
            always cause trouble.
          </p>
          <ul className="space-y-1.5">
            {late.map((order) => (
              <LateOrderRow key={order.order_id} order={order} kinds={kinds} onDone={load} />
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
                <button
                  onClick={async () => {
                    await api.resolveLinkageFlag(flag.id)
                    await load()
                  }}
                  className="flex-shrink-0 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                >
                  Looked at it
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Card>
  )
}

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

  async function record(kind: string) {
    setSaving(true)
    try {
      await api.recordConsequence(order.order_id, { kind })
      await onDone()
    } finally {
      setSaving(false)
    }
  }

  return (
    <li className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2">
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium text-[var(--text-primary)]">
          {order.external_ref}
        </span>
        <span className="text-[11px] text-[var(--text-muted)]">
          {order.minutes_late !== null ? `${order.minutes_late} min late` : 'late'}
          {' · '}
          <span className="font-mono" title={order.order_id}>
            {truncateId(order.order_id)}
          </span>
        </span>
      </div>
      <div className="flex flex-wrap gap-1">
        {kinds.map((kind) => (
          <button
            key={kind.code}
            disabled={saving}
            onClick={() => record(kind.code)}
            className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[11px] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-40"
          >
            {kind.label}
          </button>
        ))}
      </div>
    </li>
  )
}
