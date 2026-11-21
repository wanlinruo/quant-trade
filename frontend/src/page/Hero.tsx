import type { KeyboardEvent, ReactNode } from 'react'
import {
  CheckCircleFilled, CloseCircleFilled, ClockCircleOutlined, MinusCircleOutlined, SyncOutlined, WarningFilled,
} from '@ant-design/icons'
import { usePalette } from '../theme/context'
import { statusText as STATUS_TEXT, type Status } from '../theme/status'
import { Num } from '../ui/Num'
import { Spark } from '../ui/Spark'
import { StatusTag } from '../ui/StatusTag'

export interface Kpi {
  k: string; v: ReactNode; sub?: ReactNode; color?: string; onClick?: () => void
  /** 可选迷你走势：只放真实序列，null 处断开 */
  spark?: (number | null)[]; sparkColor?: string
}

const ICON: Record<Status, ReactNode> = {
  ok: <CheckCircleFilled />, warn: <WarningFilled />, bad: <CloseCircleFilled />,
  running: <SyncOutlined />, idle: <ClockCircleOutlined />, closed: <MinusCircleOutlined />,
}

/** 结论条：左边状态图标 + 一句话结论，右边 3–5 个关键数分栏（设计文档 §6.1，原型 common.tsx）。
 *  状态由不同形状的图标表达（并带可读标签），不单靠颜色（§7）。 */
export function Hero({ s, statusText, title, subtitle, kpis = [] }: {
  s: Status; statusText?: string; title: ReactNode; subtitle?: ReactNode; kpis?: Kpi[]
}) {
  const p = usePalette()
  const accent = s === 'idle' || s === 'closed' ? p.muted : (s === 'ok' ? p.ok : s === 'warn' ? p.warn : s === 'bad' ? p.bad : p.running)
  const label = statusText ?? STATUS_TEXT[s]
  return (
    <section className="qs-hero" style={{ display: 'flex', flexWrap: 'wrap', marginBottom: 12, background: p.panel, border: `1px solid ${p.border}`, borderLeft: `3px solid ${accent}`, borderRadius: 4 }}>
      <div style={{ flex: kpis.length ? '1 1 360px' : '1 1 auto', maxWidth: kpis.length ? 560 : undefined, minWidth: 0, padding: '12px 18px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span role="img" aria-label={label} title={label} data-testid="hero-status" style={{ color: accent, fontSize: 24, display: 'flex', flex: 'none' }}>{ICON[s]}</span>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h2 data-testid="hero-title" style={{ margin: 0, fontSize: 20, fontWeight: 600, lineHeight: 1.35, color: p.head }}>{title}</h2>
            {/* 自定义状态文字（如“盘中 · 暂计”“历史 · 只读”）带有图标表达不了的信息，保持可见 */}
            {statusText !== undefined && <StatusTag s={s} text={statusText} />}
          </div>
          {subtitle !== undefined && <div data-testid="hero-subtitle" style={{ marginTop: 2, fontSize: 12, color: p.muted }}>{subtitle}</div>}
        </div>
      </div>
      {kpis.map((x) => (
        <div key={x.k} data-testid="hero-kpi" className={x.onClick ? 'qs-kpi-link' : undefined}
          role={x.onClick ? 'button' : undefined} tabIndex={x.onClick ? 0 : undefined} onClick={x.onClick}
          onKeyDown={(e: KeyboardEvent) => { if (x.onClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); x.onClick() } }}
          style={{ flex: '1 1 140px', minWidth: 0, padding: '10px 14px', borderLeft: `1px solid ${p.border}`, cursor: x.onClick ? 'pointer' : undefined }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: p.muted }}>
            {x.k}{x.onClick && <span aria-hidden style={{ opacity: 0.6 }}>›</span>}
          </div>
          {/* 长文本值（如模型 ID、费用说明）不按大号数字排，避免把格子撑高 */}
          {typeof x.v === 'string' && x.v.length > 14
            ? <div title={x.v} style={{ fontSize: 12, color: x.color ?? p.head, overflowWrap: 'anywhere', lineHeight: 1.4 }}>{x.v}</div>
            : <Num size={18} weight={600} color={x.color ?? p.head}>{x.v}</Num>}
          {x.sub !== undefined && <div style={{ fontSize: 11, color: p.muted }}>{x.sub}</div>}
          {x.spark && <div style={{ marginTop: 4 }}><Spark data={x.spark} color={x.sparkColor ?? p.line} label={`${x.k}走势`} /></div>}
        </div>
      ))}
    </section>
  )
}
