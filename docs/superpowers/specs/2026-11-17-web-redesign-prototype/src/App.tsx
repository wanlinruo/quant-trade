import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ConfigProvider, Layout, Menu, Button, Select, Modal, Input, Tooltip, Tag, Tabs, Badge, Dropdown, App as AntApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import {
  DashboardOutlined, AlertOutlined, SwapOutlined, SafetyOutlined, FileTextOutlined, ExperimentOutlined,
  FundOutlined, FireOutlined, CodeOutlined, HistoryOutlined, RobotOutlined, DatabaseOutlined, TableOutlined, BookOutlined,
  CheckSquareOutlined, CloudServerOutlined, ClusterOutlined, RocketOutlined, SearchOutlined, MoonOutlined, SunOutlined,
  WalletOutlined, StopOutlined, DesktopOutlined, ReloadOutlined, PauseOutlined, CaretRightOutlined, EyeOutlined,
} from '@ant-design/icons'
import { antdConfig, palette, type Mode } from './theme'
import { Num } from './ui'
import { PM, BAD, NOW_SEC, SCENE, setScene, ALERTS_OPEN0, KILL_AT } from './scene'
import { Segmented } from 'antd'
import { Today } from './pages/Today'
import { Services } from './pages/Services'
import { Backtest } from './pages/Backtest'
import { Live } from './pages/Live'
import { Orders } from './pages/Orders'
import { Recon } from './pages/Recon'
import { Runs } from './pages/Runs'
import { Paper } from './pages/Paper'
import { Market } from './pages/Market'
import { Board } from './pages/Board'
import { Strategies } from './pages/Strategies'
import { AiLimit } from './pages/AiLimit'
import { ResearchJobs } from './pages/ResearchJobs'
import { DataOverview, DataTables, Catalog, Quality } from './pages/DataPages'
import { Nodes, Releases, Alerts } from './pages/OpsPages'

type Pal = (typeof palette)[Mode]

/** 侧栏徽标：数字＝需要处理的条数；点＝有已知警告。 */
const BADGE: Record<string, { n?: number; dot?: 'warn' | 'bad' }> = BAD
  ? { live: { dot: 'bad' }, orders: { n: 1 }, recon: { dot: 'bad' }, paper: { dot: 'bad' }, runs: { dot: 'warn' }, market: { dot: 'warn' }, board: { dot: 'warn' }, quality: { dot: 'warn' }, nodes: { dot: 'bad' } }
  : PM
  ? { runs: { dot: 'warn' }, quality: { dot: 'warn' }, services: { dot: 'warn' } }
  : { orders: { n: 1 }, runs: { dot: 'warn' }, quality: { dot: 'warn' }, nodes: { dot: 'warn' } }
function useAlertsOpen() {
  const [n, setN] = useState(() => Number(sessionStorage.getItem('qs-alerts-open-' + SCENE) ?? ALERTS_OPEN0))
  useEffect(() => { const f = (e: Event) => setN((e as CustomEvent<number>).detail); addEventListener('qs-alerts', f); return () => removeEventListener('qs-alerts', f) }, [])
  return n
}

