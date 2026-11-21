import { useMemo } from 'react'
import type { Dim, Health, OverviewTable } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { goTo } from '../../shell/router'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'
import { HEALTH_LABEL, HEALTH_ORDER } from './healthStatus'
import { DIMENSION_LABEL, domainLabel } from './labels'

export const DIMS: Dim[] = ['freshness', 'completeness', 'structure', 'validity', 'lineage']
const SYMBOL: Record<Health, string> = { red: '✕', yellow: '▲', gray: '○', green: '●' }
const ROW = 24
const colorOf = (p: Palette, h: Health) => ({ red: p.bad, yellow: p.warn, green: p.ok, gray: p.idle }[h])

/** 核心表 × 五维健康矩阵（原型热力格）：按 红 → 黄 → 灰 → 绿 稳定排序；格内符号 + 颜色，不单靠颜色；点格子看表详情。 */
export function HealthMatrix({ tables }: { tables: OverviewTable[] }) {
  const p = usePalette()
  const sorted = useMemo(() => [...tables].sort((a, b) => HEALTH_ORDER.indexOf(a.status) - HEALTH_ORDER.indexOf(b.status) || a.name.localeCompare(b.name, 'zh')), [tables])
  const option = useMemo<EChartsOption>(() => {
    const cols = ['总体', ...DIMS.map((d) => DIMENSION_LABEL[d] ?? d)]
    const data = sorted.flatMap((t, y) => [t.status, ...DIMS.map((d) => t.dimensions[d] ?? 'gray')].map((h, x) => ({
      value: [x, y, HEALTH_ORDER.indexOf(h)], h, id: t.id, name: t.name, dim: cols[x], domain: domainLabel(t.domain),
      itemStyle: { color: colorOf(p, h), borderColor: p.panel, borderWidth: x === 0 ? 3 : 2, borderRadius: 2, opacity: x === 0 ? 1 : 0.92 },
      label: { show: true, formatter: SYMBOL[h], color: h === 'gray' ? p.muted : '#fff', fontSize: 10 },
    })))
    return {
      animation: false,
      grid: { left: 190, right: 8, top: 24, bottom: 4 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const x = (raw as { data: typeof data[number] }).data
        return `<b>${x.name}</b> <span style="opacity:.6">${x.id} · ${x.domain}</span><br/>${x.dim}：${SYMBOL[x.h]} ${HEALTH_LABEL[x.h]}<br/><span style="opacity:.6">点击打开表详情</span>`
      } },
      xAxis: { type: 'category', position: 'top', data: cols, axisLabel: { color: p.text, fontSize: 12 }, axisTick: { show: false }, axisLine: { show: false } },
      yAxis: { type: 'category', inverse: true, data: sorted.map((t) => t.name), axisLabel: { color: p.text, fontSize: 12, width: 180, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'heatmap', data, emphasis: { itemStyle: { borderColor: p.head, borderWidth: 1 } } }],
    }
  }, [sorted, p])
  return (
    <div data-testid="health-matrix" style={{ maxHeight: 640, overflowY: 'auto', overflowX: 'auto' }}>
      <div style={{ minWidth: 520 }}>
        <EChart option={option} height={sorted.length * ROW + 32}
          onClick={(raw) => { const x = (raw as { data?: { id?: string } }).data; if (x?.id) goTo('tables', { id: x.id }) }} />
      </div>
    </div>
  )
}
