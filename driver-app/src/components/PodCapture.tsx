import { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { uploadCapturedFile } from '../api/uploadCapturedFile';
import type { PodMethod } from '../api/types';
import { PhotoCaptureModal } from '../media/PhotoCaptureModal';
import { SignaturePadModal } from '../media/SignaturePadModal';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';
import { TextField } from './TextField';

const METHODS: PodMethod[] = ['photo', 'signature', 'pin'];

interface PodCaptureProps {
  stopId: string;
  method: PodMethod;
  onChangeMethod: (method: PodMethod) => void;
  captured: boolean;
  onCapture: (url: string) => void;
  pin: string;
  onChangePin: (pin: string) => void;
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
  method,
  onChangeMethod,
  captured,
  onCapture,
  pin,
  onChangePin,
  leftAt,
  onChangeLeftAt,
  onSubmit,
  busy,
}: PodCaptureProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const canSubmit = method === 'pin' ? pin.length >= 4 : captured;
  const [modalOpen, setModalOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function handlePhotoCaptured(localUri: string) {
    setModalOpen(false);
    setUploading(true);
    setUploadError(null);
    try {
      const url = await uploadCapturedFile(stopId, 'photo', localUri, 'image/jpeg');
      onCapture(url);
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
      onCapture(url);
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

      {method !== 'pin' ? (
        <>
          <Pressable style={styles.capturePlaceholder} onPress={() => setModalOpen(true)} disabled={uploading}>
            {uploading ? (
              <ActivityIndicator color={colors.textMuted} />
            ) : (
              <Text style={styles.captureText}>{captured ? `${method} captured ✓` : `Tap to capture ${method}`}</Text>
            )}
          </Pressable>
          {uploadError && <Text style={styles.errorText}>{uploadError}</Text>}
        </>
      ) : (
        <TextField label="Delivery PIN" placeholder="1234" keyboardType="number-pad" value={pin} onChangeText={onChangePin} maxLength={6} />
      )}

      <TextField label="Left at" value={leftAt} onChangeText={onChangeLeftAt} />

      <Button label="Complete delivery" onPress={onSubmit} loading={busy} disabled={!canSubmit} />

      {method === 'photo' && (
        <PhotoCaptureModal visible={modalOpen} onCaptured={handlePhotoCaptured} onCancel={() => setModalOpen(false)} />
      )}
      {method === 'signature' && (
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