const NAV_RAW = [
  { label: '总览', children: [
    { key: 'today', icon: <DashboardOutlined />, label: '今日运行', kw: '首页 总览' },
    { key: 'alerts', icon: <AlertOutlined />, label: '告警中心', kw: '告警 通知' } ] },
  { label: '交易', children: [
    { key: 'live', icon: <WalletOutlined />, label: '实盘总览', kw: '持仓 资产 风险 额度' },
    { key: 'orders', icon: <SwapOutlined />, label: '委托与成交', kw: '订单 委托 成交 拒单' },
    { key: 'recon', icon: <SafetyOutlined />, label: '对账与风控', kw: '对账 急停 停机闸 规则' },
    { key: 'runs', icon: <FileTextOutlined />, label: '运行记录', kw: '日报 运行 事件' },
    { key: 'paper', icon: <ExperimentOutlined />, label: '模拟账户', kw: '模拟 paper' } ] },
  { label: '行情', children: [
    { key: 'market', icon: <FundOutlined />, label: '市场总览', kw: '大盘 情绪 指数' },
    { key: 'board', icon: <FireOutlined />, label: '打板看板', kw: '涨停 连板 炸板 梯队' } ] },
  { label: '研究', children: [
    { key: 'strategies', icon: <CodeOutlined />, label: '策略', kw: '策略 源码 参数' },
    { key: 'backtest', icon: <HistoryOutlined />, label: '回测', kw: '回测 研究任务' },
    { key: 'ailu', icon: <RobotOutlined />, label: 'AI 涨停', kw: '影子 榜单 模型' } ] },
  { label: '数据', children: [
    { key: 'dataov', icon: <DatabaseOutlined />, label: '数据总览', kw: '血缘 健康' },
    { key: 'tables', icon: <TableOutlined />, label: '数据表', kw: '表 字段 schema' },
    { key: 'catalog', icon: <BookOutlined />, label: '指标字典', kw: '指标 口径' },
    { key: 'quality', icon: <CheckSquareOutlined />, label: '数据质量', kw: '质检 规则' } ] },
  { label: '运维', children: [
    { key: 'services', icon: <CloudServerOutlined />, label: '服务看板', kw: '定时任务 服务' },
    { key: 'nodes', icon: <ClusterOutlined />, label: '节点与链路', kw: '节点 磁盘 CPU 隧道' },
    { key: 'releases', icon: <RocketOutlined />, label: '发布与版本', kw: '发布 部署 版本' } ] },
]
const PAGES = NAV_RAW.flatMap((g) => g.children.map((c) => ({ ...c, group: g.label })))
const LABEL: Record<string, string> = Object.fromEntries(PAGES.map((c) => [c.key, c.label]))
const GROUP: Record<string, string> = Object.fromEntries(PAGES.map((c) => [c.key, c.group]))

const SEARCH_EXTRA: { kind: string; name: string; go: string; hint?: string }[] = [
  ...['q-1116-0001', 'q-1116-0002', 'q-1116-0003', 'q-1116-0004'].map((id) => ({ kind: '委托', name: id, go: 'orders', hint: id === 'q-1116-0004' && !PM ? '▲ 状态未知' : undefined })),
  ...['daily-archive', 'tushare-sync', 'premarket-limits', 'limit-up-shadow', 'catalog-verify', 'limit-up-board', 'service-checks', 'daily-digest'].map((n) => ({ kind: '服务', name: n, go: 'services' })),
  ...['two_stage_limit_hold', 'lightgbm_price', 'next_open', 'sector_rotation'].map((n) => ({ kind: '策略', name: n, go: 'strategies' })),
  ...['tushare_daily', 'daily_basic', 'stk_limit', 'bar_minute_1m', 'limit_list_d', 'stock_st_daily', 'security_master', 'namechange', 'paid_daily', 'suspend_d', 'market_regime_daily', 'limit_up_shadow'].map((n) => ({ kind: '数据表', name: n, go: 'tables' })),
  ...['pct_chg 涨跌幅', 'circ_mv 流通市值', 'limit_up_price 涨停价', 'is_st 是否 ST', 'seal_ratio 封单比', 'prev_close 昨收'].map((n) => ({ kind: '指标', name: n, go: 'catalog' })),
  { kind: '告警', name: '委托 q-1116-0004 券商未回报', go: 'alerts', hint: PM ? '已解决' : '未处理' },
  { kind: '告警', name: '盘中调仓时点迟到 44s', go: 'alerts', hint: '未处理' },
  ...(BAD ? [{ kind: '告警', name: '停机闸触发：Redis 隧道心跳中断', go: 'alerts', hint: '✕ 未处理' }, { kind: '告警', name: '模拟账户 pa-01 / pa-02 标记出错', go: 'alerts', hint: '✕ 未处理' }] : []),
  { kind: '回测', name: 's6-2024-2026', go: 'backtest' },
  { kind: '节点', name: 'Linux 生产节点', go: 'nodes' }, { kind: '节点', name: 'Windows 执行节点', go: 'nodes' },
]

