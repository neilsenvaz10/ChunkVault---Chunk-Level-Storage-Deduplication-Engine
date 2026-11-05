import { Suspense, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import Icon from '../components/Icon'
import { PageHeader, StatusBadge } from '../components/ui'
import { useHealth } from '../hooks/useApi'
import { NAV_GROUPS, moduleFor } from '../utils/navigation'

function readCollapsed() {
  try {
    return window.localStorage.getItem('chunkvault.sidebar') === 'collapsed'
  } catch {
    return false
  }
}

export function HealthIndicator() {
  const { data, error, loading } = useHealth()
  if (loading && !data) return <StatusBadge>Checking backend…</StatusBadge>
  if (error) return <StatusBadge tone="error">Backend unreachable</StatusBadge>
  return data.status === 'ok'
    ? <StatusBadge tone="ok">Backend healthy</StatusBadge>
    : <StatusBadge tone="warn">Backend degraded</StatusBadge>
}

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [menuOpen, setMenuOpen] = useState(false) // small screens only
  const { pathname } = useLocation()
  useEffect(() => setMenuOpen(false), [pathname])
  const mod = moduleFor(pathname)

  const toggle = () => {
    setCollapsed((c) => {
      try {
        window.localStorage.setItem('chunkvault.sidebar', c ? 'open' : 'collapsed')
      } catch {
        // preference is optional
      }
      return !c
    })
  }

  return (
    <div className={`shell ${collapsed ? 'is-collapsed' : ''} ${menuOpen ? 'menu-open' : ''}`}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside className="sidebar" id="primary-nav">
        <div className="brand">
          <span className="brand-mark"><Icon name="logo" size={18} className="" /></span>
          <span className="brand-text">
            <span className="brand-name">ChunkVault</span>
            <small>Chunk-level deduplication</small>
          </span>
        </div>
        <nav aria-label="Primary">
          {NAV_GROUPS.map((g) => (
            <div className="nav-group" key={g.title}>
              <div className="nav-group-title" aria-hidden="true">{g.title}</div>
              {g.items.map((m) => (
                <NavLink key={m.path} to={m.path} end={m.path === '/'} title={m.label}>
                  <Icon name={m.icon} />
                  <span className="nav-label">{m.label}</span>
                  {!m.implemented && <i title={`Planned for phase ${m.phase}`}>P{m.phase}</i>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <button className="collapse-btn" onClick={toggle} aria-expanded={!collapsed}
                aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? '»' : '« Collapse'}
        </button>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <button className="menu-btn" onClick={() => setMenuOpen((o) => !o)} aria-expanded={menuOpen} aria-controls="primary-nav"
                    aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}>
              <Icon name="menu" size={16} className="" /> Menu
            </button>
            {mod && (
              <span className="crumbs" aria-label="Current section">
                <span>{mod.group}</span><span aria-hidden="true">/</span><strong>{mod.label}</strong>
              </span>
            )}
          </div>
          <div className="topbar-right">
            <span className="topbar-note">Prototype · single machine · sequential writes</span>
            <HealthIndicator />
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {mod && (
            <PageHeader
              title={mod.label}
              description={mod.description}
              status={!mod.implemented && <StatusBadge tone="warn">Not implemented · Phase {mod.phase}</StatusBadge>}
            />
          )}
          <Suspense fallback={<p className="muted" role="status">Loading page…</p>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  )
}
