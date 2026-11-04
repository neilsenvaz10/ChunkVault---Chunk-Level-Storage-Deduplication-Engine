import { useCallback, useEffect, useRef, useState } from 'react'
import { getHealth } from '../services/api'

// Generic fetch-on-mount hook; optional polling interval in ms.
export function useApi(fetcher, intervalMs) {
  const [state, setState] = useState({ data: null, error: null, loading: true })
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const load = useCallback(async () => {
    try {
      const data = await fetcherRef.current()
      setState({ data, error: null, loading: false })
    } catch (error) {
      setState({ data: null, error, loading: false })
    }
  }, [])

  useEffect(() => {
    load()
    if (!intervalMs) return undefined
    const id = setInterval(load, intervalMs)
    return () => clearInterval(id)
  }, [load, intervalMs])

  return { ...state, reload: load }
}

export const useHealth = () => useApi(getHealth, 10000)
