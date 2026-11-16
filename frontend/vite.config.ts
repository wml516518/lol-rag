import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// 开发环境下 /apps/lol-rag/api 转发到本机 FastAPI（见 docs/api-contract.md 第 6 节）。
// 生产环境由 Nginx 提供 /apps/lol-rag/ 静态文件并转发应用 API。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/apps/lol-rag/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/apps\/lol-rag/, '')
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  test: {
    environment: 'jsdom',
    globals: true,
    css: true,
    setupFiles: './src/test-setup.ts'
  }
});
