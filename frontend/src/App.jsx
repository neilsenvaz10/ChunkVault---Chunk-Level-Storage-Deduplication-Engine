import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { UploadHistoryProvider } from './context/UploadHistory'
import AppLayout from './layouts/AppLayout'
import ComparePage from './pages/ComparePage'
import Dashboard from './pages/Dashboard'
import FileDetailPage from './pages/FileDetailPage'
import FilesPage from './pages/FilesPage'
import ReliabilityPage from './pages/ReliabilityPage'

// Heavy, rarely first-visited pages load on demand (the shell, dashboard and file pages stay in the main bundle).
const ArchitecturePage = lazy(() => import('./pages/ArchitecturePage'))
const DatabasePage = lazy(() => import('./pages/DatabasePage'))
const DocsPage = lazy(() => import('./pages/DocsPage'))
const PerformancePage = lazy(() => import('./pages/PerformancePage'))
const SimulationPage = lazy(() => import('./pages/SimulationPage'))
const TradeoffsPage = lazy(() => import('./pages/TradeoffsPage'))

export default function App() {
  return (
    <UploadHistoryProvider>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Dashboard />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="files/compare" element={<ComparePage />} />
          <Route path="files/:fileId" element={<FileDetailPage />} />
          <Route path="architecture" element={<ArchitecturePage />} />
          <Route path="database" element={<DatabasePage />} />
          <Route path="reliability" element={<ReliabilityPage />} />
          <Route path="performance" element={<PerformancePage />} />
          <Route path="simulation" element={<SimulationPage />} />
          <Route path="tradeoffs" element={<TradeoffsPage />} />
          <Route path="docs" element={<DocsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </UploadHistoryProvider>
  )
}
