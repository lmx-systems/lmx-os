import { degradedState } from './permissionState';

/**
 * DRV-5: *"clear state when permission is denied."*
 *
 * Permission is checked when the watcher starts and when geofences register,
 * and never again. A driver who grants it at 6am and revokes it in Settings at
 * 10am leaves the app believing it is still tracking: registered regions stop
 * firing, the watcher stops emitting, and nothing notices.
 *
 * The cost is silent and specific — `DRV-1`'s second-precision dwell quietly
 * degrades to tap-grade for that shift, which is the measurement the whole of
 * Phase 1 exists to produce.
 *
 * These test the judgement, which needs no device. The half this cannot cover
 * is the row's other clause, "under 4% battery per 8-hour shift with background
 * on" — that is measured on a real phone over a real shift, and no test here
 * should pretend otherwise.
 */
describe('what is still being measured', () => {
  it('loses nothing when both tiers are granted', () => {
    const state = degradedState({ foregroundGranted: true, backgroundGranted: true });

    expect(state.loss).toBe('none');
    expect(state.message).toBeNull();
    expect(state.shouldStopGeofencing).toBe(false);
    expect(state.shouldStopReporting).toBe(false);
  });

  it('loses automatic arrivals when only background is revoked', () => {
    const state = degradedState({ foregroundGranted: true, backgroundGranted: false });

    expect(state.loss).toBe('automatic_arrivals');
    // Position still reports while the app is open - stopping that too would
    // take the driver off the ops map for no reason.
    expect(state.shouldStopReporting).toBe(false);
  });

  it('tears down geofences the moment they cannot fire', () => {
    // Leaving them registered is the app holding state that claims a sensor it
    // does not have - the exact thing this row calls "clear state".
    const state = degradedState({ foregroundGranted: true, backgroundGranted: false });

    expect(state.shouldStopGeofencing).toBe(true);
  });

  it('loses everything when foreground is revoked', () => {
    const state = degradedState({ foregroundGranted: false, backgroundGranted: false });

    expect(state.loss).toBe('all');
    expect(state.shouldStopGeofencing).toBe(true);
    expect(state.shouldStopReporting).toBe(true);
  });

  it('treats foreground-denied as total even if background still reads granted', () => {
    // Background implies foreground on both platforms, so this combination
    // should not occur - but a stale cached value saying otherwise must not
    // leave the app reporting a position it cannot read.
    const state = degradedState({ foregroundGranted: false, backgroundGranted: true });

    expect(state.loss).toBe('all');
    expect(state.shouldStopReporting).toBe(true);
  });

  describe('what the driver is told', () => {
    it('says what is lost and what to do instead', () => {
      const state = degradedState({ foregroundGranted: true, backgroundGranted: false });

      expect(state.message).toContain('will not be recorded');
      expect(state.message).toContain('tap Arrive and Complete');
    });

    it('does not tell a driver to turn it back on', () => {
      // A driver who turned location off usually meant to. The useful sentence
      // is what they now have to do instead, not that they should undo it -
      // and nagging is how an app earns a permanent denial.
      for (const permissions of [
        { foregroundGranted: true, backgroundGranted: false },
        { foregroundGranted: false, backgroundGranted: false },
      ]) {
        const message = degradedState(permissions).message ?? '';
        expect(message.toLowerCase()).not.toContain('enable');
        expect(message.toLowerCase()).not.toContain('turn on');
        expect(message.toLowerCase()).not.toContain('settings');
      }
    });

    it('says nothing at all when nothing is wrong', () => {
      expect(degradedState({ foregroundGranted: true, backgroundGranted: true }).message).toBeNull();
    });
  });
});
