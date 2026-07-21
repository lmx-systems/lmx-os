import { useCallback, useEffect, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';

import { api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { useAppForeground } from '../hooks/useAppForeground';
import type { JobOffer, Route } from '../api/types';

const OFFER_POLL_INTERVAL_MS = 8000;

// One data source for TodayRouteScreen, replacing what used to be three
// separate useFocusEffect/polling lifecycles spread across HomeScreen,
// AvailableJobsScreen, and ActiveRouteScreen.
export function useTodayRoute() {
  const { profile } = useAuth();
  // Derived from profile.status, not separate state - profile can be
  // refreshed by paths other than this screen's own toggle, and a
  // standalone isOnline would silently drift out of sync with the
  // server's actual view whenever that happens.
  const isOnline = profile?.status === 'available';
  const isForeground = useAppForeground();
  const [route, setRoute] = useState<Route | null>(null);
  const [offers, setOffers] = useState<JobOffer[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const fetchedRoute = await api.getMyRoute();
    setRoute(fetchedRoute);
    setOffers(fetchedRoute ? [] : await api.getMyOffers());
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      refresh().finally(() => setLoading(false));
    }, [refresh]),
  );

  useEffect(() => {
    // Only worth polling for new offers while idle (no active route),
    // online, and foregrounded - also paused while backgrounded, since a
    // driver can stay marked online for a whole shift with the phone in
    // their pocket and an offer can't be meaningfully acted on without a
    // push-notification system (which doesn't exist yet) while the app
    // isn't in the foreground anyway.
    if (!isForeground || !isOnline || route) return;
    const id = setInterval(refresh, OFFER_POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isForeground, isOnline, route, refresh]);

  return { route, offers, loading, isOnline, refresh, setRoute };
}
