// Shared between KpiStrip's "at risk" count and HoldQueueTable's row
// highlighting - a single definition so the two can't drift apart.
export const AT_RISK_MINUTES = 5

export function minutesUntil(isoDeadline: string): number {
  return (new Date(isoDeadline).getTime() - Date.now()) / 60_000
}

export function formatCountdown(isoDeadline: string): string {
  const minutes = minutesUntil(isoDeadline)
  if (minutes <= 0) return 'past deadline'
  const rounded = Math.round(minutes)
  return rounded < 1 ? '<1 min' : `${rounded} min`
}

export function truncateId(id: string, length = 8): string {
  return id.slice(0, length)
}

// Driver display name isn't available yet - DriverState (Redis) carries
// driver_id only, not the Driver.name row from Postgres (see
// docs/NEXT_STEPS.md). Two uppercase hex characters from the id stand in
// for an avatar's initials until that's wired up.
export function idInitials(id: string): string {
  return id.replace(/-/g, '').slice(0, 2).toUpperCase()
}

export function nameInitials(name: string): string {
  const parts = name.trim().split(/\s+/)
  return parts
    .slice(0, 2)
    .map((p) => p[0])
    .join('')
    .toUpperCase()
}

export function formatSecondsAgo(seconds: number): string {
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ago`
}

export function formatIsoRelative(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  return formatSecondsAgo(seconds)
}
