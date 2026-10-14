import { useContext, useMemo } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native';
import { BottomTabBarHeightContext } from '@react-navigation/bottom-tabs';
import { HeaderHeightContext, HeaderShownContext } from '@react-navigation/elements';
import type { ReactNode } from 'react';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { Edge } from 'react-native-safe-area-context';

import { spacing, useThemeColors } from '../theme';
import type { ColorScheme } from '../theme';

export function ScreenContainer({ children, scroll = true }: { children: ReactNode; scroll?: boolean }) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  // A navigation header already sits below the status bar, and the tab bar
  // already clears the home indicator. Taking those insets again doubled them:
  // a ~60px empty band above every screen with a header.
  const headerShown = useContext(HeaderShownContext);
  const tabBarHeight = useContext(BottomTabBarHeightContext);
  const edges: Edge[] = ['left', 'right'];
  if (!headerShown) edges.push('top');
  if (!tabBarHeight) edges.push('bottom');
  // The keyboard avoider measures itself from below the header, so on a screen
  // with one it fell short by the header's height and the support composer
  // stayed under the keyboard.
  const headerHeight = useContext(HeaderHeightContext) ?? 0;
  return (
    <SafeAreaView style={styles.safeArea} edges={edges}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={headerHeight}
      >
        {/* Rendered inline, never as a component made here: a component type
            created on each render remounts everything below it, which dropped
            the keyboard after every keystroke in Contact support. Not
            scrolling still keeps the gutter: these screens hold their own list. */}
        {scroll ? (
          <ScrollView contentContainerStyle={styles.scrollContent}>{children}</ScrollView>
        ) : (
          <View style={styles.fixedContent}>{children}</View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    safeArea: { flex: 1, backgroundColor: colors.bg },
    flex: { flex: 1 },
    scrollContent: { padding: spacing.lg, flexGrow: 1 },
    fixedContent: { flex: 1, paddingHorizontal: spacing.lg, paddingTop: spacing.lg, paddingBottom: spacing.md },
  });
