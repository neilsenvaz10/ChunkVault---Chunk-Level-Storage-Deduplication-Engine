import { createContext, useCallback, useContext, useMemo, useState } from 'react'

// Upload-time chunk decisions (new / reused / ...) are facts about ONE upload and are not
// stored by the backend. They are kept here (and in sessionStorage) only for uploads made
// in this browser session, so the UI never has to infer them for historical files.

const KEY = 'chunkvault.uploadOutcomes.v1'
const Ctx = createContext(null)

function load() {
  try {
    return JSON.parse(window.sessionStorage.getItem(KEY)) || {}
  } catch {
    return {}
  }
}

export function UploadHistoryProvider({ children }) {
  const [byFile, setByFile] = useState(load)

  const record = useCallback((result) => {
    setByFile((prev) => {
      const next = { ...prev, [result.file_id]: result }
      try {
        window.sessionStorage.setItem(KEY, JSON.stringify(next))
      } catch {
        // storage unavailable: the in-memory copy still works
      }
      return next
    })
  }, [])

  const forget = useCallback((fileId) => {
    setByFile((prev) => {
      const { [fileId]: _removed, ...rest } = prev
      try {
        window.sessionStorage.setItem(KEY, JSON.stringify(rest))
      } catch {
        // ignore
      }
      return rest
    })
  }, [])

  const value = useMemo(() => ({ byFile, record, forget }), [byFile, record, forget])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useUploadHistory() {
  const v = useContext(Ctx)
  if (!v) return { byFile: {}, record: () => {}, forget: () => {} }
  return v
}
