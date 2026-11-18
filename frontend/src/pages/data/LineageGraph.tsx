import { useCallback, useMemo } from 'react'
import type { LineageNode } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { goTo } from '../../shell/router'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'

export const COLUMNS = ['source', 'raw', 'canonical', 'realtime', 'derived', 'operational', 'consumer']
const COLUMN_LABEL: Record<string, string> = { source: '数据源', raw: '原始层', canonical: '真值层', realtime: '实时层', derived: '派生层', operational: '运行记录', consumer: '消费者' }
const ROW_GAP = 26

function colorOf(n: LineageNode, p: Palette): string {
  if (n.kind === 'source' || n.kind === 'consumer') return p.muted
  return { red: p.bad, yellow: p.warn, green: p.ok, gray: p.idle }[n.status ?? 'gray'] ?? p.idle
}

/** 血缘图：按层分列固定位置，悬停高亮上下游（adjacency）；点数据集节点看表详情。 */
export function LineageGraph({ nodes, edges }: { nodes: LineageNode[]; edges: { source: string; target: string }[] }) {
  const p = usePalette()
  const { option, height } = useMemo(() => {
    const byCol = new Map<string, LineageNode[]>()
    for (const n of nodes) {
      const col = COLUMNS.includes(n.kind) ? n.kind : 'derived'
      byCol.set(col, [...(byCol.get(col) ?? []), n])
    }
    const tallest = Math.max(1, ...[...byCol.values()].map((l) => l.length))
    const data = COLUMNS.flatMap((col, ci) => (byCol.get(col) ?? []).map((n, i) => ({
      id: n.id, name: n.label, x: ci * 220, y: i * ROW_GAP, ref: n.ref, kind: n.kind, category: ci,
      symbolSize: 8, itemStyle: { color: colorOf(n, p) },
      label: { show: true, position: 'right' as const, fontSize: 10, color: p.text },
    })))
    const option: EChartsOption = {
      tooltip: { formatter: (x: unknown) => { const d = (x as { data?: { name?: string; kind?: string } }).data; return d?.name ? `${d.name}（${COLUMN_LABEL[d.kind ?? ''] ?? d.kind}）` : '' } },
      title: COLUMNS.map((c, i) => ({ text: COLUMN_LABEL[c], left: 12 + i * (100 / COLUMNS.length) + '%', top: 0, textStyle: { fontSize: 11, color: p.muted, fontWeight: 400 } })),
      series: [{
        type: 'graph', layout: 'none', roam: true, top: 28, left: 12, right: 140, bottom: 12,
        data, links: edges.map((e) => ({ source: e.source, target: e.target })),
        edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 5,
        lineStyle: { color: p.border, opacity: 0.7, curveness: 0.15 },
        emphasis: { focus: 'adjacency', lineStyle: { color: p.accent, width: 2, opacity: 1 } },
      }],
    }
    return { option, height: Math.min(900, Math.max(320, tallest * ROW_GAP + 60)) }
  }, [nodes, edges, p])
  const onClick = useCallback((params: unknown) => {
    const x = params as { dataType?: string; data?: { ref?: string | null; kind?: string } }
    if (x.dataType === 'node' && x.data?.ref && x.data.kind !== 'source' && x.data.kind !== 'consumer') goTo('tables', { id: x.data.ref })
  }, [])
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: 1100 }}><EChart option={option} height={height} onClick={onClick} /></div>
    </div>
  )
}
