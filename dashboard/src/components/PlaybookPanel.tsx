import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { PlaybookMode, PlaybookReport } from '../lib/types'

const RULE_LABEL: Record<string, string> = {
  failed_delivery: 'Failed deliveries',
  unplaced: 'Orders nobody has',
}

const MODE_LABEL: Record<PlaybookMode, string> = {
  recommend: 'Recommending - a person still decides',
  veto: 'Acting unless a person stops it first',
  act: 'Acting on its own',
}

const ACTION_LABEL: Record<string, string> = {
  redeliver: 'redeliver',
  return_to_shop: 'return to shop',
  cancel: 'cancel',
  escalate: 'hand to a person',
  offer: 'offer to a driver',
  offer_bonus: 'offer with a bonus',
  hold: 'put back in the queue',
  release: 'release it',
}

const CATEGORY_LABEL: Record<string, string> = {
  day: 'running the day',
  record: 'fixing the record',
  policy: 'setting policy',
  business: 'running the business',
  unclassified: 'unclassified',
}

const DAYS = 7

function humanise(action: string): string {
  return action.replace(/_endpoint$/, '').replace(/_/g, ' ')
}

/**
 * The distance to nobody running the day (docs/ROADMAP_1.5.md DEC-7, DEC-8;
 * decision D-SEAT).
 *
 * **Person touches per 100 orders** is every write a person made from this
 * console that ran the day or fixed the record, against the live orders that
 * came in. The goal is zero. Below it, what people did most - the next thing a
 * rule should take over - and each rule's record against the people it is
 * replacing: how often its calls matched theirs, and whether that is enough to
 * give it more authority.
 *
 * A read for every ops role. Moving a rule up is a setting, not a button here:
 * the panel shows the evidence and a person decides.
 */
