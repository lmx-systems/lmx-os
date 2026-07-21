// Ops dashboard session token (roadmap item S1) - a JWT issued by POST
// /ops/auth/login (app/api/ops_auth_routes.py), per-user with a role
// claim. Stored in localStorage so a refresh doesn't log the operator
// out mid-shift; tokens expire after ~a shift (12h) server-side.
const TOKEN_KEY = 'lmx-os-dashboard.ops-token'

export function getOpsToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setOpsToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearOpsToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