const DATES = [
  { value: '1116', label: '11-16 周一', today: true },
  { value: '1113', label: '11-13 周五' },
  { value: '1112', label: '11-12 周四' },
  { value: '1111', label: '11-11 周三' },
]

type Pref = 'system' | 'dark' | 'light'
const sysMode = (): Mode => (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')

function useHash() {
  const [h, setH] = useState(() => location.hash.slice(1) || 'today')
  useEffect(() => { const f = () => setH(location.hash.slice(1) || 'today'); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  return h
}

/** 示意时钟：从场景时刻（10:42:15 或 20:40:05）起走秒；只重绘自己。 */
const T0 = NOW_SEC
const startedAt = Date.now()
const fmtHMS = (s: number) => [s / 3600, (s % 3600) / 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, '0')).join(':')
function nowSec() { return T0 + Math.floor((Date.now() - startedAt) / 1000) }
function phaseOf(s: number) {
  if (s < 9 * 3600 + 15 * 60) return '盘前'
  if (s < 9 * 3600 + 30 * 60) return '集合竞价'
  if (s < 11 * 3600 + 30 * 60) return '盘中'
  if (s < 13 * 3600) return '午间休市'
  if (s < 15 * 3600) return '盘中'
  return '盘后'
}
function Clock({ p, history }: { p: Pal; history: boolean }) {
  const [s, setS] = useState(nowSec)
  useEffect(() => { const t = setInterval(() => setS(nowSec()), 1000); return () => clearInterval(t) }, [])
  if (history) return <span style={{ display: 'inline-flex', gap: 6, alignItems: 'baseline', whiteSpace: 'nowrap' }}><span style={{ color: p.muted, fontSize: 12 }}>收盘快照</span><Num color={p.warn}>15:00:00</Num></span>
  return <><span style={{ display: 'inline-flex', gap: 6, alignItems: 'baseline', whiteSpace: 'nowrap' }}><span className="qs-h-long" style={{ color: p.muted, fontSize: 12 }}>{phaseOf(s)}</span><Num color={p.head}>{fmtHMS(s)}</Num></span></>
}

/** 页面右上角：数据截至 + 自动刷新开关 + 手动刷新。 */
function Freshness({ p, page, history }: { p: Pal; page: string; history: boolean }) {
  const { message } = AntApp.useApp()
  const [auto, setAuto] = useState(true)
  const [at, setAt] = useState(nowSec)
  const [, force] = useState(0)
  useEffect(() => { setAt(nowSec()) }, [page])
  useEffect(() => {
    const t = setInterval(() => { if (auto && !history) setAt(nowSec() - 1); force((x) => x + 1) }, 5000)
    return () => clearInterval(t)
  }, [auto, history])
  if (history) return <span style={{ color: p.warn, fontSize: 12 }}><EyeOutlined /> 只读快照 · 不刷新</span>
  const ago = Math.max(0, nowSec() - at)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: p.muted, fontSize: 12 }}>
      <span style={{ width: 6, height: 6, borderRadius: 3, background: auto ? p.ok : p.idle, boxShadow: auto ? `0 0 0 3px ${p.ok}22` : undefined }} />
      数据截至 <Num size={12} color={p.text}>{fmtHMS(at)}</Num>
      <span>{ago <= 1 ? '刚刚' : `${ago}s 前`}</span>
      <Tooltip title={auto ? '暂停自动刷新（5s）' : '恢复自动刷新'}>
        <Button size="small" type="text" icon={auto ? <PauseOutlined /> : <CaretRightOutlined />} onClick={() => setAuto(!auto)} />
      </Tooltip>
      <Tooltip title="立即刷新（R）">
        <Button size="small" type="text" icon={<ReloadOutlined />} onClick={() => { setAt(nowSec()); message.success({ content: '已刷新', duration: 1 }) }} />
      </Tooltip>
    </span>
  )
}

