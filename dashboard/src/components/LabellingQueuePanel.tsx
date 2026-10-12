import { useEffect, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type {
  LabellingAgreement,
  LabellingQueuePage,
  LabellingRecord,
  MachineVerdict,
  MachineVerdictInput,
} from '../lib/types'

/**
 * Could a drone, a sidewalk robot or a car have made this delivery?
 * (docs/ROADMAP_1.5.md DE-9.)
 *
 * One record at a time, all three machines at once, against the machine limits
 * named at the top. **Blind:** nothing here shows another scorer's verdict -
 * the second person to score a record is the blind second, and agreement
 * between the two is the ceiling on any accuracy claim, so a queue that showed
 * the first answer would measure copying.
 *
 * A "no" names what fails; a "can't tell" names what is missing. The missing
 * facts are the next visit's collection list, so they are worth the tap.
 *
 * Renders nothing when there is nothing to score. A viewer sees the count and
 * the agreement, without the buttons.
 */

const MACHINE_LABEL: Record<string, string> = {
  drone: 'Drone',
  sidewalk_robot: 'Sidewalk robot',
  car: 'Car',
}

const VERDICTS: { code: MachineVerdict; label: string }[] = [
  { code: 'yes', label: 'Yes' },
  { code: 'no', label: 'No' },
  { code: 'cant_tell', label: "Can't tell" },
]

const words = (code: string) => code.replace(/_/g, ' ')

function known(value: unknown): string {
  if (value === null || value === undefined) return 'unknown'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none'
  return String(value)
}

function Facts({ record }: { record: LabellingRecord }) {
  const rows: [string, unknown][] = [
    ['Site', record.address ?? 'no address'],
    ['Kind / setting', `${known(record.kind_of_site)} / ${known(record.setting)}`],
    ['Item', `${known(record.item_description)} · ${known(record.item_weight_kg)} kg · ${known(record.item_size_class)}`],
    ['Hazardous or liquid', record.item_hazmat_or_liquid],
    ['Handoff', record.handoff_type],
    ['Stop', `${known(record.stop_type)}, legal: ${known(record.stop_legal)}, ${known(record.walk_distance_m)} m to the door`],
    ['Steps / sidewalk', `${known(record.steps_or_ramps)} / ${known(record.continuous_sidewalk)}`],
    ['In the way', record.obstructions],
    ['Open ground / overhead', `${known(record.drone_open_ground)} / ${known(record.overhead)}`],
    ['Who receives', record.who_receives],
  ]
  return (
    <dl className="mb-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-[var(--text-muted)]">{label}</dt>
          <dd className="text-[var(--text-primary)]">{known(value)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function LabellingQueuePanel({ canDispatch }: { canDispatch: boolean }) {
  const [page, setPage] = useState<LabellingQueuePage | null>(null)
  const [agreement, setAgreement] = useState<LabellingAgreement | null>(null)
  const [answers, setAnswers] = useState<Record<string, MachineVerdictInput>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const [p, a] = await Promise.all([api.labellingQueue(10), api.labellingAgreement()])
      setPage(p)
      setAgreement(a)
      setAnswers({})
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  if (page !== null && page.records.length === 0) return null
  const record = page?.records[0]

  function choose(machine: string, verdict: MachineVerdict) {
    setAnswers((current) => ({ ...current, [machine]: { verdict, reason_codes: [] } }))
  }

  function toggleReason(machine: string, code: string) {
    setAnswers((current) => {
      const answer = current[machine]
      if (!answer) return current
      const has = answer.reason_codes.includes(code)
      return {
        ...current,
        [machine]: {
          ...answer,
          reason_codes: has ? answer.reason_codes.filter((r) => r !== code) : [...answer.reason_codes, code],
        },
      }
    })
  }

  const complete =
    page !== null &&
    page.machines.every((m) => {
      const a = answers[m]
      return a && (a.verdict === 'yes' || a.reason_codes.length > 0)
    })

  async function save() {
    if (!page || !record || !page.machine_limits_version) return
    setBusy(true)
    setError(null)
    try {
      await api.labelRecord(record.stop_id, page.machine_limits_version, answers)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const weak = agreement?.cells.filter((c) => c.under_gate) ?? []

  return (
    <Card
      title="Could a machine have made it?"
      meta={page ? `limits ${page.machine_limits_version ?? 'none'} · ${page.records.length}+ to score` : 'loading…'}
    >
      {error && <p className="mb-2 text-sm text-[var(--red)]">{error}</p>}

      {agreement && agreement.cells.length > 0 && (
        <p className="mb-2 text-[12px] text-[var(--text-secondary)]">
          {weak.length === 0
            ? `Scorers agree at or above ${Math.round(agreement.gate * 100)}% wherever both have scored.`
            : `Under ${Math.round(agreement.gate * 100)}% agreement: ${weak
                .map((c) => `${MACHINE_LABEL[c.machine] ?? c.machine}, ${c.group}`)
                .join('; ')} - fix the question before collecting more.`}
        </p>
      )}

      {record && (
        <div className="rounded-[var(--radius)] border border-[var(--border)] px-2.5 py-2">
          <Facts record={record} />

          {canDispatch &&
            page!.machines.map((machine) => {
              const answer = answers[machine]
              const reasons =
                answer?.verdict === 'no' ? page!.no_reasons : answer?.verdict === 'cant_tell' ? page!.missing_data : []
              return (
                <div key={machine} className="mb-2">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="w-28 text-[12px] text-[var(--text-primary)]">{MACHINE_LABEL[machine] ?? machine}</span>
                    {VERDICTS.map((v) => (
                      <button
                        key={v.code}
                        onClick={() => choose(machine, v.code)}
                        className={`rounded-md border px-1.5 py-0.5 text-[11px] ${
                          answer?.verdict === v.code
                            ? 'border-[var(--accent)] text-[var(--text-primary)]'
                            : 'border-[var(--border)] text-[var(--text-secondary)]'
                        }`}
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>
                  {reasons.length > 0 && (
                    <div className="ml-28 flex flex-wrap gap-1 pl-2">
                      {reasons.map((code) => (
                        <button
                          key={code}
                          onClick={() => toggleReason(machine, code)}
                          className={`rounded-md border px-1.5 py-0.5 text-[11px] ${
                            answer?.reason_codes.includes(code)
                              ? 'border-[var(--accent)] text-[var(--text-primary)]'
                              : 'border-[var(--border)] text-[var(--text-muted)]'
                          }`}
                        >
                          {words(code)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}

          {canDispatch && (
            <button
              disabled={!complete || busy || !page?.machine_limits_version}
              onClick={save}
              className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40"
            >
              Save and next
            </button>
          )}
        </div>
      )}
    </Card>
  )
}
