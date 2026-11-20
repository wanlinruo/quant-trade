import { pageByKey } from './nav'

/** 全局快捷键（设计文档 §5.6）：? 帮助、G+字母跳页、[ 收起侧栏、T 明暗、R 刷新、⌘K 或 / 搜索。 */
export interface ShortcutActions {
  navigate(key: string): void
  openSearch(): void
  openHelp(): void
  toggleSider(): void
  toggleTheme(): void
  refresh(): void
}

export const GOTO: Record<string, string> = {
  t: 'today', a: 'alerts', l: 'live', o: 'orders', c: 'recon', u: 'runs', p: 'paper',
  m: 'market', b: 'board', s: 'services', n: 'nodes', v: 'releases',
}

export const HELP: [string, string][] = [
  ['⌘K 或 /', '搜索'],
  ['?', '快捷键帮助'],
  ...Object.entries(GOTO).map(([k, page]) => [`G ${k.toUpperCase()}`, `跳到${pageByKey(page)?.label ?? page}`] as [string, string]),
  ['[', '收起 / 展开侧栏'],
  ['T', '切换明暗'],
  ['R', '刷新'],
  ['Esc', '关闭弹窗或抽屉'],
]

const EDITABLE = 'input, textarea, select, [contenteditable="true"], .ant-select, .ant-modal, .ant-drawer'

export function createShortcutHandler(a: ShortcutActions, now: () => number = Date.now) {
  let gAt = -Infinity
  return (e: KeyboardEvent) => {
    const k = e.key
    if ((e.metaKey || e.ctrlKey) && k.toLowerCase() === 'k') { e.preventDefault(); a.openSearch(); return }
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const t = e.target as Element | null
    if (t && typeof t.closest === 'function' && t.closest(EDITABLE)) return
    const lower = k.toLowerCase()
    if (now() - gAt < 1000) {
      gAt = -Infinity
      const to = GOTO[lower]
      if (to) { a.navigate(to); return }
    }
    if (lower === 'g') gAt = now()
    else if (k === '/') { e.preventDefault(); a.openSearch() }
    else if (k === '?') a.openHelp()
    else if (k === '[') a.toggleSider()
    else if (lower === 't') a.toggleTheme()
    else if (lower === 'r') a.refresh()
  }
}
