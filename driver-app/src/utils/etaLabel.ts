import type { Stop } from '../api/types';
import { isBeforeArrival } from './stopStatus';

/**
 * "ETA 2:45 pm" for a stop the driver hasn't reached yet, or null.
 *
 * The route payload has carried an `eta` for every stop since August, and no
 * screen showed it. It is the same number the recipient's tracking page and the
 * client portal now show for that drop (app/delivery/eta.py), so a driver asked
 * "when will you be here" reads the answer the customer was given.
 *
 * Null once the driver has arrived - the question has been answered, and the
 * backend stops moving the figure then - and when the backend sent none (a stop
 * with no address ends the walk for everything after it).
 *
 * A clock time rather than "in 12 min": the figure only moves when the app
 * refetches the route (once a minute while a route is active), so a countdown
 * would tick past zero on a stale number. Built from getHours/getMinutes in the
 * phone's own zone rather than toLocaleTimeString, whose output differs between
 * Hermes and JSC.
 *
 * Nothing at all once the time is more than a couple of minutes gone. The server
 * never forecasts into the past, so a passed time means the screen is behind -
 * offline, or between refetches - and a time already gone reads as a promise
 * broken, not as "late".
 */
export const STALE_AFTER_MS = 2 * 60_000;

export function etaLabel(
  stop: Pick<Stop, 'status' | 'eta'>,
  now: Date = new Date(),
): string | null {
  if (!stop.eta || !isBeforeArrival(stop)) return null;
  const at = new Date(stop.eta);
  if (Number.isNaN(at.getTime())) return null;
  if (now.getTime() - at.getTime() > STALE_AFTER_MS) return null;
  return `ETA ${clockTime(at)}`;
}

export function clockTime(at: Date): string {
  const hours = at.getHours();
  const minutes = at.getMinutes().toString().padStart(2, '0');
  const meridiem = hours < 12 ? 'am' : 'pm';
  const twelve = hours % 12 === 0 ? 12 : hours % 12;
  return `${twelve}:${minutes} ${meridiem}`;
}
