import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development, /api is proxied to the FastAPI backend so the browser
// talks to a single origin. Set VITE_API_BASE_URL to bypass the proxy
// (the backend then relies on its CORS configuration).
// ../docs is readable so the Documentation page can render the real project docs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.CHUNKVAULT_UI_PORT || 5173),
    fs: { allow: ['..'] },
    proxy: {
      '/api': { target: process.env.CHUNKVAULT_API_URL || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    css: false,
  },
})
