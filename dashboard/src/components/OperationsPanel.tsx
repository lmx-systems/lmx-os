import { useState, type ReactNode } from 'react'
import { Card } from './ui/Card'
import { ConfirmModal } from './ui/ConfirmModal'
import { api, ApiError } from '../lib/api'
import type { LastCycleInfo, RunLogEntry } from '../lib/types'

interface OperationsPanelProps {
  hubId: string
  onLastCycle: (info: LastCycleInfo) => void
  onToast: (message: string) => void
}

type Job = 'optimizer' | 'learning_loop'

const JOB_COPY: Record<Job, { title: string; description: string; confirmLabel: string }> = {
  optimizer: {
    title: 'Run dispatch cycle now?',
    description:
      'Re-optimizes every held order against the current fleet immediately, instead of waiting for the next automatic event (order ingested, driver status change). Safe to run any time.',
    confirmLabel: 'Run cycle',
  },
  learning_loop: {
    title: 'Run learning-loop job now?',
    description:
      'Scans recent stop annotations for hold-window patterns and proposes rule changes for review in proposed_rules. Nothing is auto-applied.',
    confirmLabel: 'Run job',
  },
}

const MAX_LOG_ENTRIES = 8

export function OperationsPanel({ hubId, onLastCycle, onToast }: OperationsPanelProps) {
  const [openJob, setOpenJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState(false)
  const [log, setLog] = useState<RunLogEntry[]>([])

  async function confirm() {
    if (!openJob) return
    setBusy(true)
    const now = Date.now()
    try {
      if (openJob === 'optimizer') {
        const result = await api.runOptimizerCycle(hubId)
        onLastCycle({ at: now, result })
        const summary = `${result.assignments.length} assigned · ${Math.round(result.duration_seconds * 1000)}ms`
        const entry: RunLogEntry = { at: now, kind: 'optimizer', summary }
        setLog((l) => [entry, ...l].slice(0, MAX_LOG_ENTRIES))
        onToast(
          result.assignments.length > 0
            ? `Cycle complete — ${result.assignments.length} order(s) assigned`
            : 'Cycle complete — nothing to assign',
        )
      } else {
        const result = await api.runLearningLoopJob(hubId)
        const summary = `${result.proposals_created.length} rule(s) proposed`
        const entry: RunLogEntry = { at: now, kind: 'learning_loop', summary }
        setLog((l) => [entry, ...l].slice(0, MAX_LOG_ENTRIES))
        onToast(
          result.proposals_created.length > 0
            ? `Learning-loop job complete — ${result.proposals_created.length} new rule(s) proposed`
            : 'Learning-loop job complete — no new patterns found',
        )
      }
      setOpenJob(null)
    } catch (err) {
      onToast(`Failed: ${err instanceof ApiError ? err.message : String(err)}`)
    } finally {
      setBusy(false)
    }
  }

  const disabled = hubId.length === 0

  return (
    <Card title="Operations">
      <div className="mb-3.5 grid grid-cols-2 gap-3">
        <OpsButton
          icon={
            <path d="M13 2L3 14h7l-1 8 11-14h-7l1-8z" />
          }
          title="Run dispatch cycle"
          sub="Manual override — usually automatic"
          disabled={disabled}
          onClick={() => setOpenJob('optimizer')}
        />
        <OpsButton
          icon={<path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z" />}
          title="Run learning-loop job"
          sub="Detect hold-window patterns"
          disabled={disabled}
          onClick={() => setOpenJob('learning_loop')}
        />
      </div>

      <table className="w-full text-left text-[12px]">
        <thead>
          <tr className="text-[var(--text-muted)]">
            <th className="pb-1.5 pr-2 font-medium">Time</th>
            <th className="pb-1.5 pr-2 font-medium">Job</th>
            <th className="pb-1.5 font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {log.length === 0 && (
            <tr>
              <td colSpan={3} className="py-3 text-[var(--text-muted)]">
                Nothing run this session yet.
              </td>
            </tr>
          )}
          {log.map((entry) => (
            <tr key={entry.at} className="border-t border-[var(--border)]">
              <td className="py-1.5 pr-2 text-[var(--text-muted)]">{new Date(entry.at).toLocaleTimeString()}</td>
              <td className="py-1.5 pr-2">{entry.kind === 'optimizer' ? 'Dispatch cycle' : 'Learning-loop job'}</td>
              <td className="py-1.5">{entry.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {openJob && (
        <ConfirmModal
          open={true}
          busy={busy}
          title={JOB_COPY[openJob].title}
          description={JOB_COPY[openJob].description}
          confirmLabel={JOB_COPY[openJob].confirmLabel}
          onConfirm={confirm}
          onCancel={() => setOpenJob(null)}
        />
      )}
    </Card>
  )
}

function OpsButton({
  icon,
  title,
  sub,
  disabled,
  onClick,
}: {
  icon: ReactNode
  title: string
  sub: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2.5 rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--surface-2)] p-3 text-left disabled:cursor-not-allowed disabled:opacity-40"
    >
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-[var(--accent-dim)] text-[var(--accent)]">
        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          {icon}
        </svg>
      </div>
      <div>
        <div className="text-[13px] font-medium text-[var(--text-primary)]">{title}</div>
        <div className="text-[11.5px] text-[var(--text-muted)]">{sub}</div>
      </div>
    </button>
  )
}
