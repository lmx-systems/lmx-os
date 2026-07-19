import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import type { MainStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<MainStackParamList, 'ScanParcels'>;

// Screen 1k, "Scan parcels". No camera/barcode SDK wired up in v1 - "Scan
// next parcel" is a manual tap standing in for a real scanner (fast-follow:
// swap this button for an expo-camera barcode scanner without changing the
// backend contract, which only ever wanted a running count).
export function ScanParcelsScreen({ route, navigation }: Props) {
  const { stopId, parcelCount } = route.params;
  const [scannedCount, setScannedCount] = useState(0);
  const [busy, setBusy] = useState(false);

  async function handleScanNext() {
    if (scannedCount >= parcelCount) return;
    const next = scannedCount + 1;
    setScannedCount(next);
    await api.scanParcels(stopId, next);
  }

  async function handleDone() {
    setBusy(true);
    try {
      await api.completeStop(stopId, { method: 'photo' });
      navigation.replace('ActiveRoute');
    } finally {
      setBusy(false);
    }
  }

  const pct = parcelCount > 0 ? Math.round((scannedCount / parcelCount) * 100) : 0;

  return (
    <ScreenContainer>
      <Text style={styles.viewport}>▢ align barcode</Text>
      <Text style={[typography.body, styles.counter]}>
        Scanning… {scannedCount} / {parcelCount}
      </Text>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${pct}%` }]} />
      </View>

      <Button label="Scan next parcel" variant="outline" onPress={handleScanNext} disabled={scannedCount >= parcelCount} />
      <Button label="Done scanning" onPress={handleDone} loading={busy} disabled={scannedCount < parcelCount} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
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
  counter: { marginBottom: spacing.sm },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden', marginBottom: spacing.lg },
  progressFill: { height: '100%', backgroundColor: colors.accent },
});
