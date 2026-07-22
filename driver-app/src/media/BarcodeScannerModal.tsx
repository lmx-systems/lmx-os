import { useState } from 'react';
import { Modal, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';

import { Button } from '../components/Button';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

interface BarcodeScannerModalProps {
  visible: boolean;
  onScanned: (data: string) => void;
  onCancel: () => void;
}

// Real barcode scanning for "scan next parcel" (docs/ROADMAP.md A2) -
// replaces the manual tap-to-increment ParcelScanPanel used to be. A
// driver can still fall back to a manual tap (kept in ParcelScanPanel)
// for a damaged/unreadable barcode - a scanner that can only ever
// succeed is a worse tool than one with an escape hatch.
export function BarcodeScannerModal({ visible, onScanned, onCancel }: BarcodeScannerModalProps) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const [permission, requestPermission] = useCameraPermissions();
  // onBarcodeScanned fires repeatedly for the same code while it stays in
  // frame - without this, one physical scan would enqueue several
  // scanned_count increments before the modal has a chance to close.
  const [locked, setLocked] = useState(false);

  function handleScanned({ data }: { data: string }) {
    if (locked) return;
    setLocked(true);
    onScanned(data);
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onCancel} onShow={() => setLocked(false)}>
      <View style={styles.container}>
        {!permission?.granted ? (
          <View style={styles.permissionPrompt}>
            <Text style={styles.permissionText}>Camera access is needed to scan parcel barcodes.</Text>
            <Button label="Allow camera access" onPress={requestPermission} />
            <Button label="Cancel" variant="outline" onPress={onCancel} />
          </View>
        ) : (
          <>
            <CameraView
              style={styles.camera}
              facing="back"
              onBarcodeScanned={handleScanned}
              barcodeScannerSettings={{
                barcodeTypes: ['ean13', 'ean8', 'code128', 'code39', 'qr', 'upc_a', 'upc_e'],
              }}
            />
            <View style={styles.overlay} pointerEvents="none">
              <View style={styles.frame} />
              <Text style={styles.hint}>Align the barcode inside the frame</Text>
            </View>
            <View style={styles.footer}>
              <Button label="Cancel" variant="outline" onPress={onCancel} />
            </View>
          </>
        )}
      </View>
    </Modal>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: '#000' },
    camera: { flex: 1 },
    overlay: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
    frame: { width: 260, height: 160, borderWidth: 3, borderColor: colors.accent, borderRadius: radius.lg },
    hint: {
      ...typography.body,
      color: '#ffffff',
      marginTop: spacing.lg,
      textAlign: 'center',
      paddingHorizontal: spacing.lg,
    },
    footer: { padding: spacing.lg },
    permissionPrompt: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.md },
    permissionText: { ...typography.body, color: colors.textPrimary, textAlign: 'center' },
  });
