import { useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { api, ApiError } from '../api/client';
import type { JobOffer, Route } from '../api/types';
import { spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { Button } from './Button';
import { Card } from './Card';

interface OfferBannerProps {
  offer: JobOffer;
  onAccept: (route: Route) => void;
  onDecline: () => void;
}

function msRemaining(expiresAt: string): number {
  return new Date(expiresAt).getTime() - Date.now();
}

// Rendered inline on TodayRouteScreen when an offer is waiting - replaces
// the old JobDetailScreen's pushed route entirely (per the wireframe, a
// job offer is something you see and act on right on the home screen, not
// a separate accept/decline screen). Still owns the accept-window
// countdown and the accept/decline calls verbatim from that old screen.
export function OfferBanner({ offer, onAccept, onDecline }: OfferBannerProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => setSecondsLeft(Math.max(0, Math.floor(msRemaining(offer.expires_at) / 1000)));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [offer.expires_at]);

  async function handleAccept() {
    setBusy(true);
    setError(null);
    try {
      const route = await api.acceptOffer(offer.offer_id);
      onAccept(route);
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
      await api.declineOffer(offer.offer_id);
      onDecline();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'This offer is no longer available.');
    } finally {
      setBusy(false);
    }
  }

  const shopName = offer.stops[0]?.shop_name || 'Pickup';

  return (
    <Card style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.headerTitle}>
          {offer.stops.length} stop{offer.stops.length > 1 ? 's' : ''}
        </Text>
        {secondsLeft !== null && (
          <Text style={[styles.countdownText, secondsLeft <= 15 && styles.urgent]}>
            {secondsLeft > 0 ? `Expires in ${secondsLeft}s` : 'Expired'}
          </Text>
        )}
      </View>

      <Text style={styles.cardLabel}>Pickup</Text>
      <Text style={styles.cardBody}>{shopName}</Text>

      {error && <Text style={styles.error}>{error}</Text>}

      <View style={styles.buttonRow}>
        <View style={styles.buttonHalf}>
          <Button label="Decline" variant="outline" onPress={handleDecline} disabled={busy} />
        </View>
        <View style={styles.buttonHalf}>
          <Button label="Accept job" onPress={handleAccept} loading={busy} disabled={secondsLeft === 0} />
        </View>
      </View>
    </Card>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    card: { marginBottom: spacing.lg, gap: spacing.xs },
    headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    headerTitle: { ...typography.title, color: colors.textPrimary, fontSize: 18 },
    cardLabel: { ...typography.label, color: colors.textPrimary, marginTop: spacing.sm },
    cardBody: { ...typography.body, color: colors.textPrimary },
    countdownText: { ...typography.small, color: colors.textMuted },
    urgent: { color: colors.danger, fontWeight: '700' },
    error: { color: colors.danger, marginTop: spacing.sm, fontSize: 13 },
    buttonRow: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.md },
    buttonHalf: { flex: 1 },
  });
