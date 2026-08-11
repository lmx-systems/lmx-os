import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { uploadCapturedFile } from '../api/uploadCapturedFile';
import type { PodMethod, StopProofRequirement } from '../api/types';
import { PhotoCaptureModal } from '../media/PhotoCaptureModal';
import { SignaturePadModal } from '../media/SignaturePadModal';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';
import { TextField } from './TextField';

const METHODS: PodMethod[] = ['photo', 'signature', 'pin'];

interface PodCaptureProps {
  stopId: string;
  // What THIS order requires (docs/LMX_LINK_PLAN.md §1.2). Null falls back to the
  // one-photo baseline, which is what every order without a stated requirement
  // has always been.
  proof: StopProofRequirement | null;
  method: PodMethod;
  onChangeMethod: (method: PodMethod) => void;
  // Every photo captured so far, in order. A list rather than one URL because an
  // order can ask for several with named subjects - and the API rejects a
  // completion that is short, so the app has to be able to collect them.
  photoUrls: string[];
  signatureUrl: string | null;
  onCapturePhoto: (url: string) => void;
  onCaptureSignature: (url: string) => void;
  onRemoveLastPhoto: () => void;
  pin: string;
  onChangePin: (pin: string) => void;
  // Real PIN verification (docs/ROADMAP.md A4) - null until a submit
  // attempt comes back rejected (wrong PIN, none issued, or too many
  // attempts); StopDetailScreen clears it again as soon as the driver
  // edits the PIN, so a stale error never lingers past a new attempt.
  pinError: string | null;
  leftAt: string;
  onChangeLeftAt: (leftAt: string) => void;
  onSubmit: () => void;
  busy: boolean;
}

