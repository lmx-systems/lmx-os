import { useState } from 'react';
import { StyleSheet, Text } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { ProfileStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<ProfileStackParamList, 'PaymentMethod'>;

// Screen 1r's payment method card. Last 4 digits only, for display - not
// wired to any real payout rail (see Driver.payment_bank_last4's
// docstring and docs/NEXT_STEPS.md item 12). No account/routing number is
// ever asked for here.
export function PaymentMethodScreen({ navigation }: Props) {
  const { profile, setProfile } = useAuth();
  const [last4, setLast4] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const valid = /^\d{4}$/.test(last4);

  async function handleSave() {
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updatePaymentMethod(last4);
      setProfile(updated);
      navigation.goBack();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save - try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScreenContainer>
      <Card style={styles.card}>
        <Text style={typography.label}>Currently on file</Text>
        <Text style={typography.body}>
          {profile?.payment_bank_last4 ? `Bank account ending •••• ${profile.payment_bank_last4}` : 'Nothing on file yet'}
        </Text>
      </Card>

      <TextField
        label="Last 4 digits of bank account"
        placeholder="1234"
        value={last4}
        onChangeText={(text) => setLast4(text.replace(/\D/g, '').slice(0, 4))}
        keyboardType="number-pad"
        maxLength={4}
      />

      {error && <Text style={styles.error}>{error}</Text>}

      <Button label="Save" onPress={handleSave} loading={busy} disabled={!valid} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  card: { marginBottom: spacing.lg },
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
});
