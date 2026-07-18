import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { NightlyJobResult, OptimizationResult } from '../lib/types'

interface TriggerPanelProps {
  hubId: string
}

type ActionState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'error'; message: string }
  | { status: 'optimizer-done'; result: OptimizationResult }
  | { status: 'learning-loop-done'; result: NightlyJobResult }

export function TriggerPanel({ hubId }: TriggerPanelProps) {
  const [state, setState] = useState<ActionState>({ status: 'idle' })
  const disabled = hubId.length === 0 || state.status === 'running'

  async function runOptimizer() {
    setState({ status: 'running' })
    try {
      const result = await api.runOptimizerCycle(hubId)
      setState({ status: 'optimizer-done', result })
    } catch (err) {
      setState({ status: 'error', message: err instanceof ApiError ? err.message : String(err) })
    }
  }

  async function runLearningLoop() {
    setState({ status: 'running' })
    try {
      const result = await api.runLearningLoopJob(hubId)
      setState({ status: 'learning-loop-done', result })
    } catch (err) {
      setState({ status: 'error', message: err instanceof ApiError ? err.message : String(err) })
    }
  }

  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      <h2 className="mb-3 text-sm font-semibold text-slate-200">Manual Triggers</h2>
      <p className="mb-3 text-xs text-slate-500">
        In production the Dispatch Optimizer runs on real events and the Learning Loop runs
        nightly on a schedule — these buttons exist for testing and ops (e.g. forcing a cycle
        after an out-of-band fix), same as the API endpoints they call.
      </p>

      <div className="flex gap-2">
        <button
          onClick={runOptimizer}
          disabled={disabled}
          className="rounded bg-purple-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run Optimizer Cycle
        </button>
        <button
          onClick={runLearningLoop}
          disabled={disabled}
          className="rounded bg-slate-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Run Learning Loop Job
        </button>
      </div>

      {state.status === 'error' && (
        <p className="mt-3 text-sm text-red-400">Failed: {state.message}</p>
      )}

      {state.status === 'optimizer-done' && (
        <div className="mt-3 rounded border border-slate-800 bg-slate-950/50 p-3 text-sm text-slate-300">
          <p>
            Engine: <span className="font-mono text-xs">{state.result.engine}</span> · Duration:{' '}
            {state.result.duration_seconds}s
            {state.result.over_budget && (
              <span className="ml-2 text-amber-400">(over the cycle budget)</span>
            )}
          </p>
          <p className="mt-1">
            {state.result.assignments.length} route(s) assigned,{' '}
            {state.result.unassigned_stop_ids.length} stop(s) left unassigned.
          </p>
        </div>
      )}

      {state.status === 'learning-loop-done' && (
        <div className="mt-3 rounded border border-slate-800 bg-slate-950/50 p-3 text-sm text-slate-300">
          {state.result.proposals_created.length === 0 ? (
            <p>No new patterns detected — nothing proposed this run.</p>
          ) : (
            <p>
              {state.result.proposals_created.length} new proposed rule(s) created. Review them in
              the `proposed_rules` table before promoting.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
