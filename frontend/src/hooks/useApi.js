import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'

// Fetch-on-mount with cancellation. fetcher(signal) is re-run when `deps` change or
// reload() is called; aborted/stale responses are ignored; optional polling interval.
export function useApi(fetcher, deps = [], intervalMs = 0) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const controllerRef = useRef(null)

  const load = useCallback(async ({ silent = false } = {}) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    if (!silent) setState((s) => ({ ...s, loading: true }))
    try {
      const data = await fetcherRef.current(controller.signal)
      if (!controller.signal.aborted) setState({ data, error: null, loading: false })
    } catch (error) {
      if (error?.name === 'AbortError' || controller.signal.aborted) return
      setState({ data: null, error, loading: false })
    }
  }, [])

  useEffect(() => {
    load()
    const id = intervalMs ? setInterval(() => load({ silent: true }), intervalMs) : null
    return () => {
      if (id) clearInterval(id)
      controllerRef.current?.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, intervalMs, ...deps])

  return { ...state, reload: load }
}

export const useHealth = () => useApi((signal) => api.health(signal), [], 10000)
