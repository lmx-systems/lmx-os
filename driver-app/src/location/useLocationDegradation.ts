import { useEffect, useState } from 'react';
import * as Location from 'expo-location';

import { useAppForeground } from '../hooks/useAppForeground';
import { degradedState, type DegradedState } from './permissionState';
import { stopReportingLocation } from './reportDriverLocation';
import { stopStopGeofencing } from './stopGeofences';

const NOTHING_LOST: DegradedState = {
  loss: 'none',
  shouldStopGeofencing: false,
  shouldStopReporting: false,
  message: null,
};

/**
 * Notice when location permission has gone away, and stop pretending (DRV-5).
 *
 * Permission is checked when the watcher starts and when geofences register,
 * and never again — so a driver who revokes it in Settings mid-shift leaves the
 * app holding state that claims a sensor it no longer has.
 *
 * **Re-checked on foreground**, because that is exactly when it can have
 * changed: a driver leaves the app, opens Settings, comes back. Polling would
 * cost battery to answer a question that can only change while the app is not
 * looking.
 *
 * It never re-requests. The "always" tier is asked for once, on a screen that
 * explains it first (`docs/BACKGROUND_LOCATION_CONSENT.md` §4.4); a cold prompt
 * after a deliberate refusal is how an app earns a permanent denial.
 */
export function useLocationDegradation(isOnline: boolean): DegradedState {
  const isForeground = useAppForeground();
  const [state, setState] = useState<DegradedState>(NOTHING_LOST);

  useEffect(() => {
    if (!isOnline) {
      setState(NOTHING_LOST);
      return;
    }
    let live = true;

    (async () => {
      const [foreground, background] = await Promise.all([
        Location.getForegroundPermissionsAsync(),
        Location.getBackgroundPermissionsAsync(),
      ]);
      if (!live) return;

      const next = degradedState({
        foregroundGranted: foreground.status === 'granted',
        backgroundGranted: background.granted,
      });
      setState(next);

      // Tearing down is the "clear state" half of the done-when. Leaving a
      // geofence registered that cannot fire, or a watcher running that cannot
      // read, is the app asserting a measurement it is not making.
      if (next.shouldStopGeofencing) await stopStopGeofencing();
      if (next.shouldStopReporting) stopReportingLocation();
    })().catch(() => {
      // Best-effort, the same convention as every other location call here: a
      // driver whose permission state we cannot read still gets a working app.
    });

    return () => {
      live = false;
    };
  }, [isOnline, isForeground]);

  return state;
}
