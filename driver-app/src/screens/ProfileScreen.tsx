import { useCallback, useMemo, useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { ChevronRight } from 'lucide-react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { getApiBaseUrl } from '../api/serverUrl';
import { useAuth } from '../auth/AuthContext';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { outboxManager } from '../offline/outboxManager';
import { logOutWarning } from '../utils/logOutWarning';
import type { DriverDocument } from '../api/types';

// The standalone dock survey, for docks LMX does not serve yet (`DRV-7`).
//
// **The page is built** (`client-portal/src/components/DockLogPage.tsx`) and the
// route exists. What is still missing is the deployment: `infra/aws/` has never
// been applied, so `portal.lmxit.com` does not resolve and this 404s in the
// hands of anybody who taps it today. That is why it sits in Profile rather
// than anywhere a driver meets during a shift.
//
// Deliberately a constant rather than a setting: it is one page on our own
// portal, not a per-install address like `serverUrl.ts`'s.
const DOCK_LOG_URL = 'https://portal.lmxit.com/dock-log';
import type { ProfileStackParamList } from '../navigation/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

type Props = NativeStackScreenProps<ProfileStackParamList, 'ProfileHome'>;

// A document is a problem unless it is verified AND unexpired against the date an
// LMX reviewer read off it (R4). Deliberately not the driver's claimed date - the
// whole point of the review is that the claim is not the fact - and deliberately
// `!is_usable` rather than an expiry comparison, so this agrees with the
// go-online gate instead of reimplementing it slightly differently.
function needsAttention(doc: DriverDocument): boolean {
  return !doc.is_usable;
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

  function handleLogOut() {
    const warning = logOutWarning(outboxManager.pendingCount());
    if (warning === null) {
      void signOut();
      return;
    }
    Alert.alert('Log out anyway?', warning, [
      { text: 'Stay signed in', style: 'cancel' },
      { text: 'Log out', style: 'destructive', onPress: () => void signOut() },
    ]);
  }

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

  const documentsNeedingAttention = documents?.filter(needsAttention).length ?? 0;
  const missingCount = 2 - (documents?.length ?? 0); // license + insurance
  const documentsNeedAttention = documentsNeedingAttention > 0 || missingCount > 0;

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
                ? `${documentsNeedingAttention + missingCount} need${documentsNeedingAttention + missingCount === 1 ? 's' : ''} attention`
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

      <Text style={styles.sectionLabel}>Privacy</Text>
      <Pressable onPress={() => navigation.navigate('LocationDisclosure')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Arrival times</Text>
            <Text style={styles.rowSmall}>What the app records at a delivery, and what it doesn&apos;t</Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <Pressable onPress={() => navigation.navigate('Devices')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Your phones</Text>
            <Text style={styles.rowSmall}>Where you are signed in, and how to sign a phone out</Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      <Pressable onPress={() => navigation.navigate('Server')}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Server</Text>
            <Text style={styles.rowSmall}>{getApiBaseUrl()}</Text>
          </View>
          <ChevronRight size={20} color={colors.textMuted} />
        </Card>
      </Pressable>

      {/* The standalone Dock Log (docs/ROADMAP.md DRV-7). Our own drivers
          survey through the app - `DockSurveyModal`, after a delivery
          completes - so this is the other half: the page for mapping docks we
          do not serve yet, which a driver might be asked to fill in off-shift.
          Opened in a browser rather than embedded, because it is a separate
          surface with its own lifetime and nothing here should look like it
          owns it. */}
      <Text style={styles.sectionLabel}>Dock Log</Text>
      <Pressable onPress={() => Linking.openURL(DOCK_LOG_URL)}>
        <Card style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowBody}>Map a dock we don't serve</Text>
            <Text style={styles.rowSmall}>
              Opens the survey page in your browser
            </Text>
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
      {/* Never a bare tap. Logging out with queued stop events does not send
          them and does not discard them - it strands them: the token goes,
          every flush 401s, and `refreshOnce` has no session left to refresh
          with. `flush` treats a 401 as transient precisely so a shift in a
          dead zone survives (DRV-4), and that only holds while the driver is
          still signed in. The rule lives in `logOutWarning` so this screen
          cannot drift from it. */}
      <Text style={styles.rowSmall} onPress={handleLogOut}>
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
