import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import path from 'node:path';

// Built into ../static/app and COMMITTED, so Render's Python runtime needs no Node step.
// Flask serves index.html at /<url_code>; assets live under /static/app/.
export default defineConfig({
  plugins: [react()],
  base: '/static/app/',
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
  worker: { format: 'es' },
  build: { outDir: '../static/app', emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 1500 },
  server: { proxy: { '^/[A-Za-z0-9]{6,12}/(app-config|clean-upload|files|status|submit-intent|submit-finalize)': 'http://127.0.0.1:8113' } },
});
