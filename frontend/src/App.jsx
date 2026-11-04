import { Navigate, Route, Routes } from 'react-router-dom'
import { UploadHistoryProvider } from './context/UploadHistory'
import AppLayout from './layouts/AppLayout'
import ArchitecturePage from './pages/ArchitecturePage'
import ComparePage from './pages/ComparePage'
import Dashboard from './pages/Dashboard'
import DatabasePage from './pages/DatabasePage'
import DocsPage from './pages/DocsPage'
import FileDetailPage from './pages/FileDetailPage'
import FilesPage from './pages/FilesPage'
import PerformancePage from './pages/PerformancePage'
import ReliabilityPage from './pages/ReliabilityPage'
import SimulationPage from './pages/SimulationPage'
import TradeoffsPage from './pages/TradeoffsPage'

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
