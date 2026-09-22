/**
 * What is still being measured, given what the driver has allowed (DRV-5).
 *
 * *"Clear state when permission is denied."* Permission is checked when the
 * location watcher starts and when geofences register — and **never again**. A
 * driver who grants it at 6am and revokes it in Settings at 10am leaves the app
 * believing it is still tracking: registered regions stop firing, the position
 * watcher stops emitting, and nothing notices or says so.
 *
 * The cost is specific and silent. Stop arrivals stop being recorded
 * automatically, so `DRV-1`'s second-precision dwell quietly degrades to
 * tap-grade for that shift — the exact measurement the whole of Phase 1 exists
 * to produce, lost without a line in any log.
 *
 * ## Why this file is pure
 *
 * Deciding *what has been lost* is the part worth testing, and it needs no
 * device, no expo module and no clock. The hook that acts on it is thin by
 * design; this is where the judgement lives.
 *
 * ## What it deliberately does not do
 *
 * It never asks for permission again. The "always" tier is requested once, on a
 * screen that explains it first (`docs/BACKGROUND_LOCATION_CONSENT.md` §4.4) —
 * a cold re-prompt after a driver has deliberately turned it off is how an app
 * earns a permanent denial and an App Store rejection.
 */

export interface LocationPermissions {
  foregroundGranted: boolean;
  backgroundGranted: boolean;
}

export type SensorLoss =
  /** Nothing lost — both tiers still granted. */
  | 'none'
  /**
   * Foreground gone, so nothing is being reported at all. Background implies
   * foreground on both platforms, so this is the total case.
   */
  | 'all'
  /**
   * Position still reports while the app is open; geofence crossings do not.
   * Arrivals fall back to the driver tapping them.
   */
  | 'automatic_arrivals';

export interface DegradedState {
  loss: SensorLoss;
  /** Whether registered geofences should be torn down. */
  shouldStopGeofencing: boolean;
  /** Whether the position watcher should be stopped. */
  shouldStopReporting: boolean;
  /**
   * What to tell the driver, or null when there is nothing to say.
   *
   * Written as a consequence and an instruction, never as a scolding. A driver
   * who turned location off usually meant to, and the useful sentence is what
   * they now have to do instead — not that they should undo it.
   */
  message: string | null;
}

export function degradedState(permissions: LocationPermissions): DegradedState {
  const { foregroundGranted, backgroundGranted } = permissions;

  if (!foregroundGranted) {
    return {
      loss: 'all',
      shouldStopGeofencing: true,
      shouldStopReporting: true,
      message:
        'Location is off for this app. Dispatch cannot see where you are, and ' +
        'arrivals will not be recorded on their own — tap Arrive and Complete ' +
        'at each stop as usual.',
    };
  }

  if (!backgroundGranted) {
    return {
      loss: 'automatic_arrivals',
      // Registered regions do not fire without the "always" tier. Leaving them
      // registered would be the app holding state that claims a sensor it does
      // not have.
      shouldStopGeofencing: true,
      shouldStopReporting: false,
      message:
        'Background location is off, so arrivals will not be recorded on their ' +
        'own — tap Arrive and Complete at each stop as usual.',
    };
  }

  return {
    loss: 'none',
    shouldStopGeofencing: false,
    shouldStopReporting: false,
    message: null,
  };
}
