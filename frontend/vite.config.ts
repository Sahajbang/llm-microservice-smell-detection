import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // 127.0.0.1, not localhost: on hosts where Node resolves "localhost"
      // to ::1 only, the proxy target must match uvicorn's actual
      // IPv4-only bind address or every request 502s.
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
