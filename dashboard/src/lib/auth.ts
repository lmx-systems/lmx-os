// Ops-dashboard session token (docs/ROADMAP.md S1) - a JWT issued by
// POST /ops/auth/login (app/api/ops_auth_routes.py), one login per ops
// user. Stored in localStorage so a page refresh doesn't log the user
// out - this is a real deployed web app, not a Claude artifact sandbox,
// so localStorage is the standard, appropriate choice here. Mirrors
// client-portal/src/lib/auth.ts exactly.
const TOKEN_KEY = 'lmx_ops_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
