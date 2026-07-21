import EventSource from 'react-native-sse';

import { API_BASE_URL, getAuthToken } from '../api/client';

export interface RouteChangeEvent {
  type: 'route_updated';
  route_id: string;
  plan_version: number;
  change: string;
  affected_stop_ids: string[];
  message: string;
  occurred_at: string;
}

// react-native-sse over a hand-rolled fetch+ReadableStream reader - it
// parses SSE over RN's XHR progressive readyState, which works on both
// iOS and Android without native linking (safe under Expo Go), whereas
// RN's fetch doesn't reliably support chunked streaming the way a browser
// does. Also, unlike the browser EventSource API, this client can send a
// custom Authorization header - matches every other endpoint's Bearer-only
// convention instead of needing a token-in-query-string workaround.
export function connectRouteEvents(onEvent: (event: RouteChangeEvent) => void): () => void {
  const token = getAuthToken();
  const es = new EventSource<'route_updated'>(`${API_BASE_URL}/driver/me/route-events`, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });

  es.addEventListener('route_updated', (event) => {
    if (!event.data) return;
    try {
      onEvent(JSON.parse(event.data) as RouteChangeEvent);
    } catch {
      // Malformed payload - ignore; the next real event, or a manual
      // pull-to-refresh, recovers. GET /driver/me/route stays the source
      // of truth regardless.
    }
  });

  return () => es.close();
}
