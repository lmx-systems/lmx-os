import { useCallback, useMemo, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import * as Device from 'expo-device';

import { api } from '../api/client';
import { getApiBaseUrl } from '../api/serverUrl';
import { useAuth } from '../auth/AuthContext';
import { getOrCreateDeviceId } from '../auth/deviceId';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import { BarcodeScannerModal } from '../media/BarcodeScannerModal';
import type { AuthStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { signInFailure } from '../utils/signInFailure';

type Props = NativeStackScreenProps<AuthStackParamList, 'SignIn'>;

// What a sign-in QR code from the ops console carries: a marker and the code.
// Never a server address - a planted QR must not be able to point the app at
// somebody else's server.
const QR_PREFIX = 'LMX-SIGNIN:';

// Screen 1a, "Sign in". The hub's ops team gives the driver a sign-in code from the
// ops console, as a QR code to scan or ten characters to type. It replaced the
// texted code, so signing in needs no SMS provider. "Apply to drive" is out of
// app scope per the wireframe's annotation - drivers are provisioned by ops.
export function SignInScreen({ navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { signIn } = useAuth();
  const [code, setCode] = useState('');
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Read again whenever the screen comes back into view, so an address changed
  // on the Server screen shows here on return.
  const [server, setServer] = useState(getApiBaseUrl());
  useFocusEffect(useCallback(() => setServer(getApiBaseUrl()), []));

  async function submit(raw: string) {
    if (!raw.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const deviceId = await getOrCreateDeviceId();
      const deviceName = Device.deviceName ?? Device.modelName ?? null;
      const token = await api.signIn(raw.trim(), deviceId, deviceName);
      await signIn(token.access_token);
      // RootNavigator reacts to isSignedIn/profile and switches stacks.
    } catch (err) {
      setError(signInFailure(err, server));
    } finally {
      setLoading(false);
    }
  }

  function handleScanned(data: string) {
    setScanning(false);
    if (!data.toUpperCase().startsWith(QR_PREFIX)) {
      setError("That isn't a sign-in code. Scan the QR code the ops team shows you.");
      return;
    }
    void submit(data);
  }

  return (
    <ScreenContainer>
      <View style={styles.logo}>
        <Image source={require('../../assets/lmx-mark.png')} style={styles.logoBox} />
      </View>
      <Text style={[styles.titleText, styles.centered]}>LMX Driver</Text>
      <Text style={[styles.subtitleText, styles.centered, styles.tagline]}>
        Deliver more, drive smarter.
      </Text>

      <Button label="Scan sign-in code" onPress={() => setScanning(true)} disabled={loading} />

      <Text style={[styles.footerText, styles.centered, styles.or]}>or type it</Text>

      <TextField
        label="Sign-in code"
        placeholder="XXXXX-XXXXX"
        autoCapitalize="characters"
        autoCorrect={false}
        autoComplete="off"
        maxLength={24}
        value={code}
        onChangeText={setCode}
      />
      {error && <Text style={styles.error}>{error}</Text>}

      <Button
        label="Sign in"
        variant="outline"
        onPress={() => submit(code)}
        loading={loading}
        disabled={!code.trim()}
      />

      <Text style={[styles.footerText, styles.centered, styles.footer]}>
        No code? The ops team at your hub can give you one.
      </Text>

      <BarcodeScannerModal
        visible={scanning}
        onScanned={handleScanned}
        onCancel={() => setScanning(false)}
        hint="Point the camera at the sign-in code on the dispatcher's screen"
        permissionText="Camera access is needed to scan your sign-in code. You can type the code instead."
      />

      {/* Before a session exists, because signing in needs the right server.
          It was only under Profile, which is behind sign-in. */}
      <Pressable
        onPress={() => navigation.navigate('Server')}
        accessibilityRole="button"
        hitSlop={8}
        style={styles.serverRow}
      >
        <Text style={[styles.footerText, styles.centered]}>
          Server: {server} · Change
        </Text>
      </Pressable>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    logo: { alignItems: 'center', marginTop: spacing.xxl, marginBottom: spacing.lg },
    logoBox: { width: 64, height: 64, borderRadius: 16 },
    titleText: { ...typography.title, color: colors.textPrimary },
    subtitleText: { ...typography.subtitle, color: colors.textSecondary },
    footerText: { ...typography.small, color: colors.textMuted },
    centered: { textAlign: 'center' },
    tagline: { marginBottom: spacing.xxl },
    footer: { marginTop: spacing.lg },
    or: { marginVertical: spacing.md },
    serverRow: { marginTop: spacing.md, minHeight: 44, justifyContent: 'center' },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
