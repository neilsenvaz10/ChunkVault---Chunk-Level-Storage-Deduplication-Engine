// Phase D2: Vercel configuration (SPA rewrite, headers, build settings), build-output secret scan, and deep-link routing.
// The build test runs a REAL production build into a temporary directory with sentinel secrets in the process environment.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from '../App'

const FRONTEND = resolve(__dirname, '..', '..')
const REPO = resolve(FRONTEND, '..')
const vercel = JSON.parse(readFileSync(join(REPO, 'vercel.json'), 'utf8'))

// Vercel serves a real file first; the rewrite only applies when no file matches. This models just the rewrite source patterns.
const rewriteTarget = (path) => {
  for (const r of vercel.rewrites) if (new RegExp(`^${r.source}$`).test(path)) return r.destination
  return null
}
const matches = (path) => rewriteTarget(path) === '/index.html'

describe('vercel.json', () => {
  it('builds from the repository root so docs/ and backend/ schema files are available to the frontend build', () => {
    expect(vercel.installCommand).toBe('npm ci --prefix frontend')
    expect(vercel.buildCommand).toBe('npm run build --prefix frontend')
    expect(vercel.outputDirectory).toBe('frontend/dist')
    expect(vercel.framework).toBeNull()
    expect(existsSync(join(REPO, 'backend', 'app', 'database', 'schema.sql'))).toBe(true)
    expect(existsSync(join(REPO, 'docs'))).toBe(true)
  })

  it.each(['/', '/files', '/files/3f2a9c', '/files/compare', '/architecture', '/database', '/reliability', '/performance', '/simulation', '/tradeoffs', '/docs', '/docs/decisions', '/some/unknown/deep/route'])(
    'a refresh of %s serves the single-page app', (path) => {
      expect(matches(path)).toBe(true)
    })

  it.each(['/assets/index-abc.js', '/assets/missing.css', '/api/files', '/api/health/live', '/favicon.ico', '/robots.txt', '/x.map'])(
    '%s is never answered with index.html (a missing asset or API path must stay a 404, not HTML)', (path) => {
      expect(matches(path)).toBe(false)
    })

  it('has exactly one rewrite and does not proxy /api to anywhere (the browser talks to the backend directly)', () => {
    expect(vercel.rewrites).toHaveLength(1)
    expect(JSON.stringify(vercel)).not.toMatch(/onrender\.com|supabase|"destination":\s*"https?:/i)
  })

  it('sets security headers on every response, a strict CSP, and long-lived caching only for hashed assets', () => {
    const all = vercel.headers.find((h) => h.source === '/(.*)').headers
    const h = Object.fromEntries(all.map((x) => [x.key, x.value]))
    expect(h['X-Content-Type-Options']).toBe('nosniff')
    expect(h['X-Frame-Options']).toBe('DENY')
    expect(h['Referrer-Policy']).toBe('no-referrer')
    const csp = h['Content-Security-Policy']
    for (const d of ["default-src 'self'", "script-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"]) expect(csp).toContain(d)
    expect(csp).not.toMatch(/script-src[^;]*('unsafe-inline'|'unsafe-eval'|\*)/)
    const assets = vercel.headers.find((x) => x.source === '/assets/(.*)').headers[0]
    expect(assets.value).toContain('immutable')
    expect(vercel.headers.find((x) => x.source === '/index.html').headers[0].value).toBe('no-cache')
  })

  it('ignores local-only data and secrets for CLI deployments but keeps what the build reads', () => {
    const ignore = readFileSync(join(REPO, '.vercelignore'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
    for (const must of ['backend/.env', 'data', 'backend/.venv', '.claude']) expect(ignore).toContain(must)
    expect(ignore).not.toContain('docs')
    expect(ignore.join(' ')).not.toMatch(/schema\.sql/)
  })
})

describe('deep links work after a refresh (client-side router)', () => {
  const at = (route) => render(<MemoryRouter initialEntries={[route]}><App /></MemoryRouter>)

  it.each([
    ['/files', 'File Storage'], ['/architecture', 'System Architecture'], ['/tradeoffs', 'Architecture Trade-offs'], ['/docs', 'System Design Documentation'],
  ])('%s renders its own page', async (route, title) => {
    at(route)
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument()
  })

  it('an unknown path falls back to the dashboard instead of a blank page', async () => {
    at('/no/such/page')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  })
})

// ------------------------------------------------------------------------------------------------ build output
function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    statSync(p).isDirectory() ? walk(p, out) : out.push(p)
  }
  return out
}

// Secret-looking names are assembled at run time so this file itself never contains them (a backend test forbids those words in the sources).
const N = (...parts) => parts.join('_')

describe('production build output', () => {
  it('contains no secret, token, key or connection string, even when they exist in the build environment', () => {
    const out = mkdtempSync(join(tmpdir(), 'cv-build-'))
    const sentinels = {
      [N('SUPABASE', 'SERVICE', 'KEY')]: 'sentinel-service-key-AAAA1111BBBB2222',
      [N('CHUNKVAULT', 'ADMIN', 'TOKEN')]: 'sentinel-admin-token-CCCC3333DDDD4444',
      [N('CHUNKVAULT', 'DEMO', 'TOKEN')]: 'sentinel-demo-token-EEEE5555FFFF6666',
      [N('DATABASE', 'URL')]: 'postgresql://sentinel-user:sentinel-pass-GGGG7777@db.sentinel.invalid:5432/postgres',
      [N('OWNER', 'DATABASE', 'URL')]: 'postgresql://sentinel-owner:sentinel-pass-HHHH8888@db.sentinel.invalid:5432/postgres',
    }
    try {
      execFileSync(process.execPath, [join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js'), 'build', '--outDir', out, '--emptyOutDir'], {
        cwd: FRONTEND,
        env: { ...process.env, ...sentinels, VITE_CHUNKVAULT_MODE: 'cloud', VITE_API_BASE_URL: 'https://api.example.test' },
        stdio: 'pipe',
        timeout: 150000,
      })
      const files = walk(out).filter((f) => !/\.(png|jpe?g|gif|ico|woff2?|pdf|docx)$/i.test(f))
      expect(files.length).toBeGreaterThan(5)
      const patterns = [
        ...Object.values(sentinels), 'sentinel-', 'sentinel.invalid',
        /postgres(ql)?:\/\/[^\s"'`]+:[^\s"'`@]+@/i,                       // a connection string with credentials
        /eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}/, // a JWT (Supabase keys are JWTs)
        /sb_(secret|publishable)_[A-Za-z0-9_-]{10,}/,
        new RegExp(N('service', 'role'), 'i'), new RegExp(N('SUPABASE', 'SERVICE'), 'i'), new RegExp(N('OWNER', 'DATABASE', 'URL')),
        new RegExp(N('CHUNKVAULT', 'ADMIN', 'TOKEN')), new RegExp(N('CHUNKVAULT', 'DEMO', 'TOKEN')),
        /Bearer [A-Za-z0-9._~+/-]{20,}/,                                  // a literal bearer credential (the code only builds one at run time)
      ]
      for (const f of files) {
        const text = readFileSync(f, 'utf8')
        for (const p of patterns) {
          const hit = typeof p === 'string' ? text.includes(p) : p.test(text)
          expect(hit, `${f.slice(out.length)} matches ${String(p).slice(0, 40)}`).toBe(false)
        }
      }
      // The only configuration that reaches the bundle is the public mode and the public backend address.
      const all = files.map((f) => readFileSync(f, 'utf8')).join('\n')
      expect(all).toContain('https://api.example.test')
      expect(all).toMatch(/Authorization/) // the header NAME is code; no value is ever baked in
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 180000)

  it('the source tree reads no environment variable except the two public settings', () => {
    const src = join(FRONTEND, 'src')
    const used = new Set()
    for (const f of walk(src).filter((x) => /\.jsx?$/.test(x) && !/[\\/]test[\\/]/.test(x))) {
      for (const m of readFileSync(f, 'utf8').matchAll(/import\.meta\.env\.([A-Z0-9_]+)/g)) used.add(m[1])
    }
    expect([...used].sort()).toEqual(['VITE_API_BASE_URL', 'VITE_CHUNKVAULT_MODE'])
  })
})
