import { useMemo } from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';

import type { DriverCompliance } from '../api/types';
import { radius, spacing, typography, useThemeColors } from '../theme';
import { ShieldAlert } from 'lucide-react-native';
import { Banner } from './ui/kit';
import type { ColorScheme } from '../theme';

interface ComplianceBannerProps {
  compliance: DriverCompliance;
  onOpenDocuments: () => void;
}

/**
 * Why the go-online toggle is refusing (docs/ROADMAP.md R4).
 *
 * **Shown before the driver taps, not after.** Without this, a driver whose
 * documents are not verified taps the switch, gets a 409, and has no idea what to
 * do about it - the error text is the only clue and it arrives after the failure.
 * A driver standing in a depot at 6am should be able to see the problem and, when
 * it is theirs to fix, tap straight through to fixing it.
 *
 * Every reason at once rather than the first, because a driver missing two
 * documents should not be sent back twice. And the wording distinguishes whose
 * move it is: an upload is theirs, a review is ours, and telling them to "fix" a
 * document sitting in our queue would send them round in circles.
 */
export function ComplianceBanner({ compliance, onOpenDocuments }: ComplianceBannerProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);

  if (compliance.can_go_on_shift) {
    return null;
  }

  // Anything the driver can act on themselves. If every outstanding problem is
  // awaiting our review, offering a "Fix" button would be a dead end.
  const actionable = compliance.problems.some(
    (p) => p.reason === 'missing' || p.reason === 'rejected' || p.reason === 'expired',
  );

  return (
    // Peach: something a person has to sort out before the day can start.
    <Banner icon={ShieldAlert} tone="needs" title="You can&apos;t go online yet">
      {compliance.problems.map((problem) => (
        <Text key={`${problem.doc_type}-${problem.reason}`} style={styles.line}>
          • {problem.detail}
        </Text>
      ))}
      {actionable ? (
        <Pressable onPress={onOpenDocuments} style={styles.action}>
          <Text style={styles.actionLabel}>Open documents</Text>
        </Pressable>
      ) : (
        <Text style={styles.waiting}>
          {/* LMX sends no texts; the switch simply works once it is checked. */}
          Nothing for you to do. You can go online as soon as it&apos;s checked.
        </Text>
      )}
    </Banner>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    line: { ...typography.small, fontSize: 12.5, color: colors.needs },
    // One thing to press, in ink, as everywhere else.
    action: {
      marginTop: spacing.md,
      alignSelf: 'flex-start',
      backgroundColor: colors.primary,
      borderRadius: radius.pill,
      minHeight: 44,
      paddingHorizontal: spacing.lg,
      justifyContent: 'center',
    },
    actionLabel: { ...typography.body, color: colors.primaryText, fontFamily: 'PlusJakartaSans_700Bold' },
    waiting: { ...typography.small, fontSize: 12.5, color: colors.needs, marginTop: spacing.sm },
  });
