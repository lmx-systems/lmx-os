/**
 * What Log out has to ask before it clears the token (`docs/ROADMAP.md` S1).
 *
 * **Signing out with queued work does not send it and does not discard it — it
 * strands it.** The outbox survives in AsyncStorage, but every subsequent
 * flush 401s and `refreshOnce` has no session left to refresh with, so the
 * items retry against nothing until somebody signs back in on this same phone.
 *
 * That is the failure `DRV-4` was written for, arriving by a different door.
 * `flush` deliberately treats a 401 as *transient* rather than permanent so a
 * shift queued in a dead zone is not lost — and that reasoning holds only
 * while the driver can still authenticate. Log out is the one tap that breaks
 * the assumption, and it was a bare `onPress={signOut}` with nothing in front
 * of it.
 *
 * A pure function in `utils/` rather than a method on the screen for the same
 * reason `dock_needs_survey` lives on the server: one rule, one place, tested
 * without mounting anything. `DevicesScreen` states the same constraint for
 * revoking the current device and defers here for the wording.
 *
 * `null` means log out without asking. Asking when there is nothing queued
 * would train drivers to dismiss the dialog, which is how the warning stops
 * working on the day it matters.
 */
export function logOutWarning(pending: number): string | null {
  // Negative is not a real state, and treating it as "ask" would put a dialog
  // in front of a driver for a number that cannot happen.
  if (pending <= 0) return null;
  const subject =
    pending === 1 ? 'One stop update has' : `${pending} stop updates have`;
  return (
    `${subject} not reached us yet. Logging out now leaves ` +
    `${pending === 1 ? 'it' : 'them'} stuck on this phone until you sign back in.`
  );
}
