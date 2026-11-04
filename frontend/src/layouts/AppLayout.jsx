import { NavLink, Outlet } from 'react-router-dom'
import StatusBadge from '../components/StatusBadge'
import { useHealth } from '../hooks/useApi'
import { MODULES } from '../utils/navigation'

function HealthPill() {
  const { data, error, loading } = useHealth()
  if (loading) return <StatusBadge>Checking backend…</StatusBadge>
  if (error) return <StatusBadge tone="error">Backend unreachable</StatusBadge>
  return data.status === 'ok' ? (
    <StatusBadge tone="ok">Backend healthy</StatusBadge>
  ) : (
    <StatusBadge tone="warn">Backend degraded</StatusBadge>
  )
}

export default function AppLayout() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          ChunkVault <small>Phase 1 UI shell · backend engine in Phase 2</small>
        </div>
        <nav>
          {MODULES.map((m) => (
            <NavLink key={m.path} to={m.path} end={m.path === '/'}>
              <span>{m.label}</span>
              {!m.implemented && <i title={`Planned for phase ${m.phase}`}>P{m.phase}</i>}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <span>Chunk-level storage deduplication engine</span>
          <HealthPill />
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
