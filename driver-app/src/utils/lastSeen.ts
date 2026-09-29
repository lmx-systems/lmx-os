/**
 * "3 minutes ago" rather than an ISO timestamp (`docs/ROADMAP.md` S1).
 *
 * The question a driver is answering on the devices screen is *"is that one
 * mine?"*, and they answer it by recognising when they last used a phone. An
 * ISO string makes them do arithmetic; a relative time is the fact itself.
 *
 * **An unparseable timestamp reads "unknown", never "just now".** `new Date`
 * yields `NaN` for anything it cannot read, and `NaN` arithmetic would fall
 * through every comparison below to the last branch — so a broken value would
 * render as a confident number. On a screen whose only purpose is deciding
 * which session to end, a wrong-but-plausible time is worse than an admission.
 */
export function lastSeen(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return 'unknown';
  // Floor, not round: "2 min ago" has to mean at least two minutes have
  // passed. Rounding made 30 seconds read "1 min ago", so "just now" never
  // meant what it says.
  const minutes = Math.floor((now - then) / 60000);
  // A clock that disagrees with the server's puts a session slightly in the
  // future. "just now" is the honest reading of that, not a negative age.
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}
