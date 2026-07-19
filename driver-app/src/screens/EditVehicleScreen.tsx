import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { ProfileStackParamList } from '../navigation/types';
import { colors, radius, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<ProfileStackParamList, 'EditVehicle'>;

const VEHICLE_TYPES = ['car', 'van', 'bike'] as const;

// Screen 1r's "Edit vehicle" - same fields as the one-time setup screen
// (1c, VehicleSetupScreen) but reachable any time from Profile, pre-filled
// with the driver's current values instead of starting blank.
export function EditVehicleScreen({ navigation }: Props) {
  const { profile, setProfile } = useAuth();
  const [vehicleType, setVehicleType] = useState<(typeof VEHICLE_TYPES)[number]>(
    (profile?.vehicle_type as (typeof VEHICLE_TYPES)[number]) ?? 'car',
  );
  const [plateNumber, setPlateNumber] = useState(profile?.plate_number ?? '');
  const [deliveryZone, setDeliveryZone] = useState(profile?.delivery_zone ?? '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    if (!plateNumber.trim() || !deliveryZone.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await api.updateMyProfile({
        vehicle_type: vehicleType,
        plate_number: plateNumber.trim(),
        delivery_zone: deliveryZone.trim(),
      });
      setProfile(updated);
      navigation.goBack();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save - try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenContainer>
      <Text style={typography.label}>Vehicle type</Text>
      <View style={styles.segmentRow}>
        {VEHICLE_TYPES.map((type) => (
          <Pressable
            key={type}
            onPress={() => setVehicleType(type)}
            style={[styles.segment, vehicleType === type && styles.segmentActive]}
          >
            <Text style={[styles.segmentLabel, vehicleType === type && styles.segmentLabelActive]}>
              {type[0].toUpperCase() + type.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      <TextField label="Plate number" placeholder="ABC · 1234" value={plateNumber} onChangeText={setPlateNumber} autoCapitalize="characters" />
      <TextField label="Delivery zone" placeholder="Downtown — Zone 4" value={deliveryZone} onChangeText={setDeliveryZone} />

      {error && <Text style={styles.error}>{error}</Text>}

      <Button
        label="Save changes"
        onPress={handleSave}
        loading={loading}
        disabled={!plateNumber.trim() || !deliveryZone.trim()}
      />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  segmentRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs, marginBottom: spacing.lg },
  segment: {
    flex: 1,
    paddingVertical: spacing.sm + 2,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: 'center',
  },
  segmentActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  segmentLabel: { color: colors.textPrimary, fontWeight: '600' },
  segmentLabelActive: { color: colors.primaryText },
  error: { color: colors.danger, marginBottom: spacing.md, fontSize: 13 },
});
