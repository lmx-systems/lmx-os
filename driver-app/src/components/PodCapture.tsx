import { useMemo } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import type { PodMethod } from '../api/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';
import { TextField } from './TextField';

const METHODS: PodMethod[] = ['photo', 'signature', 'pin'];

interface PodCaptureProps {
  method: PodMethod;
  onChangeMethod: (method: PodMethod) => void;
  captured: boolean;
  onCapture: () => void;
  pin: string;
  onChangePin: (pin: string) => void;
  leftAt: string;
  onChangeLeftAt: (leftAt: string) => void;
  onSubmit: () => void;
  busy: boolean;
}

// No real camera/signature-pad capture in v1 - "tap to capture" just
// records a placeholder value so the backend contract
// (Stop.pod_method/pod_photo_url/pod_signature_url/pod_pin) is fully
// exercised; swapping in expo-camera / a signature-pad library is a
// fast-follow that shouldn't need any API changes.
export function PodCapture({
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
        <Pressable style={styles.capturePlaceholder} onPress={onCapture}>
          <Text style={styles.captureText}>{captured ? `${method} captured ✓` : `tap to capture drop-off ${method}`}</Text>
        </Pressable>
      ) : (
        <TextField label="Delivery PIN" placeholder="1234" keyboardType="number-pad" value={pin} onChangeText={onChangePin} maxLength={6} />
      )}

      <TextField label="Left at" value={leftAt} onChangeText={onChangeLeftAt} />

      <Button label="Complete delivery" onPress={onSubmit} loading={busy} disabled={!canSubmit} />
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
