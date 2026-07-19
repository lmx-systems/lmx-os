import { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { AuthStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<AuthStackParamList, 'SignIn'>;

// Screen 1a, "Sign in" - phone-first login, OTP verification on the next
// screen. "Apply to drive" (non-drivers) is explicitly out of app scope
// per the wireframe's annotation - drivers are provisioned by ops.
export function SignInScreen({ navigation }: Props) {
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
        <View style={styles.logoBox} />
      </View>
      <Text style={[typography.title, styles.centered]}>LMX Driver</Text>
      <Text style={[typography.subtitle, styles.centered, styles.tagline]}>
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

      <Text style={[typography.small, styles.centered, styles.footer]}>
        New driver? Apply to drive
      </Text>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  logo: { alignItems: 'center', marginTop: spacing.xxl, marginBottom: spacing.lg },
  logoBox: { width: 64, height: 64, borderRadius: 16, backgroundColor: colors.border },
  centered: { textAlign: 'center' },
  tagline: { marginBottom: spacing.xxl },
  footer: { marginTop: spacing.lg },
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
});