/** 搜索命令面板：↑↓ 选择、回车打开、按类型分组、高亮命中。 */
function Palette({ open, onClose, p, mode, recent }: { open: boolean; onClose: () => void; p: Pal; mode: Mode; recent: string[] }) {
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => { if (!open) { setQ(''); setI(0) } }, [open])
  const all = useMemo(() => [
    ...PAGES.map((c) => ({ kind: '页面', name: c.label, go: c.key, hint: `${c.group}`, kw: c.kw })),
    ...SEARCH_EXTRA.map((x) => ({ ...x, kw: '' })),
  ], [])
  const ql = q.trim().toLowerCase()
  const items = ql
    ? all.filter((x) => (x.name + ' ' + x.kw + ' ' + x.go).toLowerCase().includes(ql)).slice(0, 14)
    : [...recent.filter((k) => LABEL[k]).map((k) => ({ kind: '最近访问', name: LABEL[k], go: k, hint: GROUP[k], kw: '' })), ...all.filter((x) => x.kind === '页面').slice(0, 6)].slice(0, 10)
  useEffect(() => { setI(0) }, [q])
  useEffect(() => { listRef.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: 'nearest' }) }, [i])
  const go = (x?: { go: string }) => { if (!x) return; location.hash = x.go; onClose() }
  const hl = (s: string) => {
    if (!ql) return s
    const k = s.toLowerCase().indexOf(ql)
    if (k < 0) return s
    return <>{s.slice(0, k)}<mark style={{ background: p.accent + '44', color: p.head, padding: 0 }}>{s.slice(k, k + ql.length)}</mark>{s.slice(k + ql.length)}</>
  }
  let lastKind = ''
  return (
    <Modal open={open} onCancel={onClose} footer={null} closable={false} width={600} styles={{ body: { padding: 0 } }} destroyOnHidden>
      <Input autoFocus size="large" variant="borderless" prefix={<SearchOutlined />} placeholder="搜索页面、委托号、服务、策略、数据表、指标…" value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(items.length - 1, x + 1)) }
          if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(0, x - 1)) }
          if (e.key === 'Enter') go(items[i])
        }}
        style={{ borderBottom: `1px solid ${p.border}`, borderRadius: 0 }} />
      <div ref={listRef} style={{ maxHeight: 400, overflow: 'auto', padding: '4px 0' }}>
        {items.length === 0 && <div style={{ padding: '24px 16px', color: p.muted, textAlign: 'center' }}>没有匹配「{q}」的结果</div>}
        {items.map((x, k) => {
          const head = x.kind !== lastKind
          lastKind = x.kind
          return (
            <div key={x.kind + x.name}>
              {head && <div style={{ padding: '8px 16px 2px', color: p.muted, fontSize: 11 }}>{x.kind}</div>}
              <div data-i={k} onClick={() => go(x)} onMouseMove={() => setI(k)}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '7px 16px', cursor: 'pointer', background: k === i ? (mode === 'dark' ? '#ffffff10' : '#0000000a') : undefined, boxShadow: k === i ? `inset 2px 0 0 ${p.accent}` : undefined }}>
                <span style={{ color: p.head, fontFamily: /^[a-z_0-9-]+$/.test(x.name.split(' ')[0]) ? 'Menlo, monospace' : undefined, fontSize: 13 }}>{hl(x.name)}</span>
                <span style={{ color: x.hint?.startsWith('▲') ? p.warn : p.muted, fontSize: 12 }}>{x.hint ?? LABEL[x.go]}{k === i && <span style={{ marginLeft: 8, opacity: .7 }}>↵</span>}</span>
              </div>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: 14, padding: '6px 16px', borderTop: `1px solid ${p.border}`, color: p.muted, fontSize: 11 }}>
        <span>↑↓ 选择</span><span>↵ 打开</span><span>Esc 关闭</span><span style={{ flex: 1 }} /><span>原型：正式版接全局索引</span>
      </div>
    </Modal>
  )
}

