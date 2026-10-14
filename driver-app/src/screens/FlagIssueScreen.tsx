import { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Ban, Banknote, CheckCircle2, Circle, Lock, PackageX, Store } from 'lucide-react-native';
import type { LucideIcon } from 'lucide-react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { Button } from '../components/Button';
import { Row } from '../components/ui/kit';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { FlagReasonCode } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { outboxManager } from '../offline/outboxManager';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'FlagIssue'>;

const REASONS: { code: FlagReasonCode; label: string; icon: LucideIcon }[] = [
  { code: 'SHOP_CLOSED', label: 'Shop is closed', icon: Store },
  { code: 'ACCESS_ISSUE', label: "Can't access location", icon: Lock },
  { code: 'COD_DISPUTE', label: 'Cash/payment dispute', icon: Banknote },
  { code: 'PARTS_MISSING', label: 'Parts missing from order', icon: PackageX },
  { code: 'REFUSED', label: 'Customer refused delivery', icon: Ban },
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
      <Text style={styles.intro}>Pick what went wrong. The stop closes as failed, and dispatch sees why.</Text>

      <View style={styles.reasons}>
        {REASONS.map(({ code, label, icon }) => (
          // The console's decision row: a peach icon tile because each of these
          // hands the stop to a person; the chosen one outlined in ink.
          <Row
            key={code}
            icon={icon}
            tone="needs"
            title={label}
            selected={reason === code}
            onPress={() => setReason(code)}
            right={reason === code ? <CheckCircle2 size={22} color={colors.primary} /> : <Circle size={22} color={colors.borderStrong} />}
          />
        ))}
      </View>

      <TextField label="Add a note (optional)" value={note} onChangeText={setNote} multiline />

      <Button label="Submit flag" onPress={handleSubmit} disabled={!reason} />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    intro: { ...typography.body, color: colors.textSecondary, marginBottom: spacing.lg },
    reasons: { gap: spacing.sm, marginBottom: spacing.lg },
  });
