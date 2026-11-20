import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the API runs on :8000; in Docker, nginx proxies /api to the api container.
const api = process.env.API_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': api } },
  preview: { port: 4173, proxy: { '/api': api } },
});