const KEYS: [string, string][] = [['⌘K / Ctrl K', '搜索'], ['/', '搜索'], ['[', '收起 / 展开侧栏'], ['T', '切换明暗'], ['R', '刷新当前页'], ['G 然后 T / A / L / O', '去 今日运行 / 告警 / 实盘 / 委托'], ['Esc', '关闭抽屉或弹窗'], ['?', '本帮助']]

export default function App() {
  const forced: Mode | null = location.search.includes('light') ? 'light' : location.search.includes('dark') ? 'dark' : null
  const [pref, setPref] = useState<Pref>(() => (localStorage.getItem('qs-theme') as Pref) || 'system')
  const [sys, setSys] = useState<Mode>(sysMode)
  const mode: Mode = forced ?? (pref === 'system' ? sys : pref)
  const [kill, setKill] = useState(BAD)
  const [cmd, setCmd] = useState(false)
  const [help, setHelp] = useState(false)
  const [date, setDate] = useState('1116')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('qs-sider') === '1')
  const [recent, setRecent] = useState<string[]>(() => JSON.parse(localStorage.getItem('qs-recent') || '[]'))
  const page = useHash()
  const p = palette[mode]
  const cfg = useMemo(() => antdConfig(mode), [mode])
  const history = date !== '1116'
  const alertsOpen = useAlertsOpen()
  const [btTab, setBtTab] = useState('r')
  useEffect(() => { const f = () => setBtTab('r'); addEventListener('qs-bt', f); return () => removeEventListener('qs-bt', f) }, [])

  useEffect(() => {
    const m = matchMedia('(prefers-color-scheme: light)')
    const f = () => setSys(sysMode())
    m.addEventListener('change', f); return () => m.removeEventListener('change', f)
  }, [])
  useEffect(() => { localStorage.setItem('qs-theme', pref) }, [pref])
  useEffect(() => { localStorage.setItem('qs-sider', collapsed ? '1' : '0') }, [collapsed])
  useEffect(() => {
    document.title = `${LABEL[page] ?? '今日运行'} · qsys`
    scrollTo({ top: 0 })
    if (LABEL[page]) setRecent((r) => { const n = [page, ...r.filter((x) => x !== page)].slice(0, 4); localStorage.setItem('qs-recent', JSON.stringify(n)); return n })
  }, [page])
  useEffect(() => {
    document.body.style.background = p.bg
    document.documentElement.style.setProperty('--qs-accent', p.accent)
    document.documentElement.style.setProperty('--qs-border', p.border)
    document.documentElement.style.setProperty('--qs-sel', mode === 'dark' ? '#ff983014' : '#e2711d10')
    document.documentElement.dataset.mode = mode
  }, [p, mode])

  const gPending = useRef(0)
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setCmd(true); return }
      const t = e.target as HTMLElement
      if (e.metaKey || e.ctrlKey || e.altKey || t.closest('input, textarea, [contenteditable="true"], .ant-select, .ant-modal, .ant-drawer')) return
      const k = e.key
      if (Date.now() - gPending.current < 1000) {
        const to = { t: 'today', a: 'alerts', l: 'live', o: 'orders', r: 'recon', m: 'market', b: 'board', s: 'services' }[k.toLowerCase()]
        gPending.current = 0
        if (to) { location.hash = to; return }
      }
      if (k === 'g' || k === 'G') gPending.current = Date.now()
      else if (k === '/') { e.preventDefault(); setCmd(true) }
      else if (k === '?') setHelp(true)
      else if (k === '[') setCollapsed((c) => !c)
      else if (k === 't' || k === 'T') setPref(mode === 'dark' ? 'light' : 'dark')
      else if (k === 'r' || k === 'R') (document.querySelector('[aria-label="reload"]')?.closest('button') as HTMLButtonElement | null)?.click()
    }
    addEventListener('keydown', f); return () => removeEventListener('keydown', f)
  }, [mode])

  const nav = useMemo(() => NAV_RAW.map((g) => ({
    type: 'group' as const, label: g.label,
    children: g.children.map((c) => {
      const b = c.key === 'alerts' ? { n: alertsOpen } : BADGE[c.key]
      return {
        key: c.key, icon: c.icon,
        label: (
          <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            {c.label}
            {b?.n ? <Badge count={b.n} size="small" color={p.warn} style={{ color: '#000', boxShadow: 'none' }} /> : b?.dot ? <Tooltip title="有已知警告"><span style={{ width: 6, height: 6, borderRadius: 3, background: b.dot === 'bad' ? p.bad : p.warn }} /></Tooltip> : null}
          </span>
        ),
      }
    }),
  })), [p, alertsOpen])

  const pill = (ok: boolean, okText: string, badText: string, go: string, tip: ReactNode) => (
    <Tooltip title={tip}>
      <a href={`#${go}`} className="qs-pill" style={{ display: 'inline-flex', gap: 5, alignItems: 'center', fontSize: 12, padding: '1px 8px', borderRadius: 3, border: `1px solid ${(ok ? p.ok : p.bad)}66`, color: ok ? p.ok : p.bad, background: ok ? undefined : p.bad + '18' }}>
        <span style={{ fontSize: 10 }}>{ok ? '●' : '✕'}</span>{ok ? okText : badText}
      </a>
    </Tooltip>
  )
  const stat = (k: string, v: ReactNode, go: string, tip: ReactNode) => (
    <Tooltip title={tip}><a href={`#${go}`} className="qs-stat qs-h-opt" style={{ color: p.muted, fontSize: 12 }}>{k} {v}</a></Tooltip>
  )

  const themeItems = [
    { key: 'system', icon: <DesktopOutlined />, label: `跟随系统（当前${sys === 'dark' ? '黑暗' : '白天'}）` },
    { key: 'light', icon: <SunOutlined />, label: '白天' },
    { key: 'dark', icon: <MoonOutlined />, label: '黑暗' },
  ]

  return (
    <ConfigProvider theme={cfg} locale={zhCN}>
      <AntApp>
        <Layout style={{ minHeight: '100vh' }}>
          <Layout.Header style={{ display: 'flex', alignItems: 'center', gap: 14, borderBottom: `1px solid ${p.border}`, position: 'sticky', top: 0, zIndex: 10, lineHeight: 'normal' }}>
            <a href="#today" style={{ color: p.head, fontWeight: 700, fontSize: 15, width: collapsed ? 40 : 176, transition: 'width .2s' }}>qsys</a>
            {pill(!kill, PM ? '实盘 已收盘' : '实盘运行', '实盘 仅允许减仓', 'live', kill ? '停机闸已触发：新开仓被拦截，减仓与撤单保留' : PM ? '今日已收盘，15:30 对账一致 · 下一个交易日 11-17 09:15 开盘准备' : '实盘可下单 · 限额 1 万 · 点击打开实盘总览')}
            {pill(!kill, '停机闸 未触发', '停机闸 已触发', 'recon', BAD ? `${KILL_AT} 由 watchdog 触发 · 解除走受管入口 · 点击看解除前核对清单` : '最后一次清除 11-11 09:12 · 点击打开对账与风控')}
            {stat('QMT', <span style={{ color: p.ok }}>在线</span>, 'nodes', PM ? 'Windows 执行器第 5 行策略在线（盘后待机）· 点击看节点与链路' : 'Windows 执行器第 5 行策略运行中 · 点击看节点与链路')}
            {stat('心跳', <Num color={p.ok} size={12}>3s</Num>, 'live', BAD ? '链路心跳最近一次 3 秒前（10:31:05–10:36:40 曾中断）' : '链路心跳最近一次 3 秒前，阈值 90 秒')}
            {stat('情绪', <Num color={p.warn} size={12}>{PM ? 64 : 62}</Num>, 'market', PM ? '收盘情绪温度 64（偏热）· 点击看市场总览' : '盘中情绪温度 62（偏热）· 点击看市场总览')}
            {stat('告警', <Num color={alertsOpen ? p.warn : p.ok} size={12}>{alertsOpen}</Num>, 'alerts', alertsOpen ? `${alertsOpen} 条未处理告警` : '没有未处理告警')}
            <span style={{ flex: 1 }} />
            <Button size="small" icon={<SearchOutlined />} onClick={() => setCmd(true)} className="qs-h-search" style={{ color: p.muted, display: 'flex', justifyContent: 'flex-start' }}><span className="qs-h-long">搜索</span><span className="qs-h-long" style={{ marginLeft: 'auto', opacity: .6 }}>⌘K</span></Button>
            <Tooltip title="原型演示：同一天的两个时刻。切换后整页重新加载" placement="bottom">
              <Segmented size="small" value={SCENE} onChange={(v) => setScene(v as 'am' | 'pm' | 'bad')} options={[{ value: 'am', label: <><span className="qs-h-short">盘中</span><span className="qs-h-long">盘中 10:42</span></> }, { value: 'pm', label: <><span className="qs-h-short">晚间</span><span className="qs-h-long">晚间 20:40</span></> }, { value: 'bad', label: <><span className="qs-h-short">异常</span><span className="qs-h-long">异常日 10:48</span></> }]} />
            </Tooltip>
            <Select size="small" value={date} onChange={setDate} style={{ width: 150 }} popupMatchSelectWidth={false} options={DATES.map((d) => ({ value: d.value, label: d.today ? `${d.label} · 今天` : d.label }))} />
            <Clock p={p} history={history} />
            {!BAD && <Tooltip title="演示：停机闸触发时的整条横幅（看完整异常日请切到“异常日”场景）"><Button size="small" danger={!kill} icon={<StopOutlined />} onClick={() => setKill(!kill)}>{kill ? '解除演示' : '演示停机'}</Button></Tooltip>}
            <Dropdown trigger={['click']} menu={{ items: themeItems, selectable: true, selectedKeys: [pref], onClick: (e) => setPref(e.key as Pref) }}>
              <Tooltip title="明暗主题（T）" placement="bottomRight"><Button size="small" icon={mode === 'dark' ? <MoonOutlined /> : <SunOutlined />} /></Tooltip>
            </Dropdown>
            <Tooltip title="快捷键（?）" placement="bottomRight"><Button size="small" onClick={() => setHelp(true)}>?</Button></Tooltip>
          </Layout.Header>
          {kill && (
            <div style={{ background: p.bad, color: '#fff', padding: '8px 18px', display: 'flex', gap: 18, alignItems: 'center', position: 'sticky', top: 44, zIndex: 9 }}>
              <b>✕ 停机闸已触发{BAD ? '' : '（演示）'}</b>
              {BAD ? <span>{KILL_AT} watchdog 触发 · 原因：Linux⇄Windows Redis 隧道心跳中断超过 90s · 新开仓全部拦截，减仓与撤单保留 · 隧道 10:36:40 已恢复，停机闸不自动解除</span>
                : <span>{PM ? '20:40:05' : '10:42:15'} · 原因：实盘账户对账不一致 · 新开仓全部拦截，减仓与撤单保留</span>}
              <span style={{ flex: 1 }} />
              <a href="#recon" style={{ color: '#fff', textDecoration: 'underline' }}>{BAD ? '查看解除前核对清单 →' : '查看对账与风控 →'}</a>
            </div>
          )}
          {history && (
            <div style={{ background: p.warn + '22', borderBottom: `1px solid ${p.warn}66`, color: p.text, padding: '6px 18px', display: 'flex', gap: 14, alignItems: 'center', position: 'sticky', top: kill ? 80 : 44, zIndex: 8, fontSize: 12 }}>
              <EyeOutlined style={{ color: p.warn }} />
              <b style={{ color: p.warn }}>正在回看 2026-{DATES.find((d) => d.value === date)?.label}</b>
              <span>页面显示当日收盘后的快照，只读；原型中各页数据未随日期切换。</span>
              <span style={{ flex: 1 }} />
              <Button size="small" onClick={() => setDate('1116')}>回到今天</Button>
            </div>
          )}
          <Layout>
            <Layout.Sider width={196} collapsible collapsed={collapsed} onCollapse={setCollapsed} collapsedWidth={56} style={{ borderRight: `1px solid ${p.border}`, position: 'sticky', top: 44, height: 'calc(100vh - 44px)', overflow: 'auto' }}>
              <Menu mode="inline" selectedKeys={[page]} items={nav} onClick={(e) => { location.hash = e.key }} style={{ borderInlineEnd: 'none', paddingTop: 4 }} />
            </Layout.Sider>
            <Layout.Content style={{ padding: '12px 16px 32px', minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, minHeight: 28 }}>
                <span style={{ color: p.muted, fontSize: 12 }}>{GROUP[page] ?? '总览'} /</span>
                <span style={{ color: p.head, fontSize: 18, fontWeight: 600 }}>{LABEL[page] ?? '今日运行'}</span>
                <Tag style={{ marginLeft: 6 }}>示意数据</Tag>
                <span style={{ flex: 1 }} />
                <Freshness p={p} page={page} history={history} />
              </div>
              <div key={page} className="qs-page">
                {(page === 'today' || !LABEL[page]) && <Today p={p} />}
                {page === 'services' && <Services p={p} />}
                {page === 'backtest' && <Tabs size="small" style={{ marginTop: -6 }} activeKey={btTab} onChange={setBtTab} items={[{ key: 'r', label: '回测结果', children: <Backtest p={p} /> }, { key: 'j', label: <>研究任务队列 <Badge count={1} size="small" color={p.info} /></>, children: <ResearchJobs p={p} /> }]} />}
                {page === 'strategies' && <Strategies p={p} />}
                {page === 'ailu' && <AiLimit p={p} />}
                {page === 'dataov' && <DataOverview p={p} />}
                {page === 'tables' && <DataTables p={p} />}
                {page === 'catalog' && <Catalog p={p} />}
                {page === 'quality' && <Quality p={p} />}
                {page === 'nodes' && <Nodes p={p} />}
                {page === 'releases' && <Releases p={p} />}
                {page === 'alerts' && <Alerts p={p} />}
                {page === 'live' && <Live p={p} />}
                {page === 'orders' && <Orders p={p} />}
                {page === 'recon' && <Recon p={p} />}
                {page === 'runs' && <Runs p={p} />}
                {page === 'paper' && <Paper p={p} />}
                {page === 'market' && <Market p={p} />}
                {page === 'board' && <Board p={p} />}
              </div>
            </Layout.Content>
          </Layout>
        </Layout>
        <Palette open={cmd} onClose={() => setCmd(false)} p={p} mode={mode} recent={recent} />
        <Modal open={help} onCancel={() => setHelp(false)} footer={null} title="快捷键" width={420}>
          {KEYS.map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: `1px solid ${p.border}` }}>
              <span>{v}</span><span>{k.split(' ').map((x, i) => (/^[A-Z⌘/[?]|Esc|Ctrl/.test(x) && x.length < 6 ? <kbd key={i} className="qs-kbd">{x}</kbd> : <span key={i} style={{ color: p.muted, margin: '0 4px' }}>{x}</span>))}</span>
            </div>
          ))}
        </Modal>
      </AntApp>
    </ConfigProvider>
  )
}
