import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The browser calls /api/... on the Vite server (port 5173), and Vite forwards
    // it to FastAPI (port 8000) with "/api" removed: /api/events -> /events.
    // To the browser everything is on one address, so no CORS setup is needed.
    proxy: {
      '/api': {
        target: process.env.API_URL ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
