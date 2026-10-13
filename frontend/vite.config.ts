import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发模式下把 /api 代理到后端：
// 前端代码里永远只写相对路径 /api/...，
// 这样开发时（vite dev）和上线后（FastAPI 托管构建产物）都不用改任何代码。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
