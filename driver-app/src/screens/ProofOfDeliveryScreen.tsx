import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { api } from '../api/client';
import { Button } from '../components/Button';
import { ScreenContainer } from '../components/ScreenContainer';
import { TextField } from '../components/TextField';
import type { PodMethod } from '../api/types';
import type { MainStackParamList } from '../navigation/types';
import { colors, radius, spacing, typography } from '../theme';

type Props = NativeStackScreenProps<MainStackParamList, 'ProofOfDelivery'>;

const METHODS: PodMethod[] = ['photo', 'signature', 'pin'];

// Screen 1m, "Proof of delivery". No real camera/signature-pad capture in
// v1 - "tap to capture" just records a placeholder value so the backend
// contract (Stop.pod_method/pod_photo_url/pod_signature_url/pod_pin) is
// fully exercised; swapping in expo-camera / a signature-pad library is a
// fast-follow that shouldn't need any API changes.
export function ProofOfDeliveryScreen({ route, navigation }: Props) {
  const { stopId } = route.params;
  const [method, setMethod] = useState<PodMethod>('photo');
  const [captured, setCaptured] = useState(false);
  const [pin, setPin] = useState('');
  const [leftAt, setLeftAt] = useState('front door');
  const [busy, setBusy] = useState(false);

  const canComplete = method === 'pin' ? pin.length >= 4 : captured;

  async function handleComplete() {
    setBusy(true);
    try {
      await api.completeStop(stopId, {
        method,
        photo_url: method === 'photo' ? `local-capture://${stopId}.jpg` : undefined,
        signature_url: method === 'signature' ? `local-capture://${stopId}.png` : undefined,
        pin: method === 'pin' ? pin : undefined,
      });
      navigation.replace('ActiveRoute');
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScreenContainer>
      <Text style={typography.title}>Confirm delivery</Text>

      <Text style={[typography.label, styles.sectionLabel]}>Method</Text>
      <View style={styles.segmentRow}>
        {METHODS.map((m) => (
          <Pressable
            key={m}
            onPress={() => {
              setMethod(m);
              setCaptured(false);
            }}
            style={[styles.segment, method === m && styles.segmentActive]}
          >
            <Text style={[styles.segmentLabel, method === m && styles.segmentLabelActive]}>
              {m[0].toUpperCase() + m.slice(1)}
            </Text>
          </Pressable>
        ))}
      </View>

      {method !== 'pin' ? (
        <Pressable style={styles.capturePlaceholder} onPress={() => setCaptured(true)}>
          <Text style={typography.small}>
            {captured ? `${method} captured ✓` : `tap to capture drop-off ${method}`}
          </Text>
        </Pressable>
      ) : (
        <TextField label="Delivery PIN" placeholder="1234" keyboardType="number-pad" value={pin} onChangeText={setPin} maxLength={6} />
      )}

      <TextField label="Left at" value={leftAt} onChangeText={setLeftAt} />

      <Button label="Complete delivery" onPress={handleComplete} loading={busy} disabled={!canComplete} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  sectionLabel: { marginTop: spacing.lg, marginBottom: spacing.xs },
  segmentRow: { flexDirection: 'row', gap: spacing.sm, marginBottom: spacing.lg },
  segment: { flex: 1, paddingVertical: spacing.sm + 2, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, alignItems: 'center' },
  segmentActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  segmentLabel: { color: colors.textPrimary, fontWeight: '600' },
  segmentLabelActive: { color: colors.primaryText },
  capturePlaceholder: {
    height: 140,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: colors.borderStrong,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.lg,
  },
});