// Real camera/signature-pad capture (docs/ROADMAP.md A3,
// media/PhotoCaptureModal.tsx, media/SignaturePadModal.tsx) - captures a
// local file, uploads it (app/api/uploadCapturedFile.ts), then reports
// the real resulting URL up to StopDetailScreen, which is what actually
// gets submitted as CompleteStopBody.photo_url/signature_url.
export function PodCapture({
  stopId,
  proof,
  method,
  onChangeMethod,
  photoUrls,
  signatureUrl,
  onCapturePhoto,
  onCaptureSignature,
  onRemoveLastPhoto,
  pin,
  onChangePin,
  pinError,
  leftAt,
  onChangeLeftAt,
  onSubmit,
  busy,
}: PodCaptureProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  // Mirrors app/delivery/proof.py so the button is disabled for the same reasons
  // the server would refuse - a driver should not be able to tap Complete and get
  // a 422 telling them to take another photo.
  const photosRequired = Math.max(proof?.photo_count_required ?? 1, method === 'photo' ? 1 : 0);
  const signatureRequired = proof?.signature_required ?? false;
  const photosDone = photoUrls.length >= photosRequired;
  const identityDone =
    !signatureRequired || signatureUrl !== null || (method === 'pin' && pin.length >= 4);
  const methodCarriesEvidence =
    method === 'photo' ? photoUrls.length > 0 : method === 'signature' ? signatureUrl !== null : pin.length >= 4;
  const canSubmit = methodCarriesEvidence && photosDone && identityDone;

  const [modalOpen, setModalOpen] = useState(false);
  const [captureKind, setCaptureKind] = useState<'photo' | 'signature'>('photo');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handlePhotoCaptured(localUri: string) {
    setModalOpen(false);
    setUploading(true);
    setUploadError(null);
    try {
      const url = await uploadCapturedFile(stopId, 'photo', localUri, 'image/jpeg');
      onCapturePhoto(url);
    } catch {
      setUploadError("Couldn't upload photo - check your connection and try again.");
    } finally {
      setUploading(false);
    }
  }

  async function handleSignatureCaptured(dataUri: string) {
    setModalOpen(false);
    setUploading(true);
    setUploadError(null);
    try {
      const url = await uploadCapturedFile(stopId, 'signature', dataUri, 'image/png');
      onCaptureSignature(url);
    } catch {
      setUploadError("Couldn't upload signature - check your connection and try again.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <View>
      <Text style={styles.sectionLabel}>Method</Text>
      <View style={styles.segmentRow}>
        {METHODS.map((m) => (
          <Pressable key={m} onPress={() => onChangeMethod(m)} style={[styles.segment, method === m && styles.segmentActive]}>
            <Text style={[styles.segmentLabel, method === m && styles.segmentLabelActive]}>
              {m[0].toUpperCase() + m.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* What this particular order asks for, stated before the driver starts.
          Named subjects are the whole reason a count above one exists - "four
          photos" without saying of what produces four pictures of a doorstep. */}
      {proof && (photosRequired > 1 || signatureRequired) && (
        <View style={styles.requirement}>
          <Text style={styles.requirementTitle}>This delivery needs</Text>
          {photosRequired > 1 && (
            <Text style={styles.requirementLine}>
              {photosRequired} photos{proof.photo_subjects.length > 0 ? ` — ${proof.photo_subjects.join(', ')}` : ''}
            </Text>
          )}
          {signatureRequired && (
            <Text style={styles.requirementLine}>
              A signature from the person receiving it (a PIN also counts)
            </Text>
          )}
        </View>
      )}

      {method !== 'pin' ? (
        <>
          <Pressable
            style={styles.capturePlaceholder}
            onPress={() => {
              setCaptureKind(method === 'signature' ? 'signature' : 'photo');
              setModalOpen(true);
            }}
            disabled={uploading}
          >
            {uploading ? (
              <ActivityIndicator color={colors.textMuted} />
            ) : (
              <Text style={styles.captureText}>
                {method === 'signature'
                  ? signatureUrl
                    ? 'signature captured ✓'
                    : 'Tap to capture signature'
                  : photosRequired > 1
                    ? `Photo ${photoUrls.length} of ${photosRequired} — tap to add${
                        proof?.photo_subjects[photoUrls.length]
                          ? `: ${proof.photo_subjects[photoUrls.length]}`
                          : ''
                      }`
                    : photoUrls.length > 0
                      ? 'photo captured ✓'
                      : 'Tap to capture photo'}
              </Text>
            )}
          </Pressable>

          {/* A retake, not an undo of the whole set - a driver whose fourth photo
              came out blurred should not lose the first three. */}
          {method === 'photo' && photoUrls.length > 0 && (
            <Pressable onPress={onRemoveLastPhoto} style={styles.retake}>
              <Text style={styles.retakeLabel}>Retake the last photo</Text>
            </Pressable>
          )}

          {/* A signature can be required on top of photos, so it needs its own way
              in rather than only being reachable by switching method. */}
          {method === 'photo' && signatureRequired && (
            <Pressable
              onPress={() => {
                setCaptureKind('signature');
                setModalOpen(true);
              }}
              style={styles.retake}
              disabled={uploading}
            >
              <Text style={styles.retakeLabel}>
                {signatureUrl ? 'Signature captured ✓ — redo' : 'Add the signature'}
              </Text>
            </Pressable>
          )}

          {uploadError && <Text style={styles.errorText}>{uploadError}</Text>}
        </>
      ) : (
        <>
          <TextField label="Delivery PIN" placeholder="1234" keyboardType="number-pad" value={pin} onChangeText={onChangePin} maxLength={6} />
          {pinError && <Text style={styles.errorText}>{pinError}</Text>}
        </>
      )}

      <TextField label="Left at" value={leftAt} onChangeText={onChangeLeftAt} />

      <Button label="Complete delivery" onPress={onSubmit} loading={busy} disabled={!canSubmit} />

      {/* Keyed off what the driver asked to capture rather than the method, since a
          photo-method delivery can still need a signature on top. */}
      {captureKind === 'photo' && (
        <PhotoCaptureModal visible={modalOpen} onCaptured={handlePhotoCaptured} onCancel={() => setModalOpen(false)} />
      )}
      {captureKind === 'signature' && (
        <SignaturePadModal visible={modalOpen} onCaptured={handleSignatureCaptured} onCancel={() => setModalOpen(false)} />
      )}
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    sectionLabel: { ...typography.label, color: colors.textPrimary, marginBottom: spacing.xs },
    segmentRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.lg },
    segment: { flex: 1, paddingVertical: spacing.sm + 2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, alignItems: 'center' },
    segmentActive: { backgroundColor: colors.primary, borderColor: colors.primary },
    segmentLabel: { color: colors.textPrimary, fontWeight: '600' },
    segmentLabelActive: { color: colors.primaryText },
    captureText: { ...typography.small, color: colors.textMuted },
    requirement: {
      backgroundColor: colors.surfaceAlt,
      borderRadius: radius.md,
      padding: spacing.md,
      marginBottom: spacing.md,
    },
    requirementTitle: { ...typography.small, color: colors.textSecondary, fontWeight: '700' },
    requirementLine: { ...typography.small, color: colors.textSecondary },
    retake: { paddingVertical: spacing.sm, alignItems: 'center' },
    retakeLabel: { ...typography.small, color: colors.accent, fontWeight: '600' },
    errorText: { ...typography.small, color: colors.danger, marginTop: -spacing.md, marginBottom: spacing.md },
    capturePlaceholder: {
      height: 140,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderStyle: 'dashed',
      borderColor: colors.borderStrong,
      alignItems: 'center',
      justifyContent: 'center',
      marginBottom: spacing.lg,
    },
  });
