import { useRef } from 'react';
import { Modal, StyleSheet, View } from 'react-native';
import SignatureScreen, { type SignatureViewRef } from 'react-native-signature-canvas';

import { Button } from '../components/Button';
import { spacing, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

interface SignaturePadModalProps {
  visible: boolean;
  onCaptured: (dataUri: string) => void;
  onCancel: () => void;
}

// Real signature capture for proof of delivery (docs/ROADMAP.md A3) -
// react-native-signature-canvas renders an actual drawable canvas (via an
// internal WebView, no separate native module) instead of a placeholder
// "tap to capture" box. onOK receives a base64 data: URI PNG, which
// uploadCapturedFile() (src/api/uploadCapturedFile.ts) treats exactly
// like a captured photo's file:// URI - fetch() reads either.
export function SignaturePadModal({ visible, onCaptured, onCancel }: SignaturePadModalProps) {
  const colors = useThemeColors();
  const styles = makeStyles(colors);
  const ref = useRef<SignatureViewRef>(null);

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onCancel}>
      <View style={styles.container}>
        <SignatureScreen
          ref={ref}
          onOK={onCaptured}
          descriptionText="Sign above"
          webStyle={signaturePadWebStyle(colors)}
          autoClear={false}
        />
        <View style={styles.footer}>
          <Button label="Cancel" variant="outline" onPress={onCancel} />
          <Button label="Clear" variant="outline" onPress={() => ref.current?.clearSignature()} />
          <Button label="Use signature" onPress={() => ref.current?.readSignature()} />
        </View>
      </View>
    </Modal>
  );
}

// The library renders its canvas inside an internal WebView styled by raw
// CSS, not React Native style objects - this is the one legitimate spot
// in this app that reaches for a plain string instead of the usual
// makeStyles(colors) pattern, since there's no RN StyleSheet bridge into
// that WebView's own document.
function signaturePadWebStyle(colors: ColorScheme): string {
  return `
    .m-signature-pad--footer { display: none; margin: 0; }
    .m-signature-pad { box-shadow: none; border: none; }
    body, html { background-color: ${colors.surface}; }
  `;
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    footer: { flexDirection: 'row', gap: spacing.sm, padding: spacing.lg },
  });
