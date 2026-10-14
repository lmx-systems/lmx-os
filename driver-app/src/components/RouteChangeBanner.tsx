import { useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type { RouteChangeEvent } from '../realtime/routeEventsClient';
import { spacing, typography, useThemeColors } from '../theme';
import { Shuffle } from 'lucide-react-native';
import { Banner } from './ui/kit';
import type { ColorScheme } from '../theme';
import { Button } from './Button';

interface RouteChangeBannerProps {
  event: RouteChangeEvent;
  onAccept: () => void;
}

// Per the wireframe's "pushed, not yanked" principle - a live route change
// is surfaced for the driver to notice and pull in on their own time, not
// a forced screen takeover. "Accept" re-fetches the route (route replans
// are dispatcher-driven, not actually vetoable by the driver - this is
// "pull in the change," not a real accept/reject decision, despite the
// label matching the wireframe's).
export function RouteChangeBanner({ event, onAccept }: RouteChangeBannerProps) {
  const colors = useThemeColors();
  const styles = useMemo(() => makeStyles(colors), [colors]);
  const [expanded, setExpanded] = useState(false);

  return (
    <Banner icon={Shuffle} tone="neutral" title="Your route changed" body={event.message}>
      {expanded && (
        <Text style={styles.details}>
          {event.affected_stop_ids.length} stop{event.affected_stop_ids.length === 1 ? '' : 's'} affected
        </Text>
      )}
      <View style={styles.buttonRow}>
        <View style={styles.buttonHalf}>
          <Button label="Details" variant="outline" onPress={() => setExpanded((e) => !e)} />
        </View>
        <View style={styles.buttonHalf}>
          <Button label="Accept" onPress={onAccept} />
        </View>
      </View>
    </Banner>
  );
}

const makeStyles = (colors: ColorScheme) =>
  StyleSheet.create({
    details: { ...typography.small, color: colors.textSecondary },
    buttonRow: { flexDirection: 'row', gap: spacing.sm },
    buttonHalf: { flex: 1 },
  });
