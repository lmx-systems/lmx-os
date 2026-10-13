import type { ExceptionItem } from './types'

/**
 * Which of the console's two lists an exception belongs on (decision D-SEAT).
 *
 * **Going ahead**: the playbook has made its call and will act at `due_at`
 * unless a person chooses first - nobody has to do anything. **Needs you**:
 * everything else - no rule covers it, the rule handed it to a person, or the
 * rule only recommends. Home's headline counts the second list, because that
 * is the number of things a person must actually do.
 */
export function isGoingAhead(item: ExceptionItem): boolean {
  const call = item.playbook
  return Boolean(call && call.outcome === 'open' && call.mode !== 'recommend' && call.due_at !== null)
}
