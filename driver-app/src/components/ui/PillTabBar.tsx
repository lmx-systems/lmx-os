import { useMemo } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { radius, spacing, useThemeColors } from '../../theme';
import type { ColorScheme } from '../../theme';

/**
 * The tabs as the console's page nav: a white pill group on the grey ground, the
 * open tab an ink pill. 56px tall for gloves, labels always shown.
 */
export function PillTabBar({ state, descriptors, navigation }: BottomTabBarProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.wrap, { paddingBottom: Math.max(insets.bottom, spacing.md) }]}>
      <View style={styles.group} accessibilityRole="tablist">
        {state.routes.map((route, index) => {
          const { options } = descriptors[route.key];
          const focused = state.index === index;
          const label = typeof options.title === 'string' ? options.title : route.name;
          const color = focused ? colors.primaryText : colors.textSecondary;
          const onPress = () => {
            const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
            if (!focused && !event.defaultPrevented) navigation.navigate(route.name, route.params);
          };
          return (
            <Pressable
              key={route.key}
              onPress={onPress}
              accessibilityRole="tab"
              accessibilityState={{ selected: focused }}
              accessibilityLabel={label}
              style={[styles.item, focused && styles.itemOn]}
            >
              {options.tabBarIcon?.({ focused, color, size: 20 })}
              <Text style={[styles.label, { color }]}>{label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    wrap: { backgroundColor: colors.bg, paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
    group: { flexDirection: 'row', backgroundColor: colors.surface, borderRadius: radius.pill, padding: 5, gap: 4 },
    item: {
      flex: 1,
      minHeight: 50,
      borderRadius: radius.pill,
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 6,
    },
    itemOn: { backgroundColor: colors.primary },
    label: { fontFamily: 'PlusJakartaSans_700Bold', fontSize: 13.5 },
  });
