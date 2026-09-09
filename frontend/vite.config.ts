import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    allowedHosts: ['main-pc'],
    fs: {
      deny: [
        '.env',
        '.env.*',
        '*.{crt,pem}',
        '**/.git/**',
        '**/.credentials.json',
        '**/.experiment/**',
        '**/data/**',
        '**/.pi/**',
      ],
    },
    proxy: {
      '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true },
      '/doc': { target: 'http://127.0.0.1:8787', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
  },
});
