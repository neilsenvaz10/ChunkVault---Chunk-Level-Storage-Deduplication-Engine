import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development, /api is proxied to the FastAPI backend so the browser
// talks to a single origin. Set VITE_API_BASE_URL to bypass the proxy
// (the backend then relies on its CORS configuration).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
