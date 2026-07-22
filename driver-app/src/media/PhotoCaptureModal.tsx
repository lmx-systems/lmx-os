import { useRef, useState } from 'react';
import { Image, Modal, StyleSheet, Text, View } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';

import { Button } from '../components/Button';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

interface PhotoCaptureModalProps {
  visible: boolean;
  onCaptured: (localUri: string) => void;
  onCancel: () => void;
}

// Real proof-of-delivery photo capture (docs/ROADMAP.md A3) - a driver
// sees exactly what they captured and can retake before it's used,
// rather than a single irreversible shutter tap.
export function PhotoCaptureModal({ visible, onCaptured, onCancel }: PhotoCaptureModalProps) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);
  const [previewUri, setPreviewUri] = useState<string | null>(null);

  async function handleShutter() {
    const photo = await cameraRef.current?.takePictureAsync({ quality: 0.7 });
    if (photo) setPreviewUri(photo.uri);
  }

  function handleClose() {
    setPreviewUri(null);
    onCancel();
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={handleClose}>
      <View style={styles.container}>
        {previewUri ? (
          <>
            <Image source={{ uri: previewUri }} style={styles.preview} resizeMode="cover" />
            <View style={styles.footer}>
              <Button label="Retake" variant="outline" onPress={() => setPreviewUri(null)} />
              <Button label="Use photo" onPress={() => onCaptured(previewUri)} />
            </View>
          </>
        ) : !permission?.granted ? (
          <View style={styles.permissionPrompt}>
            <Text style={styles.permissionText}>Camera access is needed to capture proof of delivery.</Text>
            <Button label="Allow camera access" onPress={requestPermission} />
            <Button label="Cancel" variant="outline" onPress={handleClose} />
          </View>
        ) : (
          <>
            <CameraView ref={cameraRef} style={styles.camera} facing="back" />
            <View style={styles.footer}>
              <Button label="Cancel" variant="outline" onPress={handleClose} />
              <Button label="Capture" onPress={handleShutter} />
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
    preview: { flex: 1 },
    footer: { flexDirection: 'row', gap: spacing.md, padding: spacing.lg },
    permissionPrompt: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl, gap: spacing.md },
    permissionText: { ...typography.body, color: colors.textPrimary, textAlign: 'center' },
  });
