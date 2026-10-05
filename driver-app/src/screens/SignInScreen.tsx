import { useCallback, useMemo, useState } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { getApiBaseUrl } from '../api/serverUrl';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { AuthStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { signInFailure } from '../utils/signInFailure';

type Props = NativeStackScreenProps<AuthStackParamList, 'SignIn'>;

// Screen 1a, "Sign in" - phone-first login, OTP verification on the next
// screen. "Apply to drive" (non-drivers) is explicitly out of app scope
// per the wireframe's annotation - drivers are provisioned by ops.
export function SignInScreen({ navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [phone, setPhone] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Read again whenever the screen comes back into view, so an address changed
  // on the Server screen shows here on return.
  const [server, setServer] = useState(getApiBaseUrl());
  useFocusEffect(useCallback(() => setServer(getApiBaseUrl()), []));

  async function handleContinue() {
    if (!phone.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.requestOtp(phone.trim());
      navigation.navigate('VerifyCode', { phone: phone.trim(), debugCode: result.debug_code });
    } catch (err) {
      setError(signInFailure(err, server));
    } finally {
      setLoading(false);
    }
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

      <TextField
        label="Phone number"
        placeholder="+1 (555) 000-0000"
        keyboardType="phone-pad"
        autoComplete="tel"
        value={phone}
        onChangeText={setPhone}
      />
      {error && <Text style={styles.error}>{error}</Text>}

      <Button label="Continue" onPress={handleContinue} loading={loading} disabled={!phone.trim()} />

      <Text style={[styles.footerText, styles.centered, styles.footer]}>
        New driver? Apply to drive
      </Text>

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
    serverRow: { marginTop: spacing.md, minHeight: 44, justifyContent: 'center' },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
