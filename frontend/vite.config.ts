import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// 构建产物输出到 Python 包内，由 hatch 以 artifacts 方式打进 wheel（设计文档 §12）。
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  build: { outDir: '../src/qsys/webapp/spa_dist', emptyOutDir: true },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'], include: ['src/**/*.test.{ts,tsx}'] },
})
