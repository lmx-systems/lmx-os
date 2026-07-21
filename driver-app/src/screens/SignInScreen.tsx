import { useMemo, useState } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { AuthStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

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

  async function handleContinue() {
    if (!phone.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.requestOtp(phone.trim());
      navigation.navigate('VerifyCode', { phone: phone.trim(), debugCode: result.debug_code });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.');
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
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
