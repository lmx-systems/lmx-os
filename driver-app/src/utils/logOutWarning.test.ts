import { lastSeen } from './lastSeen';
import { logOutWarning } from './logOutWarning';

/**
 * The two pure rules behind the devices screen and the Log out tap (S1).
 *
 * Neither is tested through a screen, deliberately: mounting one would test
 * React and leave the rules themselves incidental. What matters is that
 * `logOutWarning` is silent exactly when there is nothing to lose, and that
 * `lastSeen` refuses to invent a time.
 */

describe('logOutWarning', () => {
  it('says nothing when the queue is empty', () => {
    // The point of the rule. Warning on every log out teaches drivers to
    // dismiss the dialog, and it stops working on the day it matters.
    expect(logOutWarning(0)).toBeNull();
  });

  it('says nothing for a count that cannot happen', () => {
    expect(logOutWarning(-1)).toBeNull();
  });

  it('warns about a single queued update in the singular', () => {
    const warning = logOutWarning(1);
    expect(warning).toContain('One stop update has not reached us yet');
    expect(warning).toContain('leaves it stuck on this phone');
  });

  it('warns about several in the plural, with the count', () => {
    const warning = logOutWarning(7);
    expect(warning).toContain('7 stop updates have not reached us yet');
    expect(warning).toContain('leaves them stuck on this phone');
  });

  it('never claims the work is sent or discarded', () => {
    // It is neither: the token goes, every flush 401s, and refreshOnce has no
    // session left to refresh with. The wording has to leave a driver expecting
    // to find the work still here when they sign back in, because they will.
    const warning = logOutWarning(3) ?? '';
    expect(warning).toContain('until you sign back in');
    expect(warning).not.toMatch(/discard|delete|lost|sent/i);
  });
});

describe('lastSeen', () => {
  const now = Date.parse('2026-09-25T12:00:00Z');

  it('reads unknown rather than a confident number when the date is broken', () => {
    // The failure this guards: NaN falls through every comparison to the last
    // branch, so a broken timestamp would render as a plausible "N days ago"
    // on the one screen where picking the wrong session signs out the wrong
    // phone.
    expect(lastSeen('not a date', now)).toBe('unknown');
    expect(lastSeen('', now)).toBe('unknown');
  });

  it('collapses the last minute to "just now"', () => {
    expect(lastSeen('2026-09-25T11:59:30Z', now)).toBe('just now');
  });

  it('reads a clock skewed into the future as "just now", not a negative age', () => {
    expect(lastSeen('2026-09-25T12:05:00Z', now)).toBe('just now');
  });

  it('steps through minutes, hours and days', () => {
    expect(lastSeen('2026-09-25T11:20:00Z', now)).toBe('40 min ago');
    expect(lastSeen('2026-09-25T09:00:00Z', now)).toBe('3 hr ago');
    expect(lastSeen('2026-09-24T12:00:00Z', now)).toBe('yesterday');
    expect(lastSeen('2026-09-21T12:00:00Z', now)).toBe('4 days ago');
  });
});
