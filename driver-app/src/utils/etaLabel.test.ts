import type { Stop } from '../api/types';
import { clockTime, etaLabel } from './etaLabel';

/**
 * The ETA on a driver's stop list. Times are built from local components so the
 * tests read the same in any time zone the CI runner happens to be in.
 */

function at(hours: number, minutes: number): string {
  return new Date(2026, 9, 6, hours, minutes).toISOString();
}

function stop(overrides: Partial<Stop> = {}): Pick<Stop, 'status' | 'eta'> {
  return { status: 'pending', eta: at(14, 5), ...overrides } as Pick<Stop, 'status' | 'eta'>;
}

describe('the ETA on a stop', () => {
  it('shows a clock time for a stop still ahead', () => {
    expect(etaLabel(stop())).toBe('ETA 2:05 pm');
  });

  it('shows it while the driver is on the way there', () => {
    expect(etaLabel(stop({ status: 'en_route' }))).toBe('ETA 2:05 pm');
  });

  it('drops it once the driver has arrived', () => {
    expect(etaLabel(stop({ status: 'arrived' }))).toBeNull();
  });

  it('drops it for a stop that is finished either way', () => {
    expect(etaLabel(stop({ status: 'completed' }))).toBeNull();
    expect(etaLabel(stop({ status: 'failed' }))).toBeNull();
  });

  it('shows nothing when the backend sent nothing', () => {
    expect(etaLabel(stop({ eta: null }))).toBeNull();
  });

  it('shows nothing for a value it cannot read', () => {
    expect(etaLabel(stop({ eta: 'not a time' }))).toBeNull();
  });
});

describe('the clock time', () => {
  it('writes noon and midnight as twelve', () => {
    expect(clockTime(new Date(2026, 9, 6, 12, 0))).toBe('12:00 pm');
    expect(clockTime(new Date(2026, 9, 6, 0, 30))).toBe('12:30 am');
  });

  it('pads the minutes', () => {
    expect(clockTime(new Date(2026, 9, 6, 9, 7))).toBe('9:07 am');
  });
});
