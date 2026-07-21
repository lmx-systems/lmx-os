import { useEffect } from 'react';

import { useAppForeground } from '../hooks/useAppForeground';
import { connectRouteEvents } from './routeEventsClient';
import type { RouteChangeEvent } from './routeEventsClient';

// Connects only while there's an active route and the app is
// foregrounded - a driver with no route doesn't need a live connection
// open, and there's no point holding one while backgrounded (a driver can
// stay marked online for a whole shift with the phone in their pocket).
export function useRouteEvents(routeId: string | null, onEvent: (event: RouteChangeEvent) => void): void {
  const isForeground = useAppForeground();

  useEffect(() => {
    if (!routeId || !isForeground) return;
    return connectRouteEvents(onEvent);
  }, [routeId, isForeground, onEvent]);
}
