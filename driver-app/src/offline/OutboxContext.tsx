import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import { useAuth } from '../auth/AuthContext';
import { outboxManager } from './outboxManager';
import type { OutboxItem } from './types';

const OutboxContext = createContext<OutboxItem[]>([]);

export function OutboxProvider({ children }: { children: ReactNode }) {
  const { isSignedIn } = useAuth();
  const [items, setItems] = useState<OutboxItem[]>([]);

  useEffect(() => {
    if (!isSignedIn) return;
    outboxManager.init();
    return outboxManager.subscribe(setItems);
  }, [isSignedIn]);

  return <OutboxContext.Provider value={items}>{children}</OutboxContext.Provider>;
}

export function useOutboxItems(): OutboxItem[] {
  return useContext(OutboxContext);
}

// How many actions are still queued for a given stop - drives
// SyncStatusPill so a driver in a dead zone can see their tap was
// recorded even though it hasn't reached the server yet.
export function useOutboxPending(stopId: string): OutboxItem[] {
  const items = useOutboxItems();
  return useMemo(() => items.filter((i) => i.stopId === stopId), [items, stopId]);
}
