jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

import {
  EMPTY_ITEM,
  EMPTY_JOB,
  itemFacts,
  jobIntake,
  clearDrafts,
  loadDrafts,
  newDraft,
  recordBody,
  removeDraft,
  saveDraft,
  timeToday,
  uuidV4,
} from './offPlatformRecord';

const NOW = new Date(2026, 9, 12, 15, 0);

describe('logging an off-platform delivery (DE-7)', () => {
  it('names the record with a v4 UUID the server accepts', () => {
    expect(uuidV4()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const draft = newDraft('d1', NOW);
    expect(draft.recordId).not.toEqual(draft.pickupStopId);
  });

  it('reads a time of day as today', () => {
    expect(timeToday('9:05', NOW)).toEqual(new Date(2026, 9, 12, 9, 5).toISOString());
    expect(timeToday('25:00', NOW)).toBeNull();
    expect(timeToday('noon', NOW)).toBeNull();
  });

  it('turns the typed job into the intake the server takes', () => {
    const job = jobIntake(
      {
        platform: 'curri', ref: ' C-1 ', pickupAddress: '1 Supply Way', dropoffAddress: '2 Shop Ln',
        pickupFrom: '13:00', pickupTo: '13:30', deliverBy: '15:30', pay: '$42.50',
      },
      NOW,
    );
    expect(job).toEqual({
      source_platform: 'curri',
      platform_job_ref: 'C-1',
      pickup_address: '1 Supply Way',
      dropoff_address: '2 Shop Ln',
      pickup_window_open: new Date(2026, 9, 12, 13, 0).toISOString(),
      pickup_window_close: new Date(2026, 9, 12, 13, 30).toISOString(),
      dropoff_window_close: new Date(2026, 9, 12, 15, 30).toISOString(),
      pay_cents: 4250,
      intake_source: 'manual',
    });
  });

  it('says what is missing rather than sending a job the server would refuse', () => {
    expect(jobIntake(EMPTY_JOB, NOW)).toBe('Which platform was it?');
    expect(
      jobIntake({ ...EMPTY_JOB, platform: 'roadie', ref: 'R', pickupAddress: 'a', dropoffAddress: 'b', pickupFrom: '14:00', pickupTo: '13:00', pay: '5' }, NOW),
    ).toBe('The pickup window closes before it opens');
  });

  it('leaves a blank item field unknown', () => {
    expect(itemFacts({ ...EMPTY_ITEM, weightKg: '2,5', hazmat: false })).toEqual({
      item_weight_kg: 2.5,
      item_hazmat_or_liquid: false,
    });
    expect(itemFacts({ ...EMPTY_ITEM, weightKg: 'heavy' })).toBe('A weight in kilograms, 0 to 2,000.');
  });

  it('names a stored job by its id, and a typed one by itself', () => {
    const draft = newDraft('d1', NOW);
    const typed = jobIntake({ ...EMPTY_JOB, platform: 'dispatch', ref: 'D', pickupAddress: 'a', dropoffAddress: 'b', pickupFrom: '1:00', pickupTo: '2:00', pay: '9' }, NOW);
    if (typeof typed === 'string') throw new Error(typed);

    expect(recordBody(draft, typed, {})).toEqual({ pickup_stop_id: draft.pickupStopId, job: typed });
    expect(recordBody({ ...draft, gigJobId: 'g1' }, typed, { dropoff: { at: 'x' } })).toEqual({
      pickup_stop_id: draft.pickupStopId,
      gig_job_id: 'g1',
      dropoff: { at: 'x' },
    });
  });

  it('keeps a draft until the door is logged', async () => {
    const draft = newDraft('d1', NOW);
    await saveDraft(draft);
    await saveDraft({ ...draft, jobLabel: 'C-1' });
    expect(await loadDrafts('d1')).toEqual([{ ...draft, jobLabel: 'C-1' }]);

    await removeDraft(draft.recordId);
    expect(await loadDrafts('d1')).toEqual([]);
  });

  it("shows a driver only their own drafts, and none after signing out", async () => {
    const mine = newDraft('d1', NOW);
    const theirs = newDraft('d2', NOW);
    await saveDraft(mine);
    await saveDraft(theirs);
    expect(await loadDrafts('d1')).toEqual([mine]);
    expect(await loadDrafts('d2')).toEqual([theirs]);

    await clearDrafts();
    expect(await loadDrafts('d1')).toEqual([]);
    expect(await loadDrafts('d2')).toEqual([]);
  });
});
