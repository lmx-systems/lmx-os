import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { JobOffer } from '../api/types';
import type { HomeStackParamList } from '../navigation/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<HomeStackParamList, 'JobDetail'>;

function msRemaining(expiresAt: string): number {
  return new Date(expiresAt).getTime() - Date.now();
}

// Screen 1g, "Job detail & accept". Accepting is the key decision point -
// it locks the offer to this driver and creates the real Route/Stops
// (app/api/driver_routes.py's accept_offer) - declining or letting the
// countdown hit zero puts the order back for reassignment instead.
export function JobDetailScreen({ route, navigation }: Props) {
  const { offerId } = route.params;
  const [offer, setOffer] = useState<JobOffer | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const offers = await api.getMyOffers();
      const found = offers.find((o) => o.offer_id === offerId) ?? null;
      setOffer(found);
    })();
  }, [offerId]);

  useEffect(() => {
    if (!offer) return;
    const tick = () => setSecondsLeft(Math.max(0, Math.floor(msRemaining(offer.expires_at) / 1000)));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [offer]);

  async function handleAccept() {
    setBusy(true);
    setError(null);
    try {
      await api.acceptOffer(offerId);
      navigation.replace('ActiveRoute');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'This offer is no longer available.');
    } finally {
      setBusy(false);
    }
  }

  async function handleDecline() {
    setBusy(true);
    setError(null);
    try {
      await api.declineOffer(offerId);
      navigation.goBack();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'This offer is no longer available.');
    } finally {
      setBusy(false);
    }
  }

  if (!offer) {
    return (
      <ScreenContainer>
        <Text style={typography.subtitle}>This offer isn't available anymore.</Text>
        <Button label="Back" variant="outline" onPress={() => navigation.goBack()} />
      </ScreenContainer>
    );
  }

  const shopName = offer.stops[0]?.shop_name || 'Pickup';

  return (
    <ScreenContainer>
      <View style={styles.headerRow}>
        <Text style={typography.title}>{offer.stops.length} stop{offer.stops.length > 1 ? 's' : ''}</Text>
        {secondsLeft !== null && (
          <Text style={[typography.small, secondsLeft <= 15 && styles.urgent]}>
            {secondsLeft > 0 ? `Expires in ${secondsLeft}s` : 'Expired'}
          </Text>
        )}
      </View>

      <Card style={styles.card}>
        <Text style={typography.label}>Pickup</Text>
        <Text style={typography.body}>{shopName}</Text>
      </Card>

      <Card style={styles.card}>
        <Text style={typography.label}>Drops</Text>
        <Text style={typography.body}>
          {offer.stops.length} order{offer.stops.length > 1 ? 's' : ''}
        </Text>
      </Card>

      {error && <Text style={styles.error}>{error}</Text>}

      <View style={styles.buttonRow}>
        <View style={styles.buttonHalf}>
          <Button label="Decline" variant="outline" onPress={handleDecline} disabled={busy} />
        </View>
        <View style={styles.buttonHalf}>
          <Button label="Accept job" onPress={handleAccept} loading={busy} disabled={secondsLeft === 0} />
        </View>
      </View>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.lg },
  card: { marginBottom: spacing.md },
  urgent: { color: colors.danger, fontWeight: '700' },
  error: { color: colors.danger, marginVertical: spacing.md, fontSize: 13 },
  buttonRow: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.lg },
  buttonHalf: { flex: 1 },
});
