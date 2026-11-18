import type { Dim, Health, OverviewTable } from '../../api/data'
import { DIMENSION_LABEL, domainLabel } from './labels'
import { goTo } from '../../shell/router'
import { usePalette } from '../../theme/context'
import { StatusTag } from '../../ui/StatusTag'
import { HEALTH_LABEL, HEALTH_ORDER, HEALTH_SHORT, HEALTH_STATUS } from './healthStatus'

export const DIMS: Dim[] = ['freshness', 'completeness', 'structure', 'validity', 'lineage']

function Cell({ h }: { h: Health }) {
  return <span title={HEALTH_LABEL[h]}><StatusTag s={HEALTH_STATUS[h]} text={HEALTH_SHORT[h]} /></span>
}

/** 核心表 × 五维健康矩阵：按 红 → 黄 → 灰 → 绿 稳定排序；点行看表详情。 */
export function HealthMatrix({ tables }: { tables: OverviewTable[] }) {
  const p = usePalette()
  const sorted = [...tables].sort((a, b) => HEALTH_ORDER.indexOf(a.status) - HEALTH_ORDER.indexOf(b.status))
  const td = { padding: '4px 8px', borderBottom: `1px solid ${p.border}`, whiteSpace: 'nowrap' as const }
  return (
    <div style={{ overflowX: 'auto', maxHeight: 520, overflowY: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
        <thead>
          <tr style={{ color: p.muted, textAlign: 'left' }}>
            <th style={{ ...td, fontWeight: 400 }}>表</th><th style={{ ...td, fontWeight: 400 }}>总体</th>
            {DIMS.map((d) => <th key={d} style={{ ...td, fontWeight: 400 }}>{DIMENSION_LABEL[d as Dim] ?? d}</th>)}
            <th style={{ ...td, fontWeight: 400 }}>域</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => (
            <tr key={t.id} onClick={() => goTo('tables', { id: t.id })} style={{ cursor: 'pointer' }}>
              <td style={td}><span style={{ color: p.head }}>{t.name}</span> <span style={{ color: p.muted }}>{t.id}</span></td>
              <td style={td}><Cell h={t.status} /></td>
              {DIMS.map((d) => <td key={d} style={td}><Cell h={t.dimensions[d] ?? 'gray'} /></td>)}
              <td style={{ ...td, color: p.muted }}>{domainLabel(t.domain)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
