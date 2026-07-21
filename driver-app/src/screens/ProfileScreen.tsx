import { useCallback, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { ChevronRight } from 'lucide-react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { DriverDocument } from '../api/types';
import type { ProfileStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<ProfileStackParamList, 'ProfileHome'>;

function isExpired(doc: DriverDocument): boolean {
  return doc.expires_at < new Date().toISOString().slice(0, 10);
}

// Screen 1r, "Profile". Real data throughout - trip_count is a count of
// completed Routes (app/api/driver_routes.py), not invented. No star
// rating anywhere: there's no rating-submission system, so a number here
// would be fabricated rather than just estimated. Earnings is deliberately
// out of scope too (docs/NEXT_STEPS.md item 12) - Sourabh chose to skip it
// for this phase rather than have us guess at a pay formula.
export function ProfileScreen({ navigation }: Props) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { profile, signOut } = useAuth();
  const [documents, setDocuments] = useState<DriverDocument[] | null>(null);

  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      (async () => {
        const docs = await api.getMyDocuments();
        if (!cancelled) setDocuments(docs);
      })();
      return () => {
        cancelled = true;
      };
    }, []),
  );

  const expiredCount = documents?.filter(isExpired).length ?? 0;
  const missingCount = 2 - (documents?.length ?? 0); // license + insurance
  const documentsNeedAttention = expiredCount > 0 || missingCount > 0;

  const paymentSummary = profile?.payment_bank_last4 ? `•••• ${profile.payment_bank_last4}` : 'Not set';

  return (
    <ScreenContainer>
      <Card style={styles.identityCard}>
        <Text style={styles.nameText}>{profile?.name ?? 'Driver'}</Text>
        <Text style={styles.phoneText}>{profile?.phone}</Text>
        <View style={styles.statsRow}>
          <View>
            <Text style={styles.statLabel}>Trips completed</Text>
            <Text style={styles.statValue}>{profile?.trip_count ?? 0}</Text>
          </View>
        </View>
      </Card>

      <Text style={styles.sectionLabel}>Vehicle</Text>
      <Pressable onPress={() => navigation.navigate('EditVehicle')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>
              {profile?.vehicle_type ? profile.vehicle_type[0].toUpperCase() + profile.vehicle_type.slice(1) : 'Not set'}
            </Text>
            <Text style={styles.rowSmall}>
              {profile?.plate_number ?? '—'} · {profile?.delivery_zone ?? 'No zone set'}
            </Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <Text style={styles.sectionLabel}>Compliance</Text>
      <Pressable onPress={() => navigation.navigate('Documents')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Documents</Text>
            <Text style={[styles.rowSmall, documentsNeedAttention && styles.warningText]}>
              {documentsNeedAttention
                ? `${expiredCount + missingCount} need${expiredCount + missingCount === 1 ? 's' : ''} attention`
                : 'Up to date'}
            </Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <Text style={styles.sectionLabel}>Payment</Text>
      <Pressable onPress={() => navigation.navigate('PaymentMethod')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Bank account</Text>
            <Text style={styles.rowSmall}>{paymentSummary}</Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <Text style={styles.sectionLabel}>Help</Text>
      <Pressable onPress={() => navigation.navigate('Support')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Contact support</Text>
            <Text style={styles.rowSmall}>Wrong address, blocked access, safety concern</Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <View style={styles.spacer} />
      <Text style={styles.rowSmall} onPress={signOut}>
        Log out
      </Text>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    identityCard: { marginBottom: spacing.lg },
    nameText: { ...typography.title, color: colors.textPrimary },
    phoneText: { ...typography.subtitle, color: colors.textSecondary },
    statsRow: { flexDirection: 'row', marginTop: spacing.md },
    statLabel: { ...typography.label, color: colors.textPrimary },
    statValue: { fontSize: 20, fontWeight: '700', color: colors.textPrimary, marginTop: spacing.xs },
    sectionLabel: { ...typography.label, color: colors.textPrimary, marginBottom: spacing.xs },
    row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: spacing.lg },
    rowText: { flex: 1 },
    rowBody: { ...typography.body, color: colors.textPrimary },
    rowSmall: { ...typography.small, color: colors.textMuted },
    warningText: { color: colors.warning },
    spacer: { flex: 1, marginTop: spacing.xl },
  });
