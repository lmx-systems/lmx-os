import { useEffect, useState } from 'react'
import { api } from './lib/api'
import { clearToken, getToken } from './lib/auth'
import type {
  ClientOrderDetailView,
  ClientOrderSummaryView,
  ClientProfileView,
  InvoiceDetailView,
  InvoiceSummaryView,
} from './lib/types'
import { LoginPage } from './components/LoginPage'
import { SignupPage } from './components/SignupPage'
import { ResetPasswordPage } from './components/ResetPasswordPage'
import { NewOrderForm } from './components/NewOrderForm'
import { BulkPastePanel } from './components/BulkPastePanel'
import { TopBar } from './components/TopBar'
import { OrdersTable } from './components/OrdersTable'
import { OrderDetail } from './components/OrderDetail'
import { InvoicesTable } from './components/InvoicesTable'
import { InvoiceDetail } from './components/InvoiceDetail'
import { UsersPanel } from './components/UsersPanel'
import { ReturnsPanel } from './components/ReturnsPanel'

// No client-side routing yet (same call as dashboard/ - see its
// nginx.conf comment) - a single view-state variable is enough for the
// screens this app has today: orders list, order detail, invoices list,
// invoice detail, login.
type View =
  | { name: 'new-order' }
  | { name: 'orders' }
  | { name: 'order-detail'; orderId: string }
  | { name: 'invoices' }
  | { name: 'invoice-detail'; invoiceId: string }
  | { name: 'returns' }
  | { name: 'team' }

