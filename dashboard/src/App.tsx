import { useEffect, useState } from 'react'
import { TopBar } from './components/TopBar'
import { KpiStrip } from './components/KpiStrip'
import { OrderPipeline } from './components/OrderPipeline'
import { HoldQueueTable } from './components/HoldQueueTable'
import { FleetRoster } from './components/FleetRoster'
import { OperationsPanel } from './components/OperationsPanel'
import { Toast } from './components/ui/Toast'
import { usePolling } from './hooks/usePolling'
import { useToast } from './hooks/useToast'
import { api } from './lib/api'

const HUB_ID_STORAGE_KEY = 'lmx-os-dashboard.hub-id'
const POLL_INTERVAL_MS = 5000

function App() {
  const [hubId, setHubId] = useState(() => localStorage.getItem(HUB_ID_STORAGE_KEY) ?? '')
  const { message, showToast } = useToast()
  const enabled = hubId.length > 0

  useEffect(() => {
    localStorage.setItem(HUB_ID_STORAGE_KEY, hubId)
  }, [hubId])

  // Lifted here (rather than each section polling independently, as the
  // pre-redesign dashboard did) so the KPI strip's numbers always match
  // the tables below it exactly - two independent polls of the same
  // endpoint can land a tick apart and briefly disagree.
  const fleet = usePolling(() => api.fleetOverview(hubId), POLL_INTERVAL_MS, [hubId], enabled)
  const held = usePolling(() => api.heldOrders(hubId), POLL_INTERVAL_MS, [hubId], enabled)
  const summary = usePolling(() => api.orderSummary(hubId), POLL_INTERVAL_MS, [hubId], enabled)
  // Server-side snapshot (see app/optimizer/last_cycle_store.py) - reflects
  // automatic event-triggered cycles too, not just ones this tab fired.
  const lastCycle = usePolling(() => api.lastCycle(hubId), POLL_INTERVAL_MS, [hubId], enabled)

  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  useEffect(() => {
    if (fleet.data || held.data || summary.data) {
      setLastUpdatedAt(Date.now())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fleet.data, held.data, summary.data, lastCycle.data])

  return (
    <div className="min-h-screen">
      <div className="mx-auto max-w-[1320px] px-7 py-5 pb-16">
        <TopBar hubId={hubId} onChangeHubId={setHubId} lastUpdatedAt={enabled ? lastUpdatedAt : null} />

        {!enabled ? (
          <p className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-sm text-[var(--text-muted)]">
            Paste a hub UUID above to load fleet state, the hold queue, and order status for that
            hub.
          </p>
        ) : (
          <>
            <KpiStrip
              fleet={fleet.data}
              fleetError={fleet.error}
              held={held.data}
              heldError={held.error}
              summary={summary.data}
              summaryError={summary.error}
              lastCycle={lastCycle.data}
            />

            <div className="grid gap-4 lg:grid-cols-[1.55fr_1fr]">
              <div className="flex flex-col gap-4">
                <OrderPipeline summary={summary.data} error={summary.error} loading={summary.loading} />
                <HoldQueueTable key={hubId} data={held.data} error={held.error} loading={held.loading} />
              </div>
              <div className="flex flex-col gap-4">
                <FleetRoster data={fleet.data} error={fleet.error} loading={fleet.loading} />
                <OperationsPanel
                  key={hubId}
                  hubId={hubId}
                  onAfterRun={() => {
                    fleet.refetchNow()
                    held.refetchNow()
                    summary.refetchNow()
                    lastCycle.refetchNow()
                  }}
                  onToast={showToast}
                />
              </div>
            </div>
          </>
        )}

        <p className="mt-6 border-t border-[var(--border)] pt-3.5 text-xs text-[var(--text-muted)]">
          Internal use only — Phase 1 core backend. See docs/ARCHITECTURE.md for known gaps.
        </p>
      </div>

      <Toast message={message} />
    </div>
  )
}

export default App
