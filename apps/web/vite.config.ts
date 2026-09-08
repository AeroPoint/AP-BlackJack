import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The API is proxied rather than called cross-origin so the dev and production
// setups differ in as few ways as possible.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
