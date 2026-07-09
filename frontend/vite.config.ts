import { defineConfig } from 'vite';

export default defineConfig({
  envPrefix: ['VITE_', 'AUTH_'],
  server: {
    port: 5175,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
  build: { target: 'esnext' },
});
