import type { Stop } from '../api/types';
import { isBeforeArrival } from './stopStatus';

/**
 * What, if anything, the driver can do about cores at this stop
 * (docs/ROADMAP.md W1).
 *
 * Extracted from the panel for the reason every other decision here is: the
 * screens are untested and this is the part where being wrong costs something.
 * A button offered at the wrong moment queues an action the server refuses, and
 * `outboxManager`'s `isPermanentFailure` classifies a 4xx as permanent — so a
 * doomed action is not retried, it is *discarded*, and the driver is told their
 * collection failed.
 */

export type ReturnsAction =
  | { kind: 'none' }
  /** At a dropoff: cores to take away, and whether the driver may yet act. */
  | { kind: 'collect'; manifests: string[]; enabled: boolean }
  /** At a pickup: cores to hand back to this shop. */
  | { kind: 'drop'; manifests: string[] };

export function returnsActionForStop(stop: Stop): ReturnsAction {
  const returns = stop.returns;
  if (!returns) return { kind: 'none' };

  if (stop.stop_type === 'pickup') {
    // `return-to-shop` does not require arrival on the server, and requiring it
    // here would be restating a condition another module owns — the mistake
    // behind both bugs in `applyOptimistic.test.ts`.
    return returns.to_drop_manifests.length > 0
      ? { kind: 'drop', manifests: returns.to_drop_manifests }
      : { kind: 'none' };
  }

  // A dropoff. The panel still renders with nothing expected, because the
  // driver may be handed a core nobody predicted — that is what the ad-hoc
  // manifest is for, and it is the common case at a counter.
  return {
    kind: 'collect',
    manifests: returns.expected_manifests,
    // `collect_return` calls `_assert_arrived`. Offering the button before then
    // queues a 409, which the outbox throws away as a permanent failure.
    enabled: !isBeforeArrival(stop),
  };
}
