import { useEffect, useRef, useState } from 'react'

interface PollingState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  refetchNow: () => void
}

/**
 * Polls `fetcher` every `intervalMs` while `enabled` is true. Re-starts the
 * poll loop whenever `deps` change (e.g. the selected hub_id) rather than
 * accumulating stale intervals. A manual `refetchNow` is exposed so action
 * buttons (e.g. "run optimizer cycle") can force an immediate refresh
 * instead of waiting for the next tick.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[],
  enabled = true,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const [refetchToken, setRefetchToken] = useState(0)

  // Deliberately separate from the fetch effect below and keyed only on
  // `deps` (not `refetchToken`): clears out the previous poll target's data
  // the moment `deps` changes (e.g. the selected hub_id) so a stale result
  // never renders as if it belongs to the new target while its first fetch
  // is still in flight. A manual refetchNow() intentionally does NOT clear
  // data - that's a refresh of the same target, not a target change.
  useEffect(() => {
    setData(null)
    setError(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)

    const runFetch = async () => {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    runFetch()
    const id = setInterval(runFetch, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, enabled, refetchToken, ...deps])

  return { data, error, loading, refetchNow: () => setRefetchToken((t) => t + 1) }
}