export default function App() {
  const [loggedIn, setLoggedIn] = useState(() => getToken() !== null)
  const [showSignup, setShowSignup] = useState(
    () => typeof window !== 'undefined' && window.location.pathname.replace(/\/$/, '') === '/signup',
  )
  // Reset links arrive by email as /reset-password?token=… Read once on mount:
  // the token is a single-use credential and re-reading it after redemption
  // would only ever find a spent one.
  const [resetToken, setResetToken] = useState(() =>
    typeof window !== 'undefined' &&
    window.location.pathname.replace(/\/$/, '') === '/reset-password'
      ? new URLSearchParams(window.location.search).get('token')
      : null,
  )
  const [profile, setProfile] = useState<ClientProfileView | null>(null)
  const [orders, setOrders] = useState<ClientOrderSummaryView[] | null>(null)
  const [selectedOrder, setSelectedOrder] = useState<ClientOrderDetailView | null>(null)
  const [invoices, setInvoices] = useState<InvoiceSummaryView[] | null>(null)
  const [selectedInvoice, setSelectedInvoice] = useState<InvoiceDetailView | null>(null)
  const [view, setView] = useState<View>({ name: 'orders' })
  const [bulkMode, setBulkMode] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    if (!loggedIn) return
    let cancelled = false
    setLoadError(null)
    Promise.all([api.myProfile(), api.myOrders()])
      .then(([profileResult, ordersResult]) => {
        if (cancelled) return
        setProfile(profileResult)
        setOrders(ordersResult)
      })
      .catch(() => {
        if (cancelled) return
        // Most likely an expired/invalid token - api.ts already cleared it
        // on a 401, so drop back to the login screen.
        setLoggedIn(getToken() !== null)
        setLoadError('Could not load your account. Please sign in again.')
      })
    return () => {
      cancelled = true
    }
  }, [loggedIn])

  useEffect(() => {
    if (view.name !== 'order-detail') return
    let cancelled = false
    api.myOrder(view.orderId).then((order) => {
      if (!cancelled) setSelectedOrder(order)
    })
    return () => {
      cancelled = true
    }
  }, [view])

  useEffect(() => {
    if (view.name !== 'invoices' || invoices !== null) return
    let cancelled = false
    api.myInvoices().then((result) => {
      if (!cancelled) setInvoices(result)
    })
    return () => {
      cancelled = true
    }
  }, [view, invoices])

  useEffect(() => {
    if (view.name !== 'invoice-detail') return
    let cancelled = false
    api.myInvoice(view.invoiceId).then((invoice) => {
      if (!cancelled) setSelectedInvoice(invoice)
    })
    return () => {
      cancelled = true
    }
  }, [view])

  function handleLogout() {
    clearToken()
    setLoggedIn(false)
    setProfile(null)
    setOrders(null)
    setInvoices(null)
    setSelectedOrder(null)
    setSelectedInvoice(null)
    setView({ name: 'orders' })
  }

  // The public signup page - the URL LMX shares or embeds. Checked before the
  // auth gate because a prospective client has no token by definition. Path
  // rather than a hash so the link reads like a normal page; nginx.conf's SPA
  // fallback already serves index.html for it.
  if (!loggedIn && resetToken) {
    return <ResetPasswordPage token={resetToken} onDone={() => setResetToken(null)} />
  }

  if (!loggedIn && showSignup) {
    return <SignupPage onSignedIn={() => setShowSignup(false)} />
  }

  if (!loggedIn) {
    return <LoginPage onLoggedIn={() => setLoggedIn(true)} />
  }

  if (!profile || !orders) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--text-muted)]">
        {loadError ?? 'Loading your account…'}
      </div>
    )
  }

  const onNewOrderTab = view.name === 'new-order'
  const onOrdersTab = view.name === 'orders' || view.name === 'order-detail'
  const onInvoicesTab = view.name === 'invoices' || view.name === 'invoice-detail'
  const onReturnsTab = view.name === 'returns'
  const onTeamTab = view.name === 'team'
  // The Team tab (user management, docs/ROADMAP.md C4) is admin-only - the
  // API 403s a member regardless, but there's no reason to show them a tab
  // they can't use.
  const isAdmin = profile.role === 'admin'

  return (
    <div className="min-h-screen bg-[var(--bg-page)]">
      <TopBar profile={profile} onLogout={handleLogout} />
      <main className="mx-auto max-w-5xl px-6 py-6">
        <nav className="mb-5 flex gap-1 border-b border-[var(--border)] print:hidden">
          <button
            onClick={() => setView({ name: 'new-order' })}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              onNewOrderTab
                ? 'border-[var(--accent)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
          >
            New order
          </button>
          <button
            onClick={() => setView({ name: 'orders' })}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              onOrdersTab
                ? 'border-[var(--accent)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
          >
            Orders
          </button>
          <button
            onClick={() => setView({ name: 'invoices' })}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              onInvoicesTab
                ? 'border-[var(--accent)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
          >
            Invoices
          </button>
          <button
            onClick={() => setView({ name: 'returns' })}
            className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              onReturnsTab
                ? 'border-[var(--accent)] text-[var(--text-primary)]'
                : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
            }`}
          >
            Returns
          </button>
          {isAdmin && (
            <button
              onClick={() => setView({ name: 'team' })}
              className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150 ${
                onTeamTab
                  ? 'border-[var(--accent)] text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-primary)]'
              }`}
            >
              Team
            </button>
          )}
        </nav>

        {view.name === 'new-order' && (
          <div className="max-w-xl">
            <div className="mb-4 flex items-baseline justify-between gap-3">
              <h1 className="text-[16px] font-semibold text-[var(--text-primary)]">
                {bulkMode ? 'Send several deliveries' : 'Send a delivery'}
              </h1>
              {/* A quiet text switch rather than tabs: the single-order form is
                  what a counter uses all day, and paste is an occasional
                  back-office move. Making them equal-weight would suggest
                  otherwise. */}
              <button
                onClick={() => setBulkMode((v) => !v)}
                className="shrink-0 text-[13px] font-medium text-[var(--accent)] hover:underline"
              >
                {bulkMode ? 'Just one delivery' : 'Paste several'}
              </button>
            </div>
            {bulkMode ? (
              <BulkPastePanel
                onOrdersPlaced={() => {
                  api.myOrders().then(setOrders).catch(() => {})
                }}
              />
            ) : (
              <NewOrderForm
                onOrderPlaced={() => {
                  // Refresh the orders list in the background so switching tabs
                  // shows the new order rather than a stale list - without
                  // yanking the user off the confirmation they just earned.
                  api.myOrders().then(setOrders).catch(() => {})
                }}
              />
            )}
          </div>
        )}
        {view.name === 'orders' && (
          <>
            <h1 className="mb-4 text-[16px] font-semibold text-[var(--text-primary)]">Your orders</h1>
            <OrdersTable orders={orders} onSelect={(orderId) => setView({ name: 'order-detail', orderId })} />
          </>
        )}
        {view.name === 'order-detail' &&
          (selectedOrder ? (
            <OrderDetail order={selectedOrder} onBack={() => setView({ name: 'orders' })} />
          ) : (
            <div className="text-sm text-[var(--text-muted)]">Loading order…</div>
          ))}
        {view.name === 'invoices' &&
          (invoices ? (
            <>
              <h1 className="mb-4 text-[16px] font-semibold text-[var(--text-primary)]">Your invoices</h1>
              <InvoicesTable
                invoices={invoices}
                onSelect={(invoiceId) => setView({ name: 'invoice-detail', invoiceId })}
              />
            </>
          ) : (
            <div className="text-sm text-[var(--text-muted)]">Loading invoices…</div>
          ))}
        {view.name === 'invoice-detail' &&
          (selectedInvoice ? (
            <InvoiceDetail
              invoice={selectedInvoice}
              profile={profile}
              onBack={() => setView({ name: 'invoices' })}
            />
          ) : (
            <div className="text-sm text-[var(--text-muted)]">Loading invoice…</div>
          ))}
        {view.name === 'returns' && <ReturnsPanel />}
        {view.name === 'team' && isAdmin && <UsersPanel profile={profile} />}
      </main>
    </div>
  )
}
