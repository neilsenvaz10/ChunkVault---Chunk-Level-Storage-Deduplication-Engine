import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './layouts/AppLayout'
import Dashboard from './pages/Dashboard'
import {
  CapacitySimulation, DatabaseExplorer, Documentation, FileStorage,
  Performance, Reliability, SystemArchitecture, Tradeoffs,
} from './pages/ModulePages'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="files" element={<FileStorage />} />
        <Route path="architecture" element={<SystemArchitecture />} />
        <Route path="database" element={<DatabaseExplorer />} />
        <Route path="reliability" element={<Reliability />} />
        <Route path="performance" element={<Performance />} />
        <Route path="simulation" element={<CapacitySimulation />} />
        <Route path="tradeoffs" element={<Tradeoffs />} />
        <Route path="docs" element={<Documentation />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
