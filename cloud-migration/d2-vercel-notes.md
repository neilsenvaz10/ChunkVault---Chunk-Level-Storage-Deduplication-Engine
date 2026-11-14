# D2: Vercel deployment settings (frontend)

## Project settings (Vercel dashboard, "Import Project" from the Git repository)

| Setting | Value |
|---|---|
| Framework Preset | Other (`vercel.json` sets `framework: null`) |
| **Root Directory** | **repository root** (leave empty / `./`). Do NOT set it to `frontend`. |
| Install Command | `npm ci --prefix frontend` (from `vercel.json`) |
| Build Command | `npm run build --prefix frontend` (from `vercel.json`) |
| Output Directory | `frontend/dist` (from `vercel.json`) |
| Node.js version | 20.x or newer (24.x tested locally) |

**Why the repository root.** The build reads files outside `frontend/`: `docs/**/*.md` (the Documentation and ADR pages, via
`import.meta.glob`) and `backend/app/database/schema.sql` (the reference-count walkthrough). With Root Directory = `frontend`, those files are
only available if "Include source files outside of the Root Directory in the Build Step" is on; building from the root does not depend on that
setting. A clean checkout (git-tracked and untracked-not-ignored files, minus `.vercelignore`) was built with exactly these commands.

## Environment variables (Production AND Preview, set in the Vercel dashboard)

| Name | Value | Notes |
|---|---|---|
| `VITE_CHUNKVAULT_MODE` | `cloud` | anything else (or unset) builds the local-mode UI |
| `VITE_API_BASE_URL` | `https://<your-render-service>.onrender.com` | https only, no trailing path, no credentials |

These two are the ONLY variables the frontend reads, and both end up in the public bundle by design. **Never** add the demo token, admin token,
Supabase keys or database URLs to Vercel: every `VITE_*` variable is inlined into JavaScript that any visitor can read. (Tests build the app with
sentinel secrets in the environment and scan the output.)

## Backend side (Render) for this to work
* `CHUNKVAULT_CORS_ORIGINS` = the exact production origin (`https://<project>.vercel.app` or the custom domain). Preview URLs are not allowed.
* CORS already allows `Authorization`, exposes `X-ChunkVault-SHA256`, `Content-Disposition` and `Retry-After`.

## What `vercel.json` does
* One rewrite: every path that is not `/api/...`, not `/assets/...` and has no file extension serves `/index.html` (BrowserRouter deep links and
  refresh). Real files win first; a missing asset or `/api/...` path stays a 404 instead of silently returning HTML.
* Security headers: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive Permissions-Policy, COOP, and a CSP with
  no inline or eval scripts (`connect-src 'self' https://chunkvault-api.onrender.com`: this site plus the one verified backend origin; if the backend URL ever
  changes, update this header together with `VITE_API_BASE_URL`). Hashed assets are cached immutably; `index.html` is `no-cache`.
* Verified locally by serving a cloud-mode build with these headers and rewrites in a browser: pages, documentation and the Mermaid diagrams
  render with no CSP violations.

## Manual checks after the first deploy (not possible before)
1. Open `/files`, press refresh: the page must load (not 404). Open `/assets/nope.js`: must be 404.
2. With the Render service asleep: the "waking up" banner appears and clears itself; the health badge turns green.
3. Enter the demo token: upload a small synthetic file, download it (check the saved file), delete it. In the browser network panel each of
   these shows `Authorization` only on requests to the Render origin, and the token is not in any URL.
4. Reload: the token field is empty again.
5. A wrong token: friendly 401 message, token field returns. Ten wrong tries: 429 with a wait time.
6. Reliability, Capacity Simulation, benchmark runner, fault lab: marked/replaced by "not available in the public demo".
7. Browser console: no CSP violations (adjust `connect-src` there if you tighten it).
