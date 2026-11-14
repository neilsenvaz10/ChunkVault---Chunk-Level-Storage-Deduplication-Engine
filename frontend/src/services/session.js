// The demo token lives in this module's memory and nowhere else: not localStorage, sessionStorage, cookies, IndexedDB, the URL or the
// build. A page reload forgets it, on purpose. It is never logged, never part of an error message, and only ever read by services/api.js
// to build the Authorization header of a request to the backend.

let token = null
const listeners = new Set()
const emit = () => listeners.forEach((l) => l())

export function setToken(value) {
  const t = typeof value === 'string' ? value.trim() : ''
  token = t || null
  emit()
}

export const clearToken = () => setToken('')
export const hasToken = () => token !== null
export const getToken = () => token // for services/api.js only

// useSyncExternalStore hooks: components learn only WHETHER a token is held, never its value.
export const subscribe = (l) => {
  listeners.add(l)
  return () => listeners.delete(l)
}
export const snapshot = () => token !== null

// Remove any occurrence of the token from text that is about to be shown or logged (defence in depth: a server message that
// echoed it back, or a browser error that quoted a header).
export function redact(text) {
  if (!token || typeof text !== 'string') return text
  return text.split(token).join('[token hidden]')
}
