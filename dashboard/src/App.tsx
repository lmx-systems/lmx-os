import { useEffect, useState } from 'react'
import { HubSelector } from './components/HubSelector'
import { FleetOverview } from './components/FleetOverview'
import { HoldQueue } from './components/HoldQueue'
import { OrderSummary } from './components/OrderSummary'
import { TriggerPanel } from './components/TriggerPanel'

const HUB_ID_STORAGE_KEY = 'lmx-os-dashboard.hub-id'

function App() {
  const [hubId, setHubId] = useState(() => localStorage.getItem(HUB_ID_STORAGE_KEY) ?? '')

  useEffect(() => {
    localStorage.setItem(HUB_ID_STORAGE_KEY, hubId)
  }, [hubId])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">LMX OS — Orchestrator Dashboard</h1>
            <p className="text-xs text-slate-500">Phase 1 core backend, internal use only</p>
          </div>
          <HubSelector hubId={hubId} onChange={setHubId} />
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-4 px-6 py-6">
        {hubId.length === 0 ? (
          <p className="rounded-lg border border-slate-800 bg-slate-900/60 p-6 text-center text-sm text-slate-400">
            Paste a hub UUID above to load fleet state, the hold queue, and order status for that
            hub.
          </p>
        ) : (
          <>
            <OrderSummary hubId={hubId} />
            <div className="grid gap-4 lg:grid-cols-2">
              <FleetOverview hubId={hubId} />
              <HoldQueue hubId={hubId} />
            </div>
            <TriggerPanel hubId={hubId} />
          </>
        )}
      </main>
    </div>
  )
}

export default App
