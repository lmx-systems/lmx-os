import { useCallback, useMemo, useState } from 'react';
import { Alert, Linking, StyleSheet, Text, View } from 'react-native';
import { FileText, Landmark, LifeBuoy, MapPin, Server, Smartphone, Truck, Warehouse } from 'lucide-react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { getApiBaseUrl } from '../api/serverUrl';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import { Facts, Row, SectionLabel } from '../components/ui/kit';
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

  const docsDue = documentsNeedingAttention + missingCount;
  const vehicle = profile?.vehicle_type ? profile.vehicle_type[0].toUpperCase() + profile.vehicle_type.slice(1) : 'Not set';

  return (
    <ScreenContainer>
      <Card style={styles.identityCard}>
        <View style={styles.identityTop}>
          <View style={styles.avatar}>
            <Text style={styles.avatarText}>{(profile?.name ?? 'D').trim()[0]?.toUpperCase()}</Text>
          </View>
          <View style={styles.identityText}>
            <Text style={styles.nameText}>{profile?.name ?? 'Driver'}</Text>
            <Text style={styles.phoneText}>{profile?.phone}</Text>
          </View>
        </View>
        <Facts
          items={[
            { k: 'Trips', v: String(profile?.trip_count ?? 0) },
            { k: 'Vehicle', v: vehicle },
            { k: 'Zone', v: profile?.delivery_zone ?? '—' },
          ]}
        />
      </Card>

      <SectionLabel>Your work</SectionLabel>
      <View style={styles.group}>
        <Row
          icon={Truck}
          title={vehicle}
          detail={`${profile?.plate_number ?? '—'} · ${profile?.delivery_zone ?? 'No zone set'}`}
          onPress={() => navigation.navigate('EditVehicle')}
        />
        <Row
          icon={FileText}
          // Peach when something needs the driver: the console's colour for "needs a person".
          tone={documentsNeedAttention ? 'needs' : 'neutral'}
          title="Documents"
          detail={documentsNeedAttention ? `${docsDue} need${docsDue === 1 ? 's' : ''} attention` : 'Up to date'}
          onPress={() => navigation.navigate('Documents')}
        />
        <Row icon={Landmark} title="Bank account" detail={paymentSummary} onPress={() => navigation.navigate('PaymentMethod')} />
      </View>

      <SectionLabel>Privacy and phones</SectionLabel>
      <View style={styles.group}>
        <Row
          icon={MapPin}
          title="Arrival times"
          detail="What the app records at a delivery, and what it doesn't"
          onPress={() => navigation.navigate('LocationDisclosure')}
        />
        <Row icon={Smartphone} title="Your phones" detail="Where you are signed in" onPress={() => navigation.navigate('Devices')} />
        <Row icon={Server} title="Server" detail={getApiBaseUrl()} onPress={() => navigation.navigate('Server')} />
      </View>

      {/* The standalone Dock Log (docs/ROADMAP.md DRV-7). Our own drivers
          survey through the app - `DockSurveyModal`, after a delivery
          completes - so this is the other half: the page for mapping docks we
          do not serve yet, which a driver might be asked to fill in off-shift.
          Opened in a browser rather than embedded, because it is a separate
          surface with its own lifetime and nothing here should look like it
          owns it. */}
      <SectionLabel>Help</SectionLabel>
      <View style={styles.group}>
        <Row icon={LifeBuoy} title="Contact support" detail="Wrong address, blocked access, safety concern" onPress={() => navigation.navigate('Support')} />
        <Row icon={Warehouse} title="Map a dock we don't serve" detail="Opens the Dock Log in your browser" onPress={() => Linking.openURL(DOCK_LOG_URL)} />
      </View>

      {/* Never a bare tap. Logging out with queued stop events does not send
          them and does not discard them - it strands them: the token goes,
          every flush 401s, and `refreshOnce` has no session left to refresh
          with. `flush` treats a 401 as transient precisely so a shift in a
          dead zone survives (DRV-4), and that only holds while the driver is
          still signed in. The rule lives in `logOutWarning` so this screen
          cannot drift from it. */}
      <View style={styles.logOut}>
        <Button label="Log out" variant="outline" onPress={handleLogOut} />
      </View>
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    identityCard: { marginBottom: spacing.md, gap: spacing.lg, borderRadius: 28 },
    identityTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
    avatar: { width: 52, height: 52, borderRadius: 26, backgroundColor: colors.needsDim, alignItems: 'center', justifyContent: 'center' },
    avatarText: { fontFamily: 'PlusJakartaSans_800ExtraBold', fontSize: 20, color: colors.needs },
    identityText: { flex: 1, gap: 2 },
    nameText: { ...typography.headline, color: colors.textPrimary },
    phoneText: { ...typography.body, color: colors.textSecondary },
    group: { gap: spacing.sm, marginBottom: spacing.md },
    logOut: { marginTop: spacing.md },
  });
