import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { CheckCircle2, Circle } from 'lucide-react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { FlagReasonCode } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { outboxManager } from '../offline/outboxManager';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'FlagIssue'>;

const REASONS: { code: FlagReasonCode; label: string }[] = [
  { code: 'SHOP_CLOSED', label: 'Shop is closed' },
  { code: 'ACCESS_ISSUE', label: "Can't access location" },
  { code: 'COD_DISPUTE', label: 'Cash/payment dispute' },
  { code: 'PARTS_MISSING', label: 'Parts missing from order' },
  { code: 'REFUSED', label: 'Customer refused delivery' },
];

// Matches the wireframe's "Flag an issue" screen - a stop that can't be
// completed normally (shop closed, access blocked, a dispute, etc.) gets a
// specific reason code, not a dead end. Replaces the old inert
// Alert.alert('Report an issue', ...) stub.
export function FlagIssueScreen({ route, navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { stopId } = route.params;
  const [reason, setReason] = useState<FlagReasonCode | null>(null);
  const [note, setNote] = useState('');

  // Offline-first, like StopDetailScreen's actions - queues immediately
  // (safe even in a dead zone) rather than blocking this screen on the
  // network.
  async function handleSubmit() {
    if (!reason) return;
    await outboxManager.enqueue('flag', stopId, { reason, note: note.trim() || undefined });
    // The stop is now terminal - going back to StopDetail would show a
    // dead screen, so return straight to the route overview.
    navigation.navigate('Home');
  }

  return (
    <ScreenContainer>
      <Text style={styles.title}>What's wrong?</Text>

      <Card style={styles.card}>
        {REASONS.map(({ code, label }) => (
          <Pressable key={code} style={styles.reasonRow} onPress={() => setReason(code)}>
            {reason === code ? (
              <CheckCircle2 size={22} color={colors.primary} />
            ) : (
              <Circle size={22} color={colors.borderStrong} />
            )}
            <Text style={styles.reasonLabel}>{label}</Text>
          </Pressable>
        ))}
      </Card>

      <TextField label="Add a note (optional)" value={note} onChangeText={setNote} multiline />

      <Button label="Submit flag" onPress={handleSubmit} disabled={!reason} />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    title: { ...typography.title, color: colors.textPrimary, marginBottom: spacing.lg },
    card: { marginBottom: spacing.lg, gap: 0, padding: 0 },
    reasonRow: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: spacing.md,
      paddingVertical: spacing.md,
      paddingHorizontal: spacing.lg,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    reasonLabel: { ...typography.body, color: colors.textPrimary, flex: 1 },
    error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
  });
