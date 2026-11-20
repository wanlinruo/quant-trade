import { useEffect, type ReactNode } from 'react'
import { Button, Tooltip } from 'antd'
import { UpOutlined, DownOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { Num, statusColor } from '../ui'

/** 结论条：左边一句话结论，右边 3–5 个关键数字；异常时左侧色条变色。 */
export function Hero({ p, s, icon, title, sub, kpis }: {
  p: Palette; s: Status; icon: ReactNode; title: ReactNode; sub: ReactNode
  kpis: { k: string; v: ReactNode; sub?: ReactNode; c?: string; go?: string; onClick?: () => void; tip?: string }[]
}) {
  return (
    <section style={{ display: 'flex', background: p.panel, border: `1px solid ${p.border}`, borderLeft: `3px solid ${statusColor(p, s)}`, borderRadius: 4 }}>
      <div style={{ padding: '12px 18px', minWidth: 320, flex: '0 1 auto', maxWidth: 520, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: statusColor(p, s), fontSize: 24, display: 'flex' }}>{icon}</span>
        <div>
          <div style={{ color: p.head, fontSize: 20, fontWeight: 600 }}>{title}</div>
          <div style={{ color: p.muted, fontSize: 12 }}>{sub}</div>
        </div>
      </div>
      {kpis.map((k) => (
        <div key={k.k} title={k.tip} className={k.go || k.onClick ? 'qs-kpi-link' : undefined}
          onClick={k.onClick ?? (k.go ? () => { location.hash = k.go! } : undefined)}
          style={{ flex: 1, borderLeft: `1px solid ${p.border}`, padding: '10px 14px', minWidth: 0 }}>
          <div style={{ color: p.muted, fontSize: 12, display: 'flex', justifyContent: 'space-between' }}>{k.k}{(k.go || k.onClick) && <span style={{ opacity: .6 }}>›</span>}</div>
          <Num size={18} weight={600} color={k.c ?? p.head}>{k.v}</Num>
          {k.sub && <div style={{ color: p.muted, fontSize: 11 }}>{k.sub}</div>}
        </div>
      ))}
    </section>
  )
}

/** 正式版拿不到的数据：显式标“缺失”，不推算。 */
export function Missing({ p, why }: { p: Palette; why: string }) {
  return <span title={why} style={{ color: p.warn, fontSize: 12, borderBottom: `1px dashed ${p.warn}`, cursor: 'help' }}>缺失</span>
}

/** 子弹图：实际用量 vs 上限（替代仪表盘）。 */
export function Bullet({ p, label, used, limit, unit, fmt = (v: number) => v.toFixed(1) }: {
  p: Palette; label: string; used: number | null; limit: number; unit: string; fmt?: (v: number) => string
}) {
  const pct = used == null ? 0 : Math.min(100, (used / limit) * 100)
  const c = pct >= 100 ? p.bad : pct >= 70 ? p.warn : p.info
  const left = used == null ? null : limit - used
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
        <span style={{ color: p.text }}>{label}</span>
        <span>{used == null ? <Missing p={p} why="live-core 尚未对外暴露该用量" /> : <Num color={c}>{fmt(used)}{unit}</Num>}<span style={{ color: p.muted }}> / {fmt(limit)}{unit}</span>
          {left != null && <span style={{ color: pct >= 90 ? p.warn : p.muted, marginLeft: 8, fontSize: 11 }}>余 {fmt(left)}{unit}</span>}</span>
      </div>
      <div style={{ position: 'relative', height: 8, background: p.idle, borderRadius: 2, marginTop: 4 }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pct}%`, background: c, borderRadius: 2 }} />
        <div style={{ position: 'absolute', left: '70%', top: -2, bottom: -2, width: 1, background: p.muted }} />
        <div style={{ position: 'absolute', right: 0, top: -3, bottom: -3, width: 2, background: p.bad }} />
      </div>
    </div>
  )
}

export const signed = (v: number, d = 2) => `${v > 0 ? '+' : ''}${v.toFixed(d)}`

/** 表格空态：说明为什么是空的，而不是只显示“暂无数据”。 */
export function EmptyNote({ p, text }: { p: Palette; text: string }) {
  return <div style={{ padding: '18px 0', color: p.muted, fontSize: 12, textAlign: 'center' }}>{text}</div>
}

/** 抽屉内上一条 / 下一条：按钮 + 键盘 ↑↓ / J K，列表顺序与表格一致。 */
export function DrawerNav<T>({ p, list, cur, set }: { p: Palette; list: T[]; cur: T | null; set: (x: T) => void }) {
  const i = cur == null ? -1 : list.indexOf(cur)
  useEffect(() => {
    if (i < 0) return
    const f = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest('input, textarea')) return
      if ((e.key === 'ArrowDown' || e.key === 'j') && i < list.length - 1) { e.preventDefault(); set(list[i + 1]) }
      if ((e.key === 'ArrowUp' || e.key === 'k') && i > 0) { e.preventDefault(); set(list[i - 1]) }
    }
    addEventListener('keydown', f); return () => removeEventListener('keydown', f)
  }, [i, list, set])
  if (i < 0) return null
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, color: p.muted, fontSize: 12 }}>
      <Tooltip title="上一条（↑ / K）"><Button size="small" type="text" icon={<UpOutlined />} disabled={i === 0} onClick={() => set(list[i - 1])} /></Tooltip>
      <Num size={12} color={p.muted}>{i + 1}/{list.length}</Num>
      <Tooltip title="下一条（↓ / J）"><Button size="small" type="text" icon={<DownOutlined />} disabled={i === list.length - 1} onClick={() => set(list[i + 1])} /></Tooltip>
    </span>
  )
}
