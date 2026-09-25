import { useEffect, useState } from 'react'
import { TopBar } from './components/TopBar'
import { KpiStrip } from './components/KpiStrip'
import { OrderPipeline } from './components/OrderPipeline'
import { HoldQueueTable } from './components/HoldQueueTable'
import { ExceptionsPanel } from './components/ExceptionsPanel'
import { Tabs } from './components/ui/Tabs'
import { DockLabellingPanel } from './components/DockLabellingPanel'
import { DockLogReviewPanel } from './components/DockLogReviewPanel'
import { HubSettingsPanel } from './components/HubSettingsPanel'
import { MergeReviewPanel } from './components/MergeReviewPanel'
import { OrderLookupPanel } from './components/OrderLookupPanel'
import { RecordHealthPanel } from './components/RecordHealthPanel'
import { RecordLayerPanel } from './components/RecordLayerPanel'
import { FleetMap } from './components/FleetMap'
import { FleetRoster } from './components/FleetRoster'
import { MeasurementPanel } from './components/MeasurementPanel'
import { OperationsPanel } from './components/OperationsPanel'
import { OnboardClientForm } from './components/OnboardClientForm'
import { OnboardDriverForm } from './components/OnboardDriverForm'
import { UrgencyRulesPanel } from './components/UrgencyRulesPanel'
import { DriverDocumentsPanel } from './components/DriverDocumentsPanel'
import { PendingSignupsPanel } from './components/PendingSignupsPanel'
import { ProposedRulesPanel } from './components/ProposedRulesPanel'
import { CodDisputesPanel } from './components/CodDisputesPanel'
import { DriverDevicesPanel } from './components/DriverDevicesPanel'
import { GigPathPanel } from './components/GigPathPanel'
import { ReturnsPanel } from './components/ReturnsPanel'
import { ClientRatesPanel } from './components/ClientRatesPanel'
import { ClientSlaTermsPanel } from './components/ClientSlaTermsPanel'
import { ClientInvoicesPanel } from './components/ClientInvoicesPanel'
import { LoginPage } from './components/LoginPage'
import { Toast } from './components/ui/Toast'
import { usePolling } from './hooks/usePolling'
import { useToast } from './hooks/useToast'
import { api } from './lib/api'
import { clearToken, getToken } from './lib/auth'
import type { OpsProfileView } from './lib/types'

const HUB_ID_STORAGE_KEY = 'lmx-os-dashboard.hub-id'
const POLL_INTERVAL_MS = 5000

