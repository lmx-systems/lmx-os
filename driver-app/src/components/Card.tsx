import { useMemo } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { ReactNode } from 'react';

import { radius, spacing, useTheme } from '../theme';
import type { ColorScheme } from '../theme';

interface CardProps {
  children: ReactNode;
  style?: object;
  onPress?: () => void;
}

export function Card({ children, style, onPress }: CardProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  if (onPress) {
    return (
      <Pressable style={({ pressed }) => [styles.card, style, pressed && styles.pressed]} onPress={onPress}>
        {children}
      </Pressable>
    );
  }
  return <View style={[styles.card, style]}>{children}</View>;
}

// A tile (docs/DESIGN_SYSTEM.md): white on the grey ground, a large radius, no
// border and no shadow - the ground is what separates one tile from the next.
const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    card: {
      backgroundColor: colors.surface,
      borderRadius: radius.lg,
      padding: spacing.lg + 4,
    },
    pressed: { opacity: 0.85 },
  });
