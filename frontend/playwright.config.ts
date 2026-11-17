import { defineConfig } from '@playwright/test'

// 布局冒烟：用 vite preview 托管构建产物，/api/shell/status 在用例里拦截。
// 本地用已安装的 Chrome，CI 用 playwright 自带的 chromium。
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4173', ...(process.env.CI ? {} : { channel: 'chrome' }) },
  webServer: {
    command: 'npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173/app/',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
