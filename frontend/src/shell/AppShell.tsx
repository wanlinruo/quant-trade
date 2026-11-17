import { useEffect, useMemo, useRef, useState } from 'react'
import { Layout, Menu, Modal } from 'antd'
import { useShellStatus } from '../api/shell'
import { usePalette } from '../theme/context'
import { KillBanner } from './KillBanner'
import { NAV, pageByKey, DEFAULT_PAGE } from './nav'
import { Placeholder } from './Placeholder'
import { navigate, useRoute } from './router'
import { SearchPalette } from './SearchPalette'
import { createShortcutHandler, HELP } from './shortcuts'
import { useTheme } from './ThemeProvider'
import { TopBar } from './TopBar'
import './shell.css'

const SIDER_KEY = 'qs-sider'
const RECENT_KEY = 'qs-recent'

function readRecent(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(v) ? v.filter((k) => typeof k === 'string' && pageByKey(k)) : []
  } catch { return [] }
}

export function AppShell() {
  const p = usePalette()
  const theme = useTheme()
  const route = useRoute()
  const page = pageByKey(route) ?? pageByKey(DEFAULT_PAGE)!
  const status = useShellStatus()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(SIDER_KEY) === '1')
  const [search, setSearch] = useState(false)
  const [help, setHelp] = useState(false)
  const [recent, setRecent] = useState<string[]>(readRecent)
  // 顶栏与停机闸横幅一起吸顶；侧栏按其实际高度下移，横幅出现或换行时不遮住导航。
  const topRef = useRef<HTMLDivElement>(null)
  const [topH, setTopH] = useState(44)
  useEffect(() => {
    const el = topRef.current
    if (!el) return
    const update = () => setTopH(Math.round(el.getBoundingClientRect().height))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => { localStorage.setItem(SIDER_KEY, collapsed ? '1' : '0') }, [collapsed])
  useEffect(() => {
    document.title = `${page.label} · qsys`
    setRecent((r) => {
      const n = [page.key, ...r.filter((x) => x !== page.key)].slice(0, 5)
      localStorage.setItem(RECENT_KEY, JSON.stringify(n))
      return n
    })
  }, [page.key, page.label])

  const { refresh } = status
  useEffect(() => {
    const f = createShortcutHandler({
      navigate, openSearch: () => setSearch(true), openHelp: () => setHelp(true),
      toggleSider: () => setCollapsed((c) => !c), toggleTheme: theme.toggle, refresh,
    })
    addEventListener('keydown', f)
    return () => removeEventListener('keydown', f)
  }, [theme.toggle, refresh])

  const items = useMemo(() => NAV.map((g) => ({
    type: 'group' as const, label: g.group,
    children: g.pages.map((x) => ({ key: x.key, label: x.label })),
  })), [])

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <div ref={topRef} style={{ position: 'sticky', top: 0, zIndex: 10 }}>
        <Layout.Header style={{ borderBottom: `1px solid ${p.border}`, lineHeight: 'normal' }}>
          <TopBar status={status.data} error={status.error} collapsed={collapsed} onOpenSearch={() => setSearch(true)} onOpenHelp={() => setHelp(true)} />
        </Layout.Header>
        <KillBanner status={status.data} />
      </div>
      <Layout>
        <Layout.Sider width={196} collapsible collapsed={collapsed} onCollapse={setCollapsed} collapsedWidth={56}
          style={{ borderRight: `1px solid ${p.border}`, position: 'sticky', top: topH, height: `calc(100vh - ${topH}px)`, overflow: 'auto' }}>
          <Menu mode="inline" selectedKeys={[page.key]} items={items} onClick={(e) => navigate(e.key)} style={{ borderInlineEnd: 'none', paddingTop: 4 }} />
        </Layout.Sider>
        <Layout.Content style={{ padding: '12px 16px 32px', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, minHeight: 28 }}>
            <span style={{ color: p.muted, fontSize: 12 }}>{page.group} /</span>
            <h1 style={{ color: p.head, fontSize: 18, fontWeight: 600, margin: 0 }}>{page.label}</h1>
          </div>
          <div key={page.key} className="qs-page"><Placeholder page={page} /></div>
        </Layout.Content>
      </Layout>
      <SearchPalette open={search} recent={recent} onClose={() => setSearch(false)} onPick={navigate} />
      <Modal open={help} onCancel={() => setHelp(false)} footer={null} title="快捷键" width={420}>
        {HELP.map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: `1px solid ${p.border}` }}>
            <span>{v}</span><kbd className="qs-kbd">{k}</kbd>
          </div>
        ))}
      </Modal>
    </Layout>
  )
}
