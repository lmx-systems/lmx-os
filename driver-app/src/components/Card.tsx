import { useMemo } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { ReactNode } from 'react';

import { elevation, radius, spacing, useTheme } from '../theme';
import type { ColorScheme, ThemeMode } from '../theme';

interface CardProps {
  children: ReactNode;
  style?: object;
  onPress?: () => void;
}

export function Card({ children, style, onPress }: CardProps) {
  const { colors, mode } = useTheme();
  const styles = useMemo(() => makeStyles(colors, mode), [colors, mode]);
  if (onPress) {
    return (
      <Pressable style={({ pressed }) => [styles.card, style, pressed && styles.pressed]} onPress={onPress}>
        {children}
      </Pressable>
    );
  }
  return <View style={[styles.card, style]}>{children}</View>;
}

const makeStyles = (colors: ColorScheme, mode: ThemeMode) =>
  StyleSheet.create({
    card: {
      backgroundColor: colors.surface,
      borderRadius: radius.lg,
      borderWidth: 1,
      borderColor: colors.border,
      padding: spacing.lg,
      ...elevation(mode).sm,
    },
    pressed: { opacity: 0.85 },
  });
