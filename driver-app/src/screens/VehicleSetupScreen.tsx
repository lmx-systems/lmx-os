import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { api, ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

const VEHICLE_TYPES = ['car', 'van', 'bike'] as const;

// Screen 1c, "Vehicle & profile setup" - blocks going online until
// complete (see RootNavigator, which routes here whenever
// profile.vehicle_type is null). Document/insurance uploads aren't part
// of this screen in v1 - they live in the profile screen (1r, Phase 2).
export function VehicleSetupScreen() {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const { setProfile } = useAuth();
  const [vehicleType, setVehicleType] = useState<(typeof VEHICLE_TYPES)[number]>('car');
  const [plateNumber, setPlateNumber] = useState('');
  const [deliveryZone, setDeliveryZone] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFinish() {
    if (!plateNumber.trim() || !deliveryZone.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await api.updateMyProfile({
        vehicle_type: vehicleType,
        plate_number: plateNumber.trim(),
        delivery_zone: deliveryZone.trim(),
      });
      setProfile(updated); // flips RootNavigator over to the main app
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save - try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <ScreenContainer>
      <Text style={styles.stepText}>Step 2 of 2</Text>
      <Text style={styles.title}>Your vehicle</Text>

      <Text style={styles.fieldLabel}>Vehicle type</Text>
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
        label="Finish setup"
        onPress={handleFinish}
        loading={loading}
        disabled={!plateNumber.trim() || !deliveryZone.trim()}
      />
    </ScreenContainer>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    stepText: { ...typography.small, color: colors.textMuted },
    title: { ...typography.title, color: colors.textPrimary, marginBottom: spacing.lg },
    fieldLabel: { ...typography.label, color: colors.textPrimary },
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