export function PlaybookPanel({ hubId }: { hubId: string }) {
  const [report, setReport] = useState<PlaybookReport | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    let live = true
    api
      .playbook(hubId, DAYS)
      .then((r) => live && setReport(r))
      .catch((e) => live && setError(e as Error))
    return () => {
      live = false
    }
  }, [hubId])

  const meta = report ? `last ${DAYS} days` : undefined

  return (
    <Card title="Playbook" meta={meta}>
      {error && <p className="text-sm text-[var(--red)]">Couldn't load the playbook: {error.message}</p>}
      {!error && !report && <p className="text-sm text-[var(--text-muted)]">Loading…</p>}
      {report && (
        <div className="flex flex-col gap-4">
          <div>
            <p className="text-sm text-[var(--text-secondary)]">
              {report.touches_per_100_orders === null ? (
                <span className="text-[var(--text-primary)]">No live orders came in, so there is no rate yet.</span>
              ) : (
                <>
                  <span className="text-[44px] font-extrabold leading-none tracking-[-0.05em] tabular-nums text-[var(--text-primary)]">
                    {report.touches_per_100_orders}
                  </span>{' '}
                  person touches per 100 orders
                </>
              )}
            </p>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {report.touches} {report.touches === 1 ? 'touch' : 'touches'} running the day or fixing the
              record, {report.orders} live {report.orders === 1 ? 'order' : 'orders'}. The goal is zero.
              {report.unscoped_touches > 0 &&
                ` Plus ${report.unscoped_touches} on sites and merges that belong to no one hub.`}
              {(report.by_category.policy ?? 0) + (report.by_category.business ?? 0) > 0 &&
                ` Also ${report.by_category.policy ?? 0} setting policy and ${report.by_category.business ?? 0} running the business, counted but not in the rate.`}
            </p>
          </div>

          {report.by_action.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-medium text-[var(--text-muted)]">What people did</h3>
              <ul className="flex flex-col gap-0.5 text-sm">
                {report.by_action.slice(0, 8).map((t) => (
                  <li key={t.action} className="flex justify-between gap-3">
                    <span className="text-[var(--text-primary)]">
                      {humanise(t.action)}{' '}
                      <span className="text-xs text-[var(--text-muted)]">
                        {CATEGORY_LABEL[t.category] ?? t.category}
                      </span>
                    </span>
                    <span className="tabular-nums text-[var(--text-secondary)]">{t.count}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.rules.map((rule) => {
            const decided = rule.yes + rule.no
            const calls = Object.values(rule.outcomes).reduce((a, b) => a + b, 0)
            return (
              <div key={rule.rule} className="rounded-[20px] bg-[var(--surface-2)] px-4 py-3.5">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="text-[15px] font-bold text-[var(--text-primary)]">
                    {RULE_LABEL[rule.rule] ?? humanise(rule.rule)}
                  </span>
                  <span className="text-xs text-[var(--text-muted)]">{MODE_LABEL[rule.mode]}</span>
                </div>
                <Steps mode={rule.mode} acts={rule.acts} />
                {rule.acts && rule.next_mode !== null && (
                  <div
                    className="mt-2 h-2 overflow-hidden rounded-full bg-[var(--surface-3)]"
                    role="img"
                    aria-label={`${Math.min(decided, rule.min_decisions)} of ${rule.min_decisions} decided calls needed to move up`}
                  >
                    <div
                      className="h-full rounded-full bg-[var(--accent)]"
                      style={{ width: `${Math.min(100, Math.round((decided / Math.max(1, rule.min_decisions)) * 100))}%` }}
                    />
                  </div>
                )}
                <p className="mt-1 text-xs text-[var(--text-secondary)]">
                  {calls === 0
                    ? `No calls in the last ${DAYS} days.`
                    : `${calls} ${calls === 1 ? 'call' : 'calls'}: ${rule.outcomes.acted ?? 0} acted on, ${
                        rule.outcomes.agreed ?? 0
                      } a person agreed with, ${rule.outcomes.overruled ?? 0} overruled, ${
                        rule.outcomes.escalated ?? 0
                      } handed to a person, ${rule.outcomes.open ?? 0} waiting, ${
                        rule.outcomes.superseded ?? 0
                      } overtaken by events.`}
                </p>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  {!rule.acts
                    ? decided === 0
                      ? 'Recommends only. Acting is built for it once its calls match what people do.'
                      : `Recommends only: ${rule.yes} of ${decided} decided calls matched what a person did (${Math.round(
                          (rule.agreement ?? 0) * 100,
                        )}%). Acting is built for it once that is high enough.`
                    : rule.next_mode === null
                    ? 'At full authority.'
                    : rule.ready_to_move_up
                      ? `Ready to move up: ${rule.yes} of ${decided} decided calls agreed with people. Set it to ${rule.next_mode}.`
                      : decided === 0
                        ? `Moves up after ${rule.min_decisions} calls a person also decided, ${Math.round(
                            rule.min_agreement * 100,
                          )}% of them agreeing. None yet.`
                        : `${rule.yes} of ${decided} decided calls agreed (${Math.round(
                            (rule.agreement ?? 0) * 100,
                          )}%). Moves up at ${rule.min_decisions} calls and ${Math.round(rule.min_agreement * 100)}%.`}
                </p>
              </div>
            )
          })}

          {report.overruled.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-medium text-[var(--text-muted)]">
                Where a person chose differently
              </h3>
              <ul className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
                {report.overruled.map((o) => (
                  <li key={`${o.order_id}-${o.closed_at}`}>
                    <span className="font-medium text-[var(--text-primary)]">{o.external_ref ?? o.order_id}</span>
                    {': the rule said '}
                    {ACTION_LABEL[o.rule_action] ?? o.rule_action}, a person chose{' '}
                    {o.human_action ? (ACTION_LABEL[o.human_action] ?? o.human_action) : 'otherwise'}. {o.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

const STEPS: { mode: PlaybookMode; label: string }[] = [
  { mode: 'recommend', label: 'Suggests' },
  { mode: 'veto', label: 'Acts unless stopped' },
  { mode: 'act', label: 'Acts alone' },
]

/**
 * The three steps a rule climbs (docs/DESIGN_SYSTEM.md, "Rules"): the one it is
 * on in ink, the ones behind it in lime, the ones ahead outlined. A rule that
 * can only recommend shows the later steps as not built, not as merely ahead.
 */
function Steps({ mode, acts }: { mode: PlaybookMode; acts: boolean }) {
  const at = STEPS.findIndex((s) => s.mode === mode)
  return (
    <ol className="mt-2.5 grid grid-cols-3 gap-1" aria-label="Steps to acting alone">
      {STEPS.map((step, i) => {
        const style =
          i === at
            ? 'bg-[var(--accent)] text-white'
            : i < at
              ? 'bg-[var(--accent-dim)] text-[var(--lime-ink)]'
              : !acts
                ? 'bg-transparent text-[var(--text-muted)] shadow-[inset_0_0_0_1px_var(--border)]'
                : 'bg-[var(--surface)] text-[var(--text-secondary)] shadow-[inset_0_0_0_1px_var(--border-strong)]'
        return (
          <li
            key={step.mode}
            aria-current={i === at ? 'step' : undefined}
            className={`truncate rounded-[10px] px-1 py-1.5 text-center text-[12px] font-bold ${style}`}
          >
            {step.label}
            {i > at && !acts && <span className="sr-only"> (not built yet)</span>}
          </li>
        )
      })}
    </ol>
  )
}
