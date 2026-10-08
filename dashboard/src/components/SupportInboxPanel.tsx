import { useEffect, useRef, useState } from 'react'
import { Card } from './ui/Card'
import { api } from '../lib/api'
import type { SupportMessage, SupportThread } from '../lib/types'

/**
 * Drivers' support messages, and the reply (screens 1p/1q).
 *
 * A driver writes from the app's support screen: wrong address, blocked access,
 * a safety problem. That used to be texted to a support phone number through
 * Twilio, and nothing in the console showed it. Now the thread lives here and in
 * the app; a reply appears on the driver's support screen and, where push is set
 * up, on their lock screen.
 *
 * Threads waiting for an answer come first, oldest first, because the driver who
 * has waited longest is the one standing at a locked gate.
 */

const POLL_MS = 15_000

function when(value: string): string {
  return new Date(value).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function SupportInboxPanel({ hubId, onToast }: { hubId: string; onToast: (message: string) => void }) {
  const [threads, setThreads] = useState<SupportThread[] | null>(null)
  const [driverId, setDriverId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SupportMessage[] | null>(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The thread on screen; a late response for another driver is dropped.
  const current = useRef<string | null>(null)
  // Set before the request goes, so two quick Enters can't both send.
  const inFlight = useRef(false)

  async function loadThreads() {
    try {
      setThreads(await api.supportInbox(hubId))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function loadThread(id: string) {
    try {
      const thread = await api.supportThread(id)
      if (current.current === id) {
        setMessages(thread)
        setError(null)
      }
    } catch (e) {
      if (current.current === id) setError((e as Error).message)
    }
  }

  useEffect(() => {
    void loadThreads()
    const timer = setInterval(() => {
      void loadThreads()
      if (current.current) void loadThread(current.current)
    }, POLL_MS)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hubId])

  function open(id: string) {
    current.current = id
    setDriverId(id)
    setMessages(null)
    setDraft('')
    setError(null)
    void loadThread(id)
  }

  async function reply() {
    const to = driverId
    if (inFlight.current || !to || !draft.trim()) return
    inFlight.current = true
    setSending(true)
    setError(null)
    try {
      const sent = await api.replyToDriver(to, draft.trim())
      if (current.current === to) {
        setMessages((prev) => [...(prev ?? []), sent])
        setDraft('')
      }
      void loadThreads()
      onToast('Reply sent. It shows on the driver’s support screen.')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      inFlight.current = false
      setSending(false)
    }
  }

  const waiting = threads?.filter((t) => t.awaiting_reply).length ?? 0
  const selected = threads?.find((t) => t.driver_id === driverId) ?? null

  return (
    <Card title="Driver support" meta={waiting ? `${waiting} waiting` : undefined}>
      {error && <p className="mb-1.5 text-[12px] text-[var(--red)]">{error}</p>}
      {threads === null && !error && <p className="text-[12px] text-[var(--text-muted)]">loading…</p>}
      {threads?.length === 0 && (
        <p className="text-[12px] text-[var(--text-muted)]">
          No messages. Drivers write from the app’s support screen, and they land here.
        </p>
      )}

      {threads && threads.length > 0 && (
        <ul className="mb-2 space-y-1">
          {threads.map((thread) => (
            <li key={thread.driver_id}>
              <button
                type="button"
                onClick={() => open(thread.driver_id)}
                className={`w-full rounded-md border px-2 py-1 text-left text-[12px] ${
                  thread.driver_id === driverId ? 'border-[var(--accent)]' : 'border-[var(--border)]'
                }`}
              >
                <span className="font-medium text-[var(--text-primary)]">{thread.driver_name}</span>
                {thread.awaiting_reply && (
                  <span className="ml-1.5 text-[11px] text-[var(--amber)]">waiting since {when(thread.last_at)}</span>
                )}
                <span className="block truncate text-[var(--text-secondary)]">{thread.last_body}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <div className="border-t border-[var(--border)] pt-2">
          {messages === null ? (
            <p className="text-[12px] text-[var(--text-muted)]">loading…</p>
          ) : (
            <ul className="mb-2 max-h-64 space-y-1.5 overflow-y-auto">
              {messages.map((message) => (
                <li
                  key={message.message_id}
                  className={`rounded-md px-2 py-1 text-[12.5px] ${
                    message.from_driver
                      ? 'mr-8 bg-[var(--surface-2)] text-[var(--text-primary)]'
                      : 'ml-8 bg-[var(--accent-dim)] text-[var(--text-primary)]'
                  }`}
                >
                  {message.body}
                  <span className="block text-[10.5px] text-[var(--text-muted)]">
                    {message.from_driver ? selected.driver_name : (message.sent_by ?? 'Dispatch')} · {when(message.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <label htmlFor="support-reply" className="sr-only">
            Reply to {selected.driver_name}
          </label>
          <div className="flex gap-2">
            <input
              id="support-reply"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.nativeEvent.isComposing) void reply()
              }}
              disabled={sending}
              placeholder={`Reply to ${selected.driver_name}`}
              maxLength={1600}
              className="min-w-0 flex-1 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 text-[13px] text-[var(--text-primary)]"
            />
            <button
              type="button"
              disabled={sending || !draft.trim()}
              onClick={() => void reply()}
              className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40"
            >
              {sending ? 'Sending…' : 'Reply'}
            </button>
          </div>
        </div>
      )}
    </Card>
  )
}
