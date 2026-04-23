import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Single source of truth: BACKEND_PORT env var (falls back to 8000).
const backendPort = process.env.BACKEND_PORT || '8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
});
