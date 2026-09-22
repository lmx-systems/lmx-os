import { useCallback, useEffect, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';

import { api } from '../api/client';
import { stopStopGeofencing, syncStopGeofences } from '../location/stopGeofences';
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
    try {
      const fetchedRoute = await api.getMyRoute();
      setRoute(fetchedRoute);
      setOffers(fetchedRoute ? [] : await api.getMyOffers());
    } catch {
      // A transient network blip (e.g. backend restarting) shouldn't
      // surface as an unhandled promise rejection from the background
      // poll below - the next successful poll/focus naturally recovers,
      // same as the pre-redesign HomeScreen's poll had no error handling
      // either. useFocusEffect's caller still sees this resolve normally.
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      refresh().finally(() => setLoading(false));
    }, [refresh]),
  );

  useEffect(() => {
    // Only worth polling for new offers while idle (no active route),
    // online, and foregrounded - a backgrounded driver relies on the push
    // notification sent at offer-creation time instead
    // (notifications/registerForPushNotifications.ts, docs/ROADMAP.md A1);
    // tapping it brings this screen into focus, which useFocusEffect above
    // already refreshes on its own.
    if (!isForeground || !isOnline || route) return;
    const id = setInterval(refresh, OFFER_POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [isForeground, isOnline, route, refresh]);

  // Keep the geofence window on the stops that still need measuring (DRV-1).
  // Runs on every route change rather than once per shift, because the window
  // has to advance: iOS monitors 20 regions at most and a real route is longer
  // than that, so stops are registered a rolling batch at a time as earlier
  // ones complete. A mid-route insertion (the optimizer can add a stop to an
  // active route) lands here for the same reason.
  // The warehouse fence is registered whenever the driver is on duty, with or
  // without a route (DRV-3). A driver back in the yard with nothing assigned is
  // exactly the turnaround worth measuring, and tearing every region down on
  // "no route" is precisely when a route-only fence would miss it.
  const hub =
    isOnline && profile?.hub_lat != null && profile?.hub_lng != null
      ? { id: profile.hub_id, lat: profile.hub_lat, lng: profile.hub_lng }
      : null;

  useEffect(() => {
    if (!route && !hub) {
      void stopStopGeofencing();
      return;
    }
    void syncStopGeofences(route?.stops ?? [], hub);
  }, [route, hub?.id, hub?.lat, hub?.lng]);

  return { route, offers, loading, isOnline, refresh, setRoute };
}
