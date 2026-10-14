import { useMemo } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { NativeStackHeaderProps } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { ChevronLeft } from 'lucide-react-native';

import { spacing, typography, useThemeColors } from '../../theme';
import type { ColorScheme } from '../../theme';
import { BrandBar } from './kit';

/**
 * Every stack's header, as the console's page header: on the grey ground, a large
 * left-aligned title, and a round white back button rather than the platform's
 * bar. A tab's first screen has no back, so it carries the lmx bar instead.
 */
export function StackHeader({ navigation, options, back }: NativeStackHeaderProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  const title = typeof options.title === 'string' ? options.title : '';
  return (
    <View style={[styles.wrap, { paddingTop: insets.top + spacing.sm }]}>
      {back ? (
        <Pressable
          onPress={navigation.goBack}
          accessibilityRole="button"
          accessibilityLabel={`Back to ${back.title ?? 'previous screen'}`}
          hitSlop={8}
          style={({ pressed }) => [styles.back, pressed && styles.pressed]}
        >
          <ChevronLeft size={22} color={colors.textPrimary} strokeWidth={2.4} />
        </Pressable>
      ) : (
        <BrandBar />
      )}
      {title ? (
        <Text style={styles.title} accessibilityRole="header">
          {title}
        </Text>
      ) : null}
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    wrap: { backgroundColor: colors.bg, paddingHorizontal: spacing.lg, paddingBottom: spacing.xs, gap: spacing.md },
    back: {
      width: 44,
      height: 44,
      borderRadius: 22,
      backgroundColor: colors.surface,
      alignItems: 'center',
      justifyContent: 'center',
    },
    pressed: { opacity: 0.8 },
    title: { ...typography.display, color: colors.textPrimary },
  });
