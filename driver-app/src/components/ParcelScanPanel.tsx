import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';

interface ParcelScanPanelProps {
  scannedCount: number;
  total: number;
  onScanNext: () => void;
}

// No camera/barcode SDK wired up in v1 - "Scan next parcel" is a manual tap
// standing in for a real scanner (fast-follow: swap this button for an
// expo-camera barcode scanner without changing the backend contract, which
// only ever wanted a running count).
export function ParcelScanPanel({ scannedCount, total, onScanNext }: ParcelScanPanelProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const pct = total > 0 ? Math.round((scannedCount / total) * 100) : 0;

  return (
    <View>
      <Text style={styles.viewport}>▢ align barcode</Text>
      <Text style={styles.counter}>
        Scanning… {scannedCount} / {total}
      </Text>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${pct}%` }]} />
      </View>
      <Button label="Scan next parcel" variant="outline" onPress={onScanNext} disabled={scannedCount >= total} />
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    viewport: {
      height: 220,
      textAlign: 'center',
      textAlignVertical: 'center',
      backgroundColor: colors.textPrimary,
      color: '#ffffff',
      borderRadius: 12,
      marginBottom: spacing.lg,
      overflow: 'hidden',
    },
    counter: { ...typography.body, color: colors.textPrimary, marginBottom: spacing.sm },
    progressTrack: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden', marginBottom: spacing.lg },
    progressFill: { height: '100%', backgroundColor: colors.accent },
  });
