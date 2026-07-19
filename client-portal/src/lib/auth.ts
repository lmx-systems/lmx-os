// Client portal session token (Phase 8) - a JWT issued by POST
// /client/auth/login (app/api/client_routes.py), one login per client
// company, not per-user. Stored in localStorage so a page refresh doesn't
// log the client out; this is a real deployed web app (not a Claude
// artifact sandbox), so localStorage is the standard, appropriate choice
// here, unlike in Claude-authored in-chat artifacts.
const TOKEN_KEY = 'lmx_client_portal_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
