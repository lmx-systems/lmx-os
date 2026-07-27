import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { formatDate } from '../lib/format'
import type { ClientProfileView, ClientUserView } from '../lib/types'

interface UsersPanelProps {
  // The signed-in admin - used to mark "you" in the list and to keep the
  // UI from offering an action the API would reject anyway (an admin
  // deactivating their own account).
  profile: ClientProfileView
}

// The account-management view (multi-user client accounts,
// docs/ROADMAP.md C4), shown only to an admin - App.tsx hides the tab for
// a member, and the API 403s them regardless. Add a colleague, change a
// role, or deactivate someone who's left.
export function UsersPanel({ profile }: UsersPanelProps) {
  const [users, setUsers] = useState<ClientUserView[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  // Add-user form.
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('member')
  const [adding, setAdding] = useState(false)

  async function reload() {
    setError(null)
    try {
      setUsers(await api.listUsers())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load users.')
    }
  }

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    setAdding(true)
    setError(null)
    try {
      await api.createUser({ email: email.trim(), name: name.trim(), password, role })
      setEmail('')
      setName('')
      setPassword('')
      setRole('member')
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the user.')
    } finally {
      setAdding(false)
    }
  }

  async function patch(userId: string, body: { role?: string; is_active?: boolean }) {
    setBusyId(userId)
    setError(null)
    try {
      await api.updateUser(userId, body)
      await reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update the user.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-[16px] font-semibold text-[var(--text-primary)]">Team</h1>

      {error && (
        <div className="mb-4 rounded-[var(--radius)] border border-[var(--danger-border,var(--border-strong))] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)]">
          {error}
        </div>
      )}

      <form
        onSubmit={handleAdd}
        className="mb-6 grid grid-cols-1 gap-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-4 sm:grid-cols-[1fr_1fr_1fr_auto_auto]"
      >
        <input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        />
        <input
          type="text"
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="Temp password (min 8)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        />
        <select
          value={role}
          onChange={(e) => setRole(e.target.value)}
          className="rounded-[var(--radius)] border border-[var(--border-strong)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)]"
        >
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
        <button
          type="submit"
          disabled={adding}
          className="rounded-[var(--radius)] bg-[var(--accent)] px-3 py-1.5 text-sm font-medium text-white transition-colors duration-150 hover:bg-[var(--accent-hover)] disabled:opacity-60"
        >
          {adding ? 'Adding…' : 'Add user'}
        </button>
      </form>

      {users === null ? (
        <div className="text-sm text-[var(--text-muted)]">Loading team…</div>
      ) : (
        <div className="overflow-hidden rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-xs font-medium text-[var(--text-muted)]">
                <th className="px-4 py-2.5">Name</th>
                <th className="px-4 py-2.5">Email</th>
                <th className="px-4 py-2.5">Role</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5">Added</th>
                <th className="px-4 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                // The signed-in admin, matched by email (profile carries
                // the user's email, not their id) - can't act on their own
                // row from here.
                const isSelf = u.email === profile.email
                const busy = busyId === u.client_user_id
                return (
                  <tr key={u.client_user_id} className="border-b border-[var(--border)] last:border-0">
                    <td className="px-4 py-2.5 font-medium text-[var(--text-primary)]">
                      {u.name}
                      {isSelf && <span className="ml-1.5 text-xs text-[var(--text-muted)]">(you)</span>}
                    </td>
                    <td className="px-4 py-2.5 text-[var(--text-secondary)]">{u.email}</td>
                    <td className="px-4 py-2.5 text-[var(--text-secondary)] capitalize">{u.role}</td>
                    <td className="px-4 py-2.5">
                      <span
                        className={
                          u.is_active
                            ? 'text-[var(--text-secondary)]'
                            : 'text-[var(--text-muted)] line-through'
                        }
                      >
                        {u.is_active ? 'Active' : 'Deactivated'}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-[var(--text-secondary)]">{formatDate(u.created_at)}</td>
                    <td className="px-4 py-2.5 text-right">
                      {isSelf ? (
                        // You can't demote/deactivate yourself from here -
                        // the last-admin guard is server-side, and this
                        // keeps you from locking yourself out by accident.
                        <span className="text-xs text-[var(--text-muted)]">—</span>
                      ) : (
                        <div className="flex justify-end gap-2">
                          <button
                            disabled={busy}
                            onClick={() =>
                              patch(u.client_user_id, {
                                role: u.role === 'admin' ? 'member' : 'admin',
                              })
                            }
                            className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-2)] disabled:opacity-60"
                          >
                            {u.role === 'admin' ? 'Make member' : 'Make admin'}
                          </button>
                          <button
                            disabled={busy}
                            onClick={() => patch(u.client_user_id, { is_active: !u.is_active })}
                            className="rounded-[var(--radius)] border border-[var(--border-strong)] px-2 py-1 text-xs font-medium text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-2)] disabled:opacity-60"
                          >
                            {u.is_active ? 'Deactivate' : 'Reactivate'}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
