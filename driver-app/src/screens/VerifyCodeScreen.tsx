import { useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import type { AuthStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<AuthStackParamList, 'VerifyCode'>;

const RESEND_COOLDOWN_SECONDS = 24;

// Screen 1b, "Verify code" - 4-box OTP, auto-advance between boxes,
// auto-submit on the 4th digit, resend disabled during the countdown.
export function VerifyCodeScreen({ route, navigation }: Props) {
  const { phone, debugCode } = route.params;
  const { signIn } = useAuth();
  const [digits, setDigits] = useState(['', '', '', '']);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_SECONDS);
  const inputs = useRef<Array<TextInput | null>>([]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  async function submit(code: string) {
    setLoading(true);
    setError(null);
    try {
      const token = await api.verifyOtp(phone, code);
      await signIn(token.access_token);
      // RootNavigator reacts to isSignedIn/profile and switches stacks -
      // nothing else to navigate to from here.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Invalid or expired code');
      setDigits(['', '', '', '']);
      inputs.current[0]?.focus();
    } finally {
      setLoading(false);
    }
  }

  function handleChange(text: string, index: number) {
    const char = text.slice(-1);
    const next = [...digits];
    next[index] = char;
    setDigits(next);

    if (char && index < 3) {
      inputs.current[index + 1]?.focus();
    }
    if (char && index === 3) {
      const code = next.join('');
      if (code.length === 4) {
        submit(code);
      }
    }
  }

  async function handleResend() {
    if (cooldown > 0) return;
    setCooldown(RESEND_COOLDOWN_SECONDS);
    await api.requestOtp(phone);
  }

  return (
    <ScreenContainer>
      <Text style={typography.title}>Enter the code</Text>
      <Text style={[typography.subtitle, styles.subtitle]}>Sent to {phone}</Text>

      <View style={styles.boxRow}>
        {digits.map((digit, index) => (
          <TextInput
            key={index}
            ref={(el) => {
              inputs.current[index] = el;
            }}
            value={digit}
            onChangeText={(text) => handleChange(text, index)}
            keyboardType="number-pad"
            maxLength={1}
            style={styles.box}
            autoFocus={index === 0}
          />
        ))}
      </View>

      {debugCode && (
        <Text style={styles.debugHint}>Dev mode - no SMS provider configured. Code: {debugCode}</Text>
      )}
      {error && <Text style={styles.error}>{error}</Text>}

      <Text style={typography.small} onPress={handleResend}>
        {cooldown > 0 ? `Resend code in 0:${cooldown.toString().padStart(2, '0')}` : 'Resend code'}
      </Text>

      <View style={styles.spacer} />
      <Button label="Verify" onPress={() => submit(digits.join(''))} loading={loading} disabled={digits.some((d) => !d)} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  subtitle: { marginBottom: spacing.xl },
  boxRow: { flexDirection: 'row', gap: spacing.md, marginBottom: spacing.lg },
  box: {
    width: 56,
    height: 56,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 10,
    textAlign: 'center',
    fontSize: 22,
    backgroundColor: colors.surface,
    color: colors.textPrimary,
  },
  debugHint: { fontSize: 12, color: colors.accent, marginBottom: spacing.md },
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  spacer: { flex: 1 },
});
