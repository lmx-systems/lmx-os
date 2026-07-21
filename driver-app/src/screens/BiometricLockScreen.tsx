import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

// Shown when a stored session exists but biometric unlock failed, was
// cancelled, or the device is temporarily locked out (too many attempts) -
// distinct from the sign-in flow, since the driver is already
// authenticated and this is purely a "prove it's you holding the phone"
// gate. Always offers a way out (sign out -> phone/OTP) so a failed
// biometric attempt never strands a driver on a dead screen.
export function BiometricLockScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { retryBiometric, signOut } = useAuth();

  return (
    <ScreenContainer>
      <View style={styles.body}>
        <Text style={styles.title}>LMX Driver is locked</Text>
        <Text style={styles.subtitle}>Unlock with Face ID or Touch ID to continue.</Text>

        <Button label="Unlock" onPress={retryBiometric} />
        <Button label="Sign out and use phone number instead" variant="outline" onPress={signOut} />
      </View>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    body: { flex: 1, justifyContent: 'center', gap: spacing.md },
    title: { ...typography.title, color: colors.textPrimary, textAlign: 'center', marginBottom: spacing.xs },
    subtitle: {
      ...typography.subtitle,
      color: colors.textSecondary,
      textAlign: 'center',
      marginBottom: spacing.xl,
    },
  });
