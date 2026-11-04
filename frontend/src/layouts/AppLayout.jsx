import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { PageHeader, StatusBadge } from '../components/ui'
import { useHealth } from '../hooks/useApi'
import { MODULES, moduleFor } from '../utils/navigation'

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
  const { pathname } = useLocation()
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
    <div className={`shell ${collapsed ? 'is-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-name">ChunkVault</span>
          <small>Chunk-level deduplication</small>
        </div>
        <nav aria-label="Primary">
          {MODULES.map((m) => (
            <NavLink key={m.path} to={m.path} end={m.path === '/'} title={m.label}>
              <span className="nav-label">{m.label}</span>
              {!m.implemented && <i title={`Planned for phase ${m.phase}`}>P{m.phase}</i>}
            </NavLink>
          ))}
        </nav>
        <button className="collapse-btn" onClick={toggle} aria-expanded={!collapsed}
                aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? '»' : '« Collapse'}
        </button>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="muted">Single-machine prototype · sequential writes</span>
          <HealthIndicator />
        </header>
        <main className="content">
          {mod && (
            <PageHeader
              title={mod.label}
              description={mod.description}
              status={!mod.implemented && <StatusBadge tone="warn">Not implemented · Phase {mod.phase}</StatusBadge>}
            />
          )}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
