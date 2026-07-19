import { useCallback, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Card } from '../components/Card';
import { ScreenContainer } from '../components/ScreenContainer';
import type { MainStackParamList } from '../navigation/types';
import type { JobOffer } from '../api/types';
import { colors, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<MainStackParamList, 'AvailableJobs'>;

// Screen 1f, "Available jobs". v1 has no distance/pay-estimate fields yet
// (that math isn't in the backend - see docs/NEXT_STEPS.md), so cards show
// what's real: shop, SLA tier, and stop count from the offer payload.
export function AvailableJobsScreen({ navigation }: Props) {
  const [offers, setOffers] = useState<JobOffer[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setOffers(await api.getMyOffers());
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  async function handleRefresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  return (
    <ScreenContainer scroll={false}>
      <Text style={[typography.title, styles.title]}>Available</Text>
      <FlatList
        data={offers}
        keyExtractor={(item) => item.offer_id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} />}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Text style={[typography.subtitle, styles.empty]}>No offers waiting right now.</Text>
        }
        renderItem={({ item }) => (
          <Pressable onPress={() => navigation.navigate('JobDetail', { offerId: item.offer_id })}>
            <Card style={styles.card}>
              <View style={styles.cardHeaderRow}>
                <Text style={typography.body}>{item.stops[0]?.shop_name || 'Pickup'}</Text>
                <Text style={styles.tierBadge}>{item.stops[0]?.sla_tier}</Text>
              </View>
              <Text style={typography.small}>
                {item.stops.length} stop{item.stops.length > 1 ? 's' : ''}
              </Text>
            </Card>
          </Pressable>
        )}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  title: { paddingHorizontal: spacing.lg, paddingTop: spacing.lg },
  list: { padding: spacing.lg, gap: spacing.md },
  card: { marginBottom: spacing.md },
  cardHeaderRow: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: spacing.xs },
  tierBadge: { fontSize: 12, fontWeight: '700', color: colors.accent },
  empty: { textAlign: 'center', marginTop: spacing.xxl },
});