function App() {
  const [hubId, setHubId] = useState(() => localStorage.getItem(HUB_ID_STORAGE_KEY) ?? '')
  const { message, showToast } = useToast()

  // Real per-account ops auth (docs/ROADMAP.md S1), replacing the old
  // shared X-API-Key stopgap - mirrors client-portal/src/App.tsx's
  // loggedIn/profile gating exactly.
  const [loggedIn, setLoggedIn] = useState(() => getToken() !== null)
  const [opsProfile, setOpsProfile] = useState<OpsProfileView | null>(null)
  // Which horizon the left column is showing (CON-1). Not persisted: a
  // dispatcher opening the board is starting a shift, and the thing they came
  // for is the live queue - restoring "To record" from last night would put the
  // wrong half in front of them at the worst moment.
  const [tab, setTab] = useState('now')
  const [profileError, setProfileError] = useState<string | null>(null)

  useEffect(() => {
    if (!loggedIn) return
    let cancelled = false
    setProfileError(null)
    api
      .myProfile()
      .then((profile) => {
        if (!cancelled) setOpsProfile(profile)
      })
      .catch(() => {
        if (cancelled) return
        // Most likely an expired/invalid token - api.ts already cleared
        // it on a 401, so drop back to the login screen.
        setLoggedIn(getToken() !== null)
        setProfileError('Could not load your account. Please sign in again.')
      })
    return () => {
      cancelled = true
    }
  }, [loggedIn])

  function handleLogout() {
    clearToken()
    setLoggedIn(false)
    setOpsProfile(null)
  }

  useEffect(() => {
    localStorage.setItem(HUB_ID_STORAGE_KEY, hubId)
  }, [hubId])

  // Paused entirely while logged out (or the session's own profile hasn't
  // loaded yet) - hooks still fire unconditionally at the top of the
  // component regardless of which branch below actually renders, so
  // without this the login screen would sit behind a background poll
  // loop making authenticated calls with no valid session.
  const enabled = loggedIn && opsProfile !== null && hubId.length > 0

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
  // CON-4. Polled on the same tick as everything else so the exception count
  // and the tables below it cannot disagree - two independent polls of related
  // endpoints land a tick apart and briefly contradict each other, which is
  // worse here than anywhere: the whole panel is a claim that nothing else on
  // the board needs attention.
  // Counts only, so the badge is honest without loading the panels behind it.
  // Polled on the same tick as everything else: a late order becoming due
  // during a shift should show up without a reload.
  const attention = usePolling(
    () => api.attentionCounts(hubId),
    POLL_INTERVAL_MS,
    [hubId],
    enabled,
  )
  const exceptions = usePolling(
    () => api.operationsExceptions(hubId),
    POLL_INTERVAL_MS,
    [hubId],
    enabled,
  )

  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)
  useEffect(() => {
    if (fleet.data || held.data || summary.data) {
      setLastUpdatedAt(Date.now())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fleet.data, held.data, summary.data, lastCycle.data])

  if (!loggedIn) {
    return <LoginPage onLoggedIn={() => setLoggedIn(true)} />
  }

  if (!opsProfile) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--text-muted)]">
        {profileError ?? 'Loading your account…'}
      </div>
    )
  }

  return (
    <div className="min-h-screen">
      <div className="mx-auto max-w-[1320px] px-7 py-5 pb-16">
        <TopBar
          hubId={hubId}
          onChangeHubId={setHubId}
          lastUpdatedAt={hubId.length > 0 ? lastUpdatedAt : null}
          opsProfile={opsProfile}
          onLogout={handleLogout}
        />

        {hubId.length === 0 ? (
          <p className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center text-sm text-[var(--text-muted)]">
            Select a hub above to load fleet state, the hold queue, and order status for that
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
              {/* Two horizons, not one scroll (CON-1: "a working dispatcher can
                  run a day on it"). Everything under "To record" is real work
                  and none of it is urgent; interleaved with the hold queue it
                  put a fortnight-old labelling question between a dispatcher
                  and the thing they came to the screen for.

                  The badge is what keeps the split honest. A tab with no count
                  is a tab nobody opens, which would trade a cluttered board for
                  work that silently stops getting done. */}
              <div className="flex flex-col gap-4">
                <Tabs
                  tabs={[
                    { id: 'now', label: 'Dispatching' },
                    {
                      id: 'record',
                      label: 'To record',
                      badge: attention.data
                        ? attention.data.late_orders +
                          attention.data.merge_proposals +
                          attention.data.unlabelled_docks
                        : undefined,
                    },
                  ]}
                  active={tab}
                  onChange={setTab}
                />

                {tab === 'now' && (
                  <>
                    {/* First, because it is what somebody reaches for when the
                        phone rings - and that is not a scheduled moment. Before
                        this a dispatcher could not look up an order at all once
                        it left the hold queue (CON-1). */}
                    <OrderLookupPanel key={`lookup-${hubId}`} hubId={hubId} />
                    <OrderPipeline summary={summary.data} error={summary.error} loading={summary.loading} />
                    {/* Above the hold queue on purpose: the hold queue is work
                        going to plan and this is work that is not, so a
                        dispatcher scanning top-down should meet the exceptions
                        first. Deliberately outside the admin block - it is a
                        read, and a dispatcher on a viewer account who cannot
                        see their own exceptions cannot run a day (CON-1). */}
                    <ExceptionsPanel
                      data={exceptions.data}
                      error={exceptions.error}
                      loading={exceptions.loading}
                    />
                    <HoldQueueTable key={hubId} data={held.data} error={held.error} loading={held.loading} />
                  </>
                )}

                {/* Mounted only when the tab is open, which is what makes the
                    loading lazy: each of these fetches on mount, and before the
                    split all four ran on every page view whether anybody looked
                    or not. */}
                {tab === 'record' && (
                  <>
                    <RecordLayerPanel key={`record-${hubId}`} hubId={hubId} />
                    {/* Not hub-scoped: the same physical dock can be reached
                        from two hubs and that pair is the most valuable merge
                        to catch (IDN-2). */}
                    <MergeReviewPanel isAdmin={opsProfile.role === 'admin'} />
                    {/* Also not hub-scoped: a dock is a physical place and its
                        class does not change with which hub serves it (IDN-3). */}
                    <DockLabellingPanel />
                    {/* Beside dock labelling because it is the same act on the
                        same rows - deciding what a dock is. This queue is why
                        dock_log_submissions exists as a separate table: a
                        stranger's answers never reach receiver_profiles, the
                        layer M5 trains on, without a person here matching them
                        to a dock (docs/ROADMAP.md DRV-7). */}
                    <DockLogReviewPanel onToast={showToast} />
                    {/* Last, because it is the read-back rather than the work:
                        is the record being written at all (REC-1..REC-4). */}
                    <RecordHealthPanel key={`health-${hubId}`} hubId={hubId} />
                    {/* Recording work rather than dispatching work: a repeat
                        disputer is a conversation to have this month, not this
                        minute. Deliberately NOT in the tab badge — with no SMS
                        provider configured every dispute is un-escalated by
                        definition, so the count would never fall and a badge that
                        never falls is the "tab nobody opens" failure inverted. */}
                    <CodDisputesPanel key={`cod-${hubId}`} hubId={hubId} />
                    {/* Recording work too, and the half of W1 that shipped
                        without a front end: a driver could never collect a core
                        and nobody could close one out. Hides itself when
                        nothing is outstanding. */}
                    <ReturnsPanel
                      key={`returns-${hubId}`}
                      hubId={hubId}
                      onToast={showToast}
                    />
                  </>
                )}
              </div>
              <div className="flex flex-col gap-4">
                {/* Map above the roster: "where is my fleet" is the glance a
                    dispatcher takes, and the roster is the detail they drop to.
                    Both read the same polled fleet data, so they cannot
                    disagree with each other. */}
                <FleetMap key={`map-${hubId}`} data={fleet.data} error={fleet.error} loading={fleet.loading} />
                <FleetRoster data={fleet.data} error={fleet.error} loading={fleet.loading} />
                {opsProfile.role === 'admin' && (
                  <>
                    {/* Mutating actions (run-cycle, run-nightly-job, onboard
                        a client) are admin-only on the backend
                        (app/ops_auth/dependencies.py's require_admin) - a
                        viewer never even sees the controls for actions
                        they'd get a 403 from. */}
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
                    {/* Above the manual onboarding form on purpose: an applicant
                        who is already waiting should be approved, not
                        re-created by hand as a second client record. */}
                    <PendingSignupsPanel key={`signups-${hubId}`} hubId={hubId} onToast={showToast} />
                    {/* No hub key: compliance review is whole-company, since an
                        unreviewed license keeps a driver off the road everywhere. */}
                    <DriverDocumentsPanel onToast={showToast} />
                    <OnboardClientForm hubId={hubId} onToast={showToast} />
                    {/* Beside client onboarding because it is what happens next:
                        rates were set once inside signup approval and then could
                        not be read back at all, so "what are we charging them?"
                        lived only in the database
                        (docs/ROADMAP_AUDIT_2026-09.md). Nothing listed clients
                        either, which is why this needed an endpoint and not just
                        a form. */}
                    <ClientRatesPanel key={`rates-${hubId}`} hubId={hubId} onToast={showToast} />
                    {/* Directly beneath the rates panel because price and
                        promise are one contract and a credit is computed from
                        both - credit_percent is a percentage of the fee the
                        rate card produced. Both endpoints existed with no
                        caller, so an operator could set a term with a script
                        and then had no way to read back what they agreed to,
                        which is the half that matters when a client disputes a
                        credit (tests/test_no_unreachable_routes.py). */}
                    <ClientSlaTermsPanel key={`sla-${hubId}`} hubId={hubId} onToast={showToast} />
                    {/* Third of the client-contract trio, and last for a
                        reason: price, then promise, then the bill that is
                        computed from both. Nothing in this system had ever
                        raised an invoice - generate_invoice's only call site
                        was an endpoint no front end reached - while the client
                        portal shipped a full viewer reading a table nothing
                        could write. */}
                    <ClientInvoicesPanel key={`invoices-${hubId}`} hubId={hubId} onToast={showToast} />
                    {/* Beside client onboarding, because it is the same kind of
                        act: creating the identity somebody logs in with. Nothing
                        created a driver before this - every row was a
                        hand-written insert (docs/ROADMAP_AUDIT_2026-09.md). */}
                    <OnboardDriverForm hubId={hubId} onToast={showToast} />
                    {/* Beside the two onboarding forms, because it is the third
                        thing nothing could do: `Hub.state_code` selects a
                        driver's overtime rule and was set by nothing at all
                        (docs/ROADMAP_AUDIT_2026-09.md). */}
                    <HubSettingsPanel
                      key={`hub-${hubId}`}
                      hubId={hubId}
                      isAdmin={opsProfile.role === 'admin'}
                      onToast={showToast}
                    />
                    {/* Beside the driver form, because it is the other half of
                        the same job. The revocation endpoint had existed for
                        "the driver lost their phone and rings dispatch" — with
                        nothing that could list a driver's devices to an admin,
                        so ops had to already know an id only the driver could
                        give them (docs/ROADMAP_AUDIT_2026-09.md). */}
                    <DriverDevicesPanel drivers={fleet.data} onToast={showToast} />
                    <UrgencyRulesPanel key={`urgency-${hubId}`} hubId={hubId} onToast={showToast} />
                    <ProposedRulesPanel key={`proposed-${hubId}`} hubId={hubId} onToast={showToast} />
                    {/* Jobs and density in one card: the density report is a
                        summary of exactly those jobs, and split apart a reader
                        would have to join them by eye to know whether 12%
                        sequenced is 3 of 25 or 300 of 2500. Hides itself when
                        the path is empty, which is today. */}
                    <GigPathPanel key={`gig-${hubId}`} hubId={hubId} />
                    {/* No hub key: these are fleet-wide distributions over durable
                        rows, so they do not change when the hub picker does. Both
                        endpoints had existed with no consumer at all (F7). */}
                    <MeasurementPanel />
                  </>
                )}
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
