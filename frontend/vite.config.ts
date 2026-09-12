import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    // In dev, run `npm run dev` alongside `uvicorn app.main:app --reload`
    // (default port 8000) and proxy API calls to it, so the SPA and API
    // share an origin the same way they do in the production container.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
