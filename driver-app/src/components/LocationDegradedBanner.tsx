import { MapPinOff } from 'lucide-react-native';

import type { DegradedState } from '../location/permissionState';
import { Banner } from './ui/kit';

/**
 * What stopped being measured when location permission went away (DRV-5).
 *
 * *"Clear state when permission is denied."* Without this a driver who revokes
 * location in Settings mid-shift sees nothing change: the app keeps its
 * geofences registered, the toggle still says online, and stop arrivals simply
 * stop being recorded. `DRV-1`'s second-precision dwell degrades to tap-grade
 * for the shift and nobody finds out until the data is analysed.
 *
 * **Warning, not danger.** Nothing is broken and the driver has done nothing
 * wrong — they can still complete every delivery by tapping. Styling this like
 * the compliance banner, which refuses to let them work, would cry wolf.
 *
 * The copy says what is lost and what to do instead, and deliberately does not
 * ask them to turn it back on. A driver who turned it off usually meant to, and
 * nagging is how an app earns a permanent denial.
 */
export function LocationDegradedBanner({ state }: { state: DegradedState }) {
  if (state.message === null) {
    return null;
  }

  return (
    <Banner
      icon={MapPinOff}
      tone="warning"
      title={state.loss === 'all' ? 'Location is off' : 'Automatic arrivals are off'}
      body={state.message}
    />
  );
}
