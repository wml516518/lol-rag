import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// 开发环境下 /api 转发到本机 FastAPI（见 docs/api-contract.md 第 6 节）。
// 生产环境由 frontend/netlify.toml 的 redirect 代理到 Render，前端代码始终请求相对路径。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
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
