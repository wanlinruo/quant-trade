import { useEffect, useState, type ReactNode } from 'react'
import { Button, Dropdown, Tooltip } from 'antd'
import { DesktopOutlined, MoonOutlined, SearchOutlined, SunOutlined } from '@ant-design/icons'
import type { ShellStatus } from '../api/shell'
import { usePalette } from '../theme/context'
import { Missing } from '../ui/Missing'
import { Num } from '../ui/Num'
import { Link } from './router'
import { useTheme, type ThemePref } from './ThemeProvider'
import { dayLabel, hms, parseLocal, phaseOf } from './time'

function useNow(ms = 1000): Date {
  const [d, setD] = useState(() => new Date())
  useEffect(() => { const t = setInterval(() => setD(new Date()), ms); return () => clearInterval(t) }, [ms])
  return d
}

function Pill({ ok, text, to, tip, testId }: { ok: boolean; text: string; to: string; tip: ReactNode; testId: string }) {
  const p = usePalette()
  const c = ok ? p.ok : p.bad
  return (
    <Tooltip title={tip}>
      <Link to={to} data-testid={testId} className="qs-pill"
        style={{ display: 'inline-flex', gap: 5, alignItems: 'center', fontSize: 12, padding: '1px 8px', borderRadius: 3, border: `1px solid ${c}66`, color: c, background: ok ? undefined : c + '18' }}>
        {`${ok ? '●' : '✕'} ${text}`}
      </Link>
    </Tooltip>
  )
}

function Stat({ k, children, to, tip, testId }: { k: string; children: ReactNode; to: string; tip: ReactNode; testId: string }) {
  const p = usePalette()
  return (
    <span data-testid={testId} className="qs-stat qs-h-opt" style={{ color: p.muted, fontSize: 12, display: 'inline-flex', gap: 4 }}>
      <Tooltip title={tip}><Link to={to} style={{ color: p.muted }}>{k}</Link></Tooltip>{children}
    </span>
  )
}

function Heartbeat({ at }: { at: string | null }) {
  const p = usePalette()
  const now = useNow()
  const d = parseLocal(at)
  if (!d) return <Missing why="Windows 实盘 agent 心跳不可用、已过期或未上报" />
  const age = Math.max(0, Math.round((now.getTime() - d.getTime()) / 1000))
  return <Num size={12} color={age < 10 ? p.ok : p.warn}>{age}s</Num>
}

function Clock() {
  const p = usePalette()
  const now = useNow()
  return (
    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'baseline' }}>
      <span style={{ color: p.muted, fontSize: 12 }}>{dayLabel(now)}</span>
      <span className="qs-h-long" style={{ color: p.muted, fontSize: 12 }}>{phaseOf(now)}</span>
      <Num color={p.head}>{hms(now)}</Num>
    </span>
  )
}

const KILL = {
  clear: { ok: true, text: '停机闸 未触发' },
  stopped: { ok: false, text: '停机闸 已触发' },
  unknown: { ok: false, text: '停机状态未知' },
} as const

export function TopBar({ status, error, collapsed = false, onOpenSearch, onOpenHelp }: {
  status: ShellStatus | null; error: string | null; collapsed?: boolean; onOpenSearch(): void; onOpenHelp(): void
}) {
  const p = usePalette()
  const theme = useTheme()
  const killState = status?.kill.state ?? 'unknown'
  const kill = KILL[killState] ?? KILL.unknown
  const liveState = status?.live.state ?? 'unknown'
  const liveText = status?.live.label ?? '实盘状态未知'
  const unavailable = error ?? '状态尚未取得'
  const themeItems = [
    { key: 'system', icon: <DesktopOutlined />, label: `跟随系统（当前${theme.system === 'dark' ? '黑暗' : '白天'}）` },
    { key: 'light', icon: <SunOutlined />, label: '白天' },
    { key: 'dark', icon: <MoonOutlined />, label: '黑暗' },
  ]
  return (
    <div className="qs-topbar" style={{ display: 'flex', alignItems: 'center', gap: 14, height: '100%' }}>
      <Link to="today" style={{ color: p.head, fontWeight: 700, fontSize: 15, width: collapsed ? 40 : 176, transition: 'width .2s' }}>qsys</Link>
      <Pill testId="live-pill" ok={liveState === 'trading'} text={liveText} to="live"
        tip={status ? (liveState === 'reduce_only' ? '停机闸已触发：禁止自动开仓' : liveState === 'trading' ? '三处停机闸均未生效 · 点击打开实盘总览' : status.kill.message) : unavailable} />
      <Pill testId="kill-pill" ok={kill.ok} text={kill.text} to="recon" tip={status ? status.kill.message : unavailable} />
      <Stat testId="stat-qmt" k="QMT" to="nodes" tip="QMT 执行端在线状态 · 点击看节点与链路">
        <Missing why={status?.missing.qmt ?? unavailable} />
      </Stat>
      <Stat testId="stat-heartbeat" k="心跳" to="live" tip="Windows 实盘 agent 最近一次心跳距今">
        {status ? <Heartbeat at={status.agent_heartbeat_at} /> : <Missing why={unavailable} />}
      </Stat>
      <Stat testId="stat-alerts" k="告警" to="alerts" tip="未处理告警数">
        {status && status.alerts_open !== null
          ? <Num size={12} color={status.alerts_open ? p.warn : p.ok}>{status.alerts_open}</Num>
          : <Missing why={status?.missing.alerts_open ?? unavailable} />}
      </Stat>
      <span style={{ flex: 1 }} />
      <Button size="small" icon={<SearchOutlined />} onClick={onOpenSearch} className="qs-h-search" aria-label="搜索"
        style={{ color: p.muted, display: 'flex', justifyContent: 'flex-start' }}>
        <span className="qs-h-long">搜索</span><span className="qs-h-long" style={{ marginLeft: 'auto', opacity: 0.6 }}>⌘K</span>
      </Button>
      <Clock />
      <Dropdown trigger={['click']} menu={{ items: themeItems, selectable: true, selectedKeys: [theme.pref], onClick: (e) => theme.setPref(e.key as ThemePref) }}>
        <Tooltip title="明暗主题（T）" placement="bottomRight">
          <Button size="small" aria-label="明暗主题" icon={theme.mode === 'dark' ? <MoonOutlined /> : <SunOutlined />} />
        </Tooltip>
      </Dropdown>
      <Tooltip title="快捷键（?）" placement="bottomRight"><Button size="small" onClick={onOpenHelp} aria-label="快捷键">?</Button></Tooltip>
    </div>
  )
}
