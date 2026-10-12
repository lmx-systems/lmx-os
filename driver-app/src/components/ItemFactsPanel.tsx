import { useEffect, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { api } from '../api/client';
import type { ItemFactsBody, ItemSizeClass, OrderItemView } from '../api/types';
import { outboxManager } from '../offline/outboxManager';
import { radius, spacing, typography, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';
import { TextField } from './TextField';

interface ItemFactsPanelProps {
  stopId: string;
  // From the stop itself, so the rows are there offline, before the list of
  // what is already known has loaded.
  orderIds: string[];
}

const SIZES: { code: ItemSizeClass; label: string }[] = [
  { code: 'envelope', label: 'Envelope' },
  { code: 'small', label: 'Shoebox' },
  { code: 'medium', label: 'Carry-on' },
  { code: 'large', label: 'Car boot' },
  { code: 'oversize', label: 'Van only' },
];

/**
 * What is going in the van (docs/ROADMAP_1.5.md DE-5).
 *
 * The golden record's item element - weight, size, hazardous or liquid - and
 * the one place it can come from today: no order source sends a real weight,
 * and the order's `weight_units` is a capacity count, not kilograms. A van
 * scale is assumed.
 *
 * **Optional, and never in the way.** Collecting the parts is the job; this is
 * the measurement beside it, so the driver can confirm collection with every
 * field blank. A blank stays unknown rather than becoming a guess.
 *
 * Queued like every other action, so a dead zone at the counter loses nothing;
 * a correction before it is sent replaces the first entry.
 */
export function ItemFactsPanel({ stopId, orderIds }: ItemFactsPanelProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [known, setKnown] = useState<Record<string, OrderItemView>>({});
  const [saved, setSaved] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let live = true;
    api
      .stopItems(stopId)
      .then((items) => live && setKnown(Object.fromEntries(items.map((i) => [i.order_id, i]))))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [stopId]);

  return (
    <View style={styles.panel}>
      <Text style={styles.title}>What's going in the van?</Text>
      <Text style={styles.body}>Optional. Weigh it if you can; leave anything you can't tell.</Text>
      {orderIds.map((orderId, index) => (
        <ItemRow
          key={orderId}
          label={known[orderId]?.reference ?? `Order ${index + 1}`}
          initial={known[orderId]}
          saved={!!saved[orderId]}
          styles={styles}
          onSave={async (facts) => {
            await outboxManager.enqueue('item-facts', stopId, { orderId, facts });
            setSaved((s) => ({ ...s, [orderId]: true }));
          }}
        />
      ))}
    </View>
  );
}

function ItemRow({
  label,
  initial,
  saved,
  styles,
  onSave,
}: {
  label: string;
  initial?: OrderItemView;
  saved: boolean;
  styles: ReturnType<typeof makeStyles>;
  onSave: (facts: ItemFactsBody) => Promise<void>;
}) {
  const [weight, setWeight] = useState(initial?.item_weight_kg != null ? String(initial.item_weight_kg) : '');
  const [size, setSize] = useState<ItemSizeClass | null>(initial?.item_size_class ?? null);
  const [hazmat, setHazmat] = useState<boolean | null>(initial?.item_hazmat_or_liquid ?? null);

  const kg = weight.trim() === '' ? undefined : Number(weight.replace(',', '.'));
  const weightOk = kg === undefined || (Number.isFinite(kg) && kg >= 0 && kg <= 2000);
  const anything = kg !== undefined || size !== null || hazmat !== null;

  function facts(): ItemFactsBody {
    return {
      ...(kg !== undefined ? { item_weight_kg: kg } : {}),
      ...(size !== null ? { item_size_class: size } : {}),
      ...(hazmat !== null ? { item_hazmat_or_liquid: hazmat } : {}),
    };
  }

  return (
    <View style={styles.row}>
      <Text style={styles.rowTitle}>{label}</Text>
      <TextField
        label="Weight (kg)"
        value={weight}
        onChangeText={setWeight}
        keyboardType="decimal-pad"
        placeholder="unknown"
      />
      {!weightOk && <Text style={styles.warning}>A weight in kilograms, 0 to 2,000.</Text>}
      <Text style={styles.fieldLabel}>Size</Text>
      <View style={styles.chips}>
        {SIZES.map((s) => (
          <Chip key={s.code} label={s.label} active={size === s.code} onPress={() => setSize(size === s.code ? null : s.code)} styles={styles} />
        ))}
      </View>
      <Text style={styles.fieldLabel}>Hazardous or liquid?</Text>
      <View style={styles.chips}>
        <Chip label="Yes" active={hazmat === true} onPress={() => setHazmat(hazmat === true ? null : true)} styles={styles} />
        <Chip label="No" active={hazmat === false} onPress={() => setHazmat(hazmat === false ? null : false)} styles={styles} />
      </View>
      <Pressable
        accessibilityRole="button"
        disabled={!anything || !weightOk}
        onPress={() => void onSave(facts())}
        style={[styles.save, (!anything || !weightOk) && styles.saveDisabled]}
      >
        <Text style={styles.saveLabel}>{saved ? 'Saved - update' : 'Save'}</Text>
      </Pressable>
    </View>
  );
}

function Chip({
  label,
  active,
  onPress,
  styles,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  styles: ReturnType<typeof makeStyles>;
}) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} style={[styles.chip, active && styles.chipActive]}>
      <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>{label}</Text>
    </Pressable>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    panel: {
      backgroundColor: colors.surfaceAlt,
      borderColor: colors.borderStrong,
      borderWidth: 1,
      borderRadius: radius.md,
      padding: spacing.md,
      marginBottom: spacing.md,
    },
    title: { ...typography.body, color: colors.textPrimary, fontWeight: '700' },
    body: { ...typography.small, color: colors.textSecondary, marginBottom: spacing.md },
    row: { borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.md, marginTop: spacing.sm },
    rowTitle: { ...typography.body, color: colors.textPrimary, marginBottom: spacing.sm },
    fieldLabel: { ...typography.small, color: colors.textSecondary, marginBottom: spacing.sm },
    warning: { ...typography.small, color: colors.danger, marginBottom: spacing.sm },
    chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.md },
    chip: {
      borderWidth: 1,
      borderColor: colors.borderStrong,
      borderRadius: radius.md,
      paddingVertical: spacing.sm,
      paddingHorizontal: spacing.md,
      backgroundColor: colors.surface,
    },
    chipActive: { borderColor: colors.primary, backgroundColor: colors.accentDim },
    chipLabel: { ...typography.small, color: colors.textSecondary },
    chipLabelActive: { color: colors.primary, fontWeight: '700' },
    save: { backgroundColor: colors.primary, borderRadius: radius.md, paddingVertical: spacing.md, alignItems: 'center' },
    saveDisabled: { opacity: 0.4 },
    saveLabel: { ...typography.body, color: colors.primaryText, fontWeight: '700' },
  });
