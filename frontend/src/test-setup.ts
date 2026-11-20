import '@testing-library/jest-dom/vitest'

// jsdom 没有 ResizeObserver / matchMedia，antd 浮层与响应式组件需要；仅测试环境使用的空实现。
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver
window.matchMedia ??= ((query: string) => ({
  matches: false, media: query, onchange: null,
  addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia
