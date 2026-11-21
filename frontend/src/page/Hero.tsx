import type { ReactNode } from 'react'
import { usePalette } from '../theme/context'
import type { Status } from '../theme/status'
import { StatusTag } from '../ui/StatusTag'

export interface Kpi { k: string; v: ReactNode; sub?: ReactNode; color?: string; onClick?: () => void }

/** 结论条：先给结论（状态 + 一句话），再给几个关键数（设计文档 §6.1）。 */
export function Hero({ s, statusText, title, subtitle, kpis = [] }: { s: Status; statusText?: string; title: ReactNode; subtitle?: ReactNode; kpis?: Kpi[] }) {
  const p = usePalette()
  return (
    <section className="qs-hero" style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', padding: '12px 14px', marginBottom: 12, background: p.panel, border: `1px solid ${p.border}`, borderLeft: `3px solid ${s === 'idle' || s === 'closed' ? p.muted : (s === 'ok' ? p.ok : s === 'warn' ? p.warn : s === 'bad' ? p.bad : p.running)}`, borderRadius: 4 }}>
      <div style={{ flex: '1 1 280px', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <StatusTag s={s} text={statusText} />
          <h2 data-testid="hero-title" style={{ margin: 0, fontSize: 16, fontWeight: 600, color: p.head }}>{title}</h2>
        </div>
        {subtitle !== undefined && <div data-testid="hero-subtitle" style={{ marginTop: 4, fontSize: 12, color: p.muted }}>{subtitle}</div>}
      </div>
      {kpis.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 20px', minWidth: 0 }}>
          {kpis.map((x) => (
            <div key={x.k} role={x.onClick ? 'button' : undefined} tabIndex={x.onClick ? 0 : undefined}
              onClick={x.onClick} onKeyDown={(e) => { if (x.onClick && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); x.onClick() } }}
              style={{ minWidth: 72, cursor: x.onClick ? 'pointer' : undefined, borderBottom: x.onClick ? `1px dashed ${p.muted}` : undefined }}>
              <div style={{ fontSize: 11, color: p.muted }}>{x.k}</div>
              <div style={{ fontSize: 15, color: x.color ?? p.text }}>{x.v}</div>
              {x.sub !== undefined && <div style={{ fontSize: 11, color: p.muted }}>{x.sub}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
