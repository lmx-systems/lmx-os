import type { PodMethod, StopProofRequirement } from '../api/types';

/**
 * Whether what the driver has captured would satisfy the server, so Complete is
 * enabled for exactly the reasons app/delivery/proof.py accepts:
 *
 *   1. The chosen method carries its own evidence: a photo, a signature, or a
 *      PIN of at least four digits.
 *   2. A photo count is a floor for every method only when it is above one. At
 *      one, the default, it means only that the photo method needs its photo.
 *   3. A signature requirement is met by a signature, or by a PIN.
 *
 * The app used to read a count of one as a floor for every method, so every
 * signature or PIN drop-off demanded a photo the server never asked for.
 */
export function proofReadiness(input: {
  proof: StopProofRequirement | null | undefined;
  method: PodMethod;
  photoCount: number;
  hasSignature: boolean;
  pinLength: number;
}): { photosRequired: number; signatureRequired: boolean; canSubmit: boolean } {
  const { proof, method, photoCount, hasSignature, pinLength } = input;
  const countRequired = proof?.photo_count_required ?? 1;
  const photosRequired = countRequired > 1 ? countRequired : method === 'photo' ? 1 : 0;
  const signatureRequired = proof?.signature_required ?? false;
  const pinEntered = pinLength >= 4;
  const methodCarriesEvidence =
    method === 'photo' ? photoCount > 0 : method === 'signature' ? hasSignature : pinEntered;
  const identityDone = !signatureRequired || hasSignature || (method === 'pin' && pinEntered);
  return {
    photosRequired,
    signatureRequired,
    canSubmit: methodCarriesEvidence && photoCount >= photosRequired && identityDone,
  };
}
