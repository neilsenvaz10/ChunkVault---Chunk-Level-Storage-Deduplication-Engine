// Deployment mode, chosen explicitly at build time. Anything but "cloud" is the local behaviour the project always had.
//   VITE_CHUNKVAULT_MODE=cloud  -> public demo against a hosted backend (needs VITE_API_BASE_URL, asks for a demo token)
//   (unset or "local")          -> unchanged: same-origin /api via the dev proxy, no token, no banner, nothing hidden
// Values are read when called (not captured at import) so tests can switch them.
// Secrets never belong here: Vite inlines every VITE_* variable into the public bundle, so the demo token is NEVER read from the
// environment; it is typed by the visitor and kept in memory only (see services/session.js).

export function getMode() {
  return String(import.meta.env.VITE_CHUNKVAULT_MODE || 'local').trim().toLowerCase() === 'cloud' ? 'cloud' : 'local'
}

export const isCloudMode = () => getMode() === 'cloud'

export function getApiBase() {
  return String(import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/$/, '')
}

// Cloud mode must name its backend explicitly and over https (plain http only for a local development backend).
export function cloudConfigProblem() {
  if (!isCloudMode()) return null
  const base = getApiBase()
  if (!base) return 'Cloud mode needs VITE_API_BASE_URL (the https address of the backend).'
  let url
  try {
    url = new URL(base)
  } catch {
    return 'VITE_API_BASE_URL is not a valid address.'
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) return 'VITE_API_BASE_URL must be an https address.'
  if (url.username || url.password) return 'VITE_API_BASE_URL must not contain credentials.'
  return null
}
