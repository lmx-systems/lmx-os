import { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { BarcodeScannerModal } from '../media/BarcodeScannerModal';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';

interface ParcelScanPanelProps {
  scannedCount: number;
  total: number;
  onScanNext: () => void;
}

// Real barcode scanning (docs/ROADMAP.md A2, media/BarcodeScannerModal.tsx)
// for "Scan next parcel" - the backend contract never cared how a scan
// happened, only that scanned_count went up, so this swap needed no API
// change. A manual fallback stays for a damaged/unreadable barcode - a
// driver blocked by one bad label with no way past it is worse than the
// count being a running tally either way.
export function ParcelScanPanel({ scannedCount, total, onScanNext }: ParcelScanPanelProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const pct = total > 0 ? Math.round((scannedCount / total) * 100) : 0;
  const [scannerOpen, setScannerOpen] = useState(false);
  const allScanned = scannedCount >= total;

  function handleScanned() {
    setScannerOpen(false);
    onScanNext();
  }

  return (
    <View>
      <Text style={styles.counter}>
        Scanned {scannedCount} / {total}
      </Text>
      <View style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${pct}%` }]} />
      </View>
      <Button label="Scan barcode" onPress={() => setScannerOpen(true)} disabled={allScanned} />
      <View style={styles.manualGap} />
      <Button label="Can't scan? Confirm manually" variant="outline" onPress={onScanNext} disabled={allScanned} />
      <BarcodeScannerModal
        visible={scannerOpen}
        onScanned={handleScanned}
        onCancel={() => setScannerOpen(false)}
      />
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    counter: { ...typography.body, color: colors.textPrimary, marginBottom: spacing.sm },
    progressTrack: { height: 6, borderRadius: 3, backgroundColor: colors.border, overflow: 'hidden', marginBottom: spacing.lg },
    progressFill: { height: '100%', backgroundColor: colors.accent },
    manualGap: { height: spacing.sm },
  });
