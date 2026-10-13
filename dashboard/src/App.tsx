import { useEffect, useState } from 'react'
import { TopBar, type PageSpec } from './components/TopBar'
import { KpiStrip } from './components/KpiStrip'
import { OrderPipeline } from './components/OrderPipeline'
import { HoldQueueTable } from './components/HoldQueueTable'
import { ExceptionsPanel } from './components/ExceptionsPanel'
import { isGoingAhead } from './lib/exceptions'
import { RulesTile, TouchesTile } from './components/HomeTiles'
import { Card } from './components/ui/Card'
import { DockLabellingPanel } from './components/DockLabellingPanel'
import { LabellingQueuePanel } from './components/LabellingQueuePanel'
import { SiteFactDisputesPanel } from './components/SiteFactDisputesPanel'
import { DockLogReviewPanel } from './components/DockLogReviewPanel'
import { HubSettingsPanel } from './components/HubSettingsPanel'
import { CreateHubPanel } from './components/CreateHubPanel'
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
import { PlaybookPanel } from './components/PlaybookPanel'
import { CodDisputesPanel } from './components/CodDisputesPanel'
import { SupportInboxPanel } from './components/SupportInboxPanel'
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
  // Bumped when a hub is created, so the top bar's list picks it up.
  const [hubsVersion, setHubsVersion] = useState(0)
  const { message, showToast } = useToast()

  // Real per-account ops auth (docs/ROADMAP.md S1), replacing the old
  // shared X-API-Key stopgap - mirrors client-portal/src/App.tsx's
  // loggedIn/profile gating exactly.
  const [loggedIn, setLoggedIn] = useState(() => getToken() !== null)
  const [opsProfile, setOpsProfile] = useState<OpsProfileView | null>(null)
  // Which page is open, kept in the address (#orders) so a link or a reload
  // lands on the same page. No hash is Home: somebody opening the console is
  // starting a shift, and Home is the live work (CON-1).
  const [page, setPage] = useState(pageFromHash)
  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  function goTo(id: string) {
    if (id === page) return
    window.location.hash = id
    setPage(id)
    window.scrollTo({ top: 0 })
  }
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

  // The two lines the server draws (app/ops_auth/dependencies.py): admins
  // configure the hub, dispatchers do the day's order work, viewers read. A
  // control a role would get a 403 from is never shown to it.
  const isAdmin = opsProfile.role === 'admin'
  const canDispatch = isAdmin || opsProfile.role === 'dispatcher'

  const needsCount = exceptions.data ? exceptions.data.items.filter((i) => !isGoingAhead(i)).length : undefined
  const recordBadge = attention.data
    ? attention.data.late_orders +
      attention.data.merge_proposals +
      attention.data.unlabelled_docks +
      attention.data.site_fact_disputes
    : undefined
  const pages: PageSpec[] =
    hubId.length === 0
      ? []
      : [
          { id: 'home', label: 'Home', badge: needsCount },
          { id: 'orders', label: 'Orders' },
          { id: 'drivers', label: 'Drivers' },
          { id: 'rules', label: 'Rules' },
          // The badge is what keeps this page honest (CON-1): a page with no
          // count is a page nobody opens, which would trade a cluttered board
          // for work that silently stops getting done.
          { id: 'record', label: 'Record', badge: recordBadge },
          ...(isAdmin
            ? [
                { id: 'clients', label: 'Clients' },
                { id: 'settings', label: 'Settings' },
              ]
            : []),
        ]
  // A page this role cannot see (a bookmarked #settings on a dispatcher's
  // account) falls back to Home rather than to a blank screen.
  const current = pages.some((p) => p.id === page) ? page : 'home'

  const refreshAfterException = () => {
    exceptions.refetchNow()
    // A redelivery is back in the hold queue.
    held.refetchNow()
    // An offer takes a driver out of the available pool.
    fleet.refetchNow()
  }

  return (
    <div className="min-h-screen">
      <div className="mx-auto max-w-[1440px] px-4 py-6 pb-16 sm:px-8">
        <TopBar
          hubId={hubId}
          hubsVersion={hubsVersion}
          onChangeHubId={setHubId}
          lastUpdatedAt={hubId.length > 0 ? lastUpdatedAt : null}
          opsProfile={opsProfile}
          onLogout={handleLogout}
          pages={pages}
          page={current}
          onPage={goTo}
        />

        {hubId.length === 0 ? (
          <div className="mx-auto max-w-xl space-y-4">
            <p className="rounded-[var(--radius-lg)] bg-[var(--surface)] p-6 text-center text-sm text-[var(--text-secondary)]">
              Select a hub above to load fleet state, the hold queue, and order status for that
              hub.
            </p>
            <CreateHubPanel
              isAdmin={isAdmin}
              onCreated={(hub) => {
                setHubsVersion((v) => v + 1)
                setHubId(hub.id)
                showToast(`${hub.name} created.`)
              }}
            />
          </div>
        ) : (
          <>
            {current === 'home' && (
              <>
                {/* Home is the on-call view (decision D-SEAT): what a rule may
                    not decide, what the playbook is about to do, and how much
                    of the day still needed a person. Everything else is a
                    page away, so a good day is a mostly empty screen. */}
                <PageHeader
                  kicker={greeting()}
                  title={
                    needsCount === undefined
                      ? 'Loading…'
                      : needsCount === 0
                        ? 'Nothing needs you.'
                        : needsCount === 1
                          ? 'One thing needs you.'
                          : `${needsCount} things need you.`
                  }
                  description="The playbook handles what its rules cover. These are the ones it isn't allowed to decide, and the ones it is about to."
                />
                <div className="flex flex-wrap items-start gap-4">
                  <div className="flex min-w-0 flex-[7_1_560px] flex-col gap-4">
                    <ExceptionsPanel
                      group="needs"
                      data={exceptions.data}
                      error={exceptions.error}
                      loading={exceptions.loading}
                      canDispatch={canDispatch}
                      drivers={fleet.data}
                      driversError={fleet.error !== null}
                      onResolved={refreshAfterException}
                      onToast={showToast}
                    />
                    {/* A driver's message is somebody standing at a locked
                        gate - a person's, so it sits under Needs you. */}
                    {canDispatch && (
                      <SupportInboxPanel key={`support-${hubId}`} hubId={hubId} onToast={showToast} />
                    )}
                  </div>
                  <div className="flex min-w-0 flex-[5_1_360px] flex-col gap-4">
                    <TouchesTile key={`touches-${hubId}`} hubId={hubId} />
                    <ExceptionsPanel
                      group="soon"
                      data={exceptions.data}
                      error={exceptions.error}
                      loading={exceptions.loading}
                      canDispatch={canDispatch}
                      drivers={fleet.data}
                      driversError={fleet.error !== null}
                      onResolved={refreshAfterException}
                      onToast={showToast}
                    />
                    <RulesTile key={`rules-${hubId}`} hubId={hubId} onOpen={() => goTo('rules')} />
                    <FleetMap key={`map-${hubId}`} data={fleet.data} error={fleet.error} loading={fleet.loading} />
                  </div>
                </div>
                <div className="mt-4">
                  <KpiStrip
                    fleet={fleet.data}
                    fleetError={fleet.error}
                    held={held.data}
                    heldError={held.error}
                    summary={summary.data}
                    summaryError={summary.error}
                    lastCycle={lastCycle.data}
                  />
                </div>
              </>
            )}

            {current === 'orders' && (
              <>
                <PageHeader title="Orders" description="Find any order, see where the day's orders are, and what is waiting to go out." />
                <div className="flex flex-col gap-4">
                  {/* First, because it is what somebody reaches for when the
                      phone rings - and that is not a scheduled moment (CON-1). */}
                  <OrderLookupPanel key={`lookup-${hubId}`} hubId={hubId} canDispatch={canDispatch} onToast={showToast} />
                  <OrderPipeline summary={summary.data} error={summary.error} loading={summary.loading} />
                  <HoldQueueTable
                    key={hubId}
                    data={held.data}
                    error={held.error}
                    loading={held.loading}
                    canDispatch={canDispatch}
                    onCancelled={() => {
                      held.refetchNow()
                      exceptions.refetchNow()
                    }}
                    onToast={showToast}
                  />
                </div>
              </>
            )}

            {current === 'drivers' && (
              <>
                <PageHeader title="Drivers" description="Where the fleet is, who is free, and the paperwork that keeps them on the road." />
                <div className="grid items-start gap-4 lg:grid-cols-[1.4fr_1fr]">
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Map above the roster: "where is my fleet" is the
                        glance, and the roster is the detail. Both read the
                        same polled fleet data, so they cannot disagree. */}
                    <FleetMap key={`map-${hubId}`} data={fleet.data} error={fleet.error} loading={fleet.loading} />
                    <FleetRoster data={fleet.data} error={fleet.error} loading={fleet.loading} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Forcing a dispatch cycle is the day's work, so a
                        dispatcher may; running the nightly job by hand is an
                        admin's, and the panel shows that card only to one. */}
                    {canDispatch && (
                      <OperationsPanel
                        key={hubId}
                        hubId={hubId}
                        isAdmin={isAdmin}
                        onAfterRun={() => {
                          fleet.refetchNow()
                          held.refetchNow()
                          summary.refetchNow()
                          lastCycle.refetchNow()
                        }}
                        onToast={showToast}
                      />
                    )}
                    {isAdmin && (
                      <>
                        {/* No hub key: compliance review is whole-company, since
                            an unreviewed license keeps a driver off the road
                            everywhere. */}
                        <DriverDocumentsPanel onToast={showToast} />
                        {/* Nothing created a driver before this - every row was
                            a hand-written insert (docs/ROADMAP_AUDIT_2026-09.md). */}
                        <OnboardDriverForm hubId={hubId} onToast={showToast} />
                        {/* The other half of the same job: a driver who lost
                            their phone needs an admin who can list their
                            devices, not just revoke one by id. */}
                        <DriverDevicesPanel key={`devices-${hubId}`} hubId={hubId} onToast={showToast} />
                      </>
                    )}
                  </div>
                </div>
              </>
            )}

            {current === 'rules' && (
              <>
                <PageHeader
                  title="How much the playbook decides alone"
                  description="Every rule starts by suggesting. When people agree with it often enough, it earns the next step. Moving a rule up is a setting a person changes."
                />
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  {/* How much still needs a person, and how the rules taking it
                      over are doing (DEC-7, DEC-8). */}
                  <PlaybookPanel key={`playbook-${hubId}`} hubId={hubId} />
                  {isAdmin && (
                    <div className="flex min-w-0 flex-col gap-4">
                      <UrgencyRulesPanel key={`urgency-${hubId}`} hubId={hubId} onToast={showToast} />
                      <ProposedRulesPanel key={`proposed-${hubId}`} hubId={hubId} onToast={showToast} />
                    </div>
                  )}
                </div>
              </>
            )}

            {/* Mounted only when the page is open, which is what makes the
                loading lazy: each of these fetches on mount. */}
            {current === 'record' && (
              <>
                <PageHeader title="Record" description="Real work and none of it urgent: the facts about places, docks and returns the next delivery depends on." />
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-4">
                    <RecordLayerPanel key={`record-${hubId}`} hubId={hubId} canDispatch={canDispatch} />
                    {/* Not hub-scoped: the same physical dock can be reached
                        from two hubs and that pair is the most valuable merge
                        to catch (IDN-2). */}
                    <MergeReviewPanel isAdmin={isAdmin} />
                    {/* Also not hub-scoped: a dock is a physical place and its
                        class does not change with which hub serves it (IDN-3). */}
                    <DockLabellingPanel canDispatch={canDispatch} />
                    {/* Not hub-scoped either: the golden record's labels are
                        about a place and a machine (DE-9). */}
                    <LabellingQueuePanel canDispatch={canDispatch} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Answers at the door that disagree with a site's facts,
                        kept rather than applied so a stale phone cannot undo a
                        newer answer - and so somebody has to settle them
                        (DE-15). In the badge, because settling them makes the
                        count fall. */}
                    <SiteFactDisputesPanel canDispatch={canDispatch} />
                    {/* Beside dock labelling because it is the same act on the
                        same rows - deciding what a dock is (DRV-7). */}
                    <DockLogReviewPanel onToast={showToast} isAdmin={isAdmin} />
                    {/* Deliberately NOT in the badge: with no mail server every
                        dispute is un-escalated by definition, so the count would
                        never fall. Its list is a dispatcher's read on the
                        server; a viewer would only see it fail to load. */}
                    {canDispatch && <CodDisputesPanel key={`cod-${hubId}`} hubId={hubId} />}
                    {/* The half of W1 that shipped without a front end. Hides
                        itself when nothing is outstanding. */}
                    {canDispatch && <ReturnsPanel key={`returns-${hubId}`} hubId={hubId} onToast={showToast} />}
                    {/* Last, because it is the read-back rather than the work:
                        is the record being written at all (REC-1..REC-4). */}
                    <RecordHealthPanel key={`health-${hubId}`} hubId={hubId} />
                  </div>
                </div>
              </>
            )}

            {/* Configuring the business is admin-only on the backend
                (app/ops_auth/dependencies.py's require_admin) - a dispatcher
                or viewer never sees the controls for actions they'd get a 403
                from, and never sees these pages. */}
            {current === 'clients' && isAdmin && (
              <>
                <PageHeader title="Clients" description="Who is waiting to join, and each client's price, promise and bill." />
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Above the manual form on purpose: an applicant who is
                        already waiting should be approved, not re-created by
                        hand as a second client record. */}
                    <PendingSignupsPanel key={`signups-${hubId}`} hubId={hubId} onToast={showToast} />
                    <OnboardClientForm hubId={hubId} onToast={showToast} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Price, then promise, then the bill computed from both:
                        a credit is a percentage of the fee the rate card
                        produced. */}
                    <ClientRatesPanel key={`rates-${hubId}`} hubId={hubId} onToast={showToast} />
                    <ClientSlaTermsPanel key={`sla-${hubId}`} hubId={hubId} onToast={showToast} />
                    <ClientInvoicesPanel key={`invoices-${hubId}`} hubId={hubId} onToast={showToast} />
                  </div>
                </div>
              </>
            )}

            {current === 'settings' && isAdmin && (
              <>
                <PageHeader title="Settings" description="The hub itself, new hubs, and the measurements behind the numbers." />
                <div className="grid items-start gap-4 lg:grid-cols-2">
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* `Hub.state_code` selects a driver's overtime rule and
                        was set by nothing at all before this panel. */}
                    <HubSettingsPanel key={`hub-${hubId}`} hubId={hubId} isAdmin={isAdmin} onToast={showToast} />
                    <CreateHubPanel
                      isAdmin={isAdmin}
                      onCreated={(hub) => {
                        setHubsVersion((v) => v + 1)
                        setHubId(hub.id)
                        showToast(`${hub.name} created.`)
                      }}
                    />
                  </div>
                  <div className="flex min-w-0 flex-col gap-4">
                    {/* Jobs and density in one card; hides itself when the
                        path is empty, which is today. */}
                    <GigPathPanel key={`gig-${hubId}`} hubId={hubId} />
                    {/* No hub key: fleet-wide distributions over durable rows (F7). */}
                    <Card title="Measurement">
                      <MeasurementPanel />
                    </Card>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        <p className="mt-10 text-xs text-[var(--text-secondary)]">
          Internal use only. See docs/ARCHITECTURE.md for known gaps.
        </p>
      </div>

      <Toast message={message} />
    </div>
  )
}

const PAGE_IDS = ['home', 'orders', 'drivers', 'rules', 'record', 'clients', 'settings']

function pageFromHash(): string {
  const id = window.location.hash.slice(1)
  return PAGE_IDS.includes(id) ? id : 'home'
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning.'
  if (hour < 18) return 'Good afternoon.'
  return 'Good evening.'
}

function PageHeader({ kicker, title, description }: { kicker?: string; title: string; description: string }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-2 px-1">
      <div className="flex flex-col gap-1">
        {kicker && <span className="text-[15px] text-[var(--text-secondary)]">{kicker}</span>}
        <h1 className="text-[clamp(30px,4vw,44px)] font-extrabold leading-[1.05] tracking-[-0.045em] [text-wrap:balance]">
          {title}
        </h1>
      </div>
      <p className="max-w-[52ch] text-[15px] text-[var(--text-secondary)]">{description}</p>
    </div>
  )
}

export default App
