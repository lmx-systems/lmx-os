import type { StopProofRequirement } from '../api/types';
import { proofReadiness } from './proofReadiness';

/**
 * The drop-off button, against app/delivery/proof.py's three rules. A wrong
 * answer here doesn't look like a bug - it looks like a delivery a driver can't
 * finish, which is what a signature drop-off was until this was pulled out of
 * PodCapture and pinned.
 */

const DEFAULT: StopProofRequirement = { photo_count_required: 1, photo_subjects: [], signature_required: false };

function ready(overrides: Partial<Parameters<typeof proofReadiness>[0]> = {}) {
  return proofReadiness({ proof: DEFAULT, method: 'photo', photoCount: 0, hasSignature: false, pinLength: 0, ...overrides })
    .canSubmit;
}

describe('the default requirement', () => {
  it('lets a signature drop-off complete with no photo', () => {
    expect(ready({ method: 'signature', hasSignature: true })).toBe(true);
  });

  it('lets a PIN drop-off complete with no photo', () => {
    expect(ready({ method: 'pin', pinLength: 4 })).toBe(true);
  });

  it('still needs the photo when the method is photo', () => {
    expect(ready({ method: 'photo' })).toBe(false);
    expect(ready({ method: 'photo', photoCount: 1 })).toBe(true);
  });

  it('needs the chosen method to carry its evidence', () => {
    expect(ready({ method: 'signature', photoCount: 1 })).toBe(false);
    expect(ready({ method: 'pin', pinLength: 3 })).toBe(false);
  });

  it('treats a missing requirement as the default', () => {
    expect(ready({ proof: null, method: 'signature', hasSignature: true })).toBe(true);
    expect(ready({ proof: undefined, method: 'photo' })).toBe(false);
  });
});

describe('a photo count above one', () => {
  const three: StopProofRequirement = { ...DEFAULT, photo_count_required: 3, photo_subjects: ['box', 'label', 'door'] };

  it('is a floor for every method', () => {
    expect(ready({ proof: three, method: 'signature', hasSignature: true, photoCount: 2 })).toBe(false);
    expect(ready({ proof: three, method: 'signature', hasSignature: true, photoCount: 3 })).toBe(true);
    expect(ready({ proof: three, method: 'pin', pinLength: 4, photoCount: 3 })).toBe(true);
  });

  it('is reported for the photo counter', () => {
    expect(proofReadiness({ proof: three, method: 'pin', photoCount: 0, hasSignature: false, pinLength: 0 }).photosRequired).toBe(3);
  });
});

describe('a signature requirement', () => {
  const signed: StopProofRequirement = { ...DEFAULT, signature_required: true };

  it('is not met by a photo alone', () => {
    expect(ready({ proof: signed, method: 'photo', photoCount: 1 })).toBe(false);
    expect(ready({ proof: signed, method: 'photo', photoCount: 1, hasSignature: true })).toBe(true);
  });

  it('is met by a PIN', () => {
    expect(ready({ proof: signed, method: 'pin', pinLength: 4 })).toBe(true);
  });
});
