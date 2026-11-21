import type { ReactNode } from 'react'
import { Tooltip } from 'antd'
import { usePalette } from '../theme/context'

/** 面板标题写成一个问题（“近 60 日市场情绪怎样？”）；ⓘ 写口径与数据来源。 */
export function Panel({ title, info, extra, children, flush = false }: { title: ReactNode; info?: ReactNode; extra?: ReactNode; children: ReactNode; flush?: boolean }) {
  const p = usePalette()
  return (
    <section className="qs-panel" style={{ background: p.panel, border: `1px solid ${p.border}`, borderRadius: 4, marginBottom: 12, minWidth: 0 }}>
      <header style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, padding: '8px 12px', borderBottom: `1px solid ${p.border}` }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: p.head }}>{title}</h3>
        {info !== undefined && (
          <Tooltip title={info}><span aria-label="口径说明" style={{ color: p.muted, cursor: 'help', fontSize: 12 }}>ⓘ</span></Tooltip>
        )}
        <span style={{ flex: 1 }} />
        {extra}
      </header>
      <div style={{ padding: flush ? '4px 0 0' : 12, minWidth: 0, overflowX: 'auto' }}>{children}</div>
    </section>
  )
}
