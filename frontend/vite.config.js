import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `/api` is proxied to FastAPI in dev, so the browser only ever talks to one
// origin and there is no preflight in the common case. The backend still sets
// CORS headers for the deployed setup, where the two are on separate hosts.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
    allowedHosts: [
      'payroll-cardstock-mulch.ngrok-free.dev'
    ],
  },
})