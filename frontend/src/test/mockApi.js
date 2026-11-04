import { vi } from 'vitest'
import * as fx from './fixtures'

// Replace every network function with a mock; setDefaults() gives fixture answers.
export function mockModule(actual) {
  return {
    ...actual,
    api: {
      health: vi.fn(), plannedEndpoints: vi.fn(), storageStats: vi.fn(), listFiles: vi.fn(), getFile: vi.fn(),
      deleteFile: vi.fn(), listChunks: vi.fn(), getChunk: vi.fn(), inspectSchema: vi.fn(), inspectRows: vi.fn(),
      audit: vi.fn(), reconcile: vi.fn(), systemConfig: vi.fn(), chunkingDemo: vi.fn(), openapi: vi.fn(),
    },
    uploadFile: vi.fn(),
    downloadFile: vi.fn(),
  }
}

export function setDefaults(api) {
  Object.values(api).forEach((f) => f.mockReset())
  api.health.mockResolvedValue(fx.health)
  api.plannedEndpoints.mockResolvedValue(fx.planned)
  api.storageStats.mockResolvedValue(fx.stats)
  api.listFiles.mockResolvedValue(fx.fileList)
  api.getFile.mockImplementation(async (id) => (id === fx.fileB.file_id ? fx.detailB : fx.detailA))
  api.getChunk.mockResolvedValue(fx.chunkDetail)
  api.inspectSchema.mockResolvedValue(fx.schema)
  api.inspectRows.mockResolvedValue(fx.fileRows)
  api.systemConfig.mockResolvedValue(fx.systemConfig)
  api.chunkingDemo.mockResolvedValue(fx.chunkingDemo)
  api.openapi.mockResolvedValue(fx.openapi)
  api.audit.mockResolvedValue(fx.healthyAudit)
  api.deleteFile.mockResolvedValue({ file_id: fx.fileA.file_id, chunks_released: 3, chunks_reclaimed: 1, bytes_reclaimed: 10000, cleanup_failed: 0 })
}
