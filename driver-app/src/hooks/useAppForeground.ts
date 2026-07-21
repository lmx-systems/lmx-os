import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

// A driver can stay marked online for a whole shift with the phone in
// their pocket - polling/streaming while backgrounded wastes battery/data
// for updates a driver can't act on anyway, so callers gate on this.
export function useAppForeground(): boolean {
  const [isForeground, setIsForeground] = useState(AppState.currentState === 'active');

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextState) => {
      setIsForeground(nextState === 'active');
    });
    return () => subscription.remove();
  }, []);

  return isForeground;
}
