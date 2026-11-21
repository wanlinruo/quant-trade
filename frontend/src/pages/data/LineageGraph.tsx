import { useCallback, useMemo } from 'react'
import type { LineageNode } from '../../api/data'
import { EChart, type EChartsOption } from '../../page/EChart'
import { goTo } from '../../shell/router'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'

/** 分层：稀疏的层合并成一列（原始并入真值；实时、派生、运行记录合为一列），消费者过多时拆两列。 */
const LAYER: Record<string, number> = { source: 0, raw: 1, canonical: 1, realtime: 2, derived: 2, operational: 2, consumer: 3 }
const LAYER_LABEL = ['数据源', '原始 / 真值层', '实时 / 派生 / 运行记录', '消费者']
const KIND_LABEL: Record<string, string> = { source: '数据源', raw: '原始层', canonical: '真值层', realtime: '实时层', derived: '派生层', operational: '运行记录', consumer: '消费者' }
const ROW_GAP = 22
const COL_GAP = 240
const CONSUMER_SPLIT = 24
const STATUS_RANK: Record<string, number> = { red: 0, yellow: 1, gray: 2, green: 3 }

function colorOf(n: LineageNode, p: Palette): string {
  if (n.kind === 'source' || n.kind === 'consumer') return p.muted
  return { red: p.bad, yellow: p.warn, green: p.ok, gray: p.idle }[n.status ?? 'gray'] ?? p.idle
}

const clip = (s: string, n = 22) => s.length > n ? `${s.slice(0, n)}…` : s

/** 血缘图（原型“数据从哪来、被谁用？”）：按层分列、不压缩行距；列内按与相邻层连线的平均位置排序以减少交叉；
 *  有问题的表排在真值层最前。悬停高亮上下游；点数据集节点看表详情。 */
export function LineageGraph({ nodes, edges }: { nodes: LineageNode[]; edges: { source: string; target: string }[] }) {
  const p = usePalette()
  const { option, height } = useMemo(() => {
    const up = new Map<string, string[]>(); const down = new Map<string, string[]>()
    for (const e of edges) {
      up.set(e.target, [...(up.get(e.target) ?? []), e.source])
      down.set(e.source, [...(down.get(e.source) ?? []), e.target])
    }
    const layerOf = (n: LineageNode) => LAYER[n.kind] ?? 2
    const cols: LineageNode[][] = [[], [], [], []]
    for (const n of nodes) cols[layerOf(n)].push(n)
    const rank = new Map<string, number>()
    const place = (list: LineageNode[]) => list.forEach((n, i) => rank.set(n.id, i))
    const center = (n: LineageNode, links: Map<string, string[]>) => {
      const ys = (links.get(n.id) ?? []).map((id) => rank.get(id)).filter((x): x is number => x !== undefined)
      return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Number.POSITIVE_INFINITY
    }
    // 真值层：有问题的在前，其次按名称；其余各列按重心排序
    cols[1].sort((a, b) => (STATUS_RANK[a.status ?? 'gray'] - STATUS_RANK[b.status ?? 'gray']) || a.label.localeCompare(b.label, 'zh'))
    place(cols[1])
    cols[0].sort((a, b) => center(a, down) - center(b, down) || a.label.localeCompare(b.label, 'zh')); place(cols[0])
    cols[2].sort((a, b) => center(a, up) - center(b, up) || a.label.localeCompare(b.label, 'zh')); place(cols[2])
    cols[3].sort((a, b) => center(a, up) - center(b, up) || a.label.localeCompare(b.label, 'zh'))
    // 消费者过多时拆两列，保持排序的连续性
    const consumers = cols[3]
    const split = consumers.length > CONSUMER_SPLIT ? Math.ceil(consumers.length / 2) : consumers.length
    const columns = [cols[0], cols[1], cols[2], consumers.slice(0, split), ...(split < consumers.length ? [consumers.slice(split)] : [])]
    const labels = [...LAYER_LABEL.slice(0, 3), '消费者', ...(columns.length > 4 ? ['消费者（续）'] : [])]
    const tallest = Math.max(1, ...columns.map((c) => c.length))
    const data = columns.flatMap((col, ci) => col.map((n, i) => ({
      id: n.id, name: n.label, x: ci * COL_GAP, y: i * ROW_GAP, ref: n.ref, kind: n.kind, category: ci,
      symbolSize: n.kind === 'source' || n.kind === 'consumer' ? 6 : 9, itemStyle: { color: colorOf(n, p) },
      label: { show: true, position: 'right' as const, fontSize: 11, color: p.text, formatter: clip(n.label) },
    })))
    // 列标题作为该列顶部的无符号节点，坐标与列完全对齐
    const headers = labels.map((text, ci) => ({
      id: `__layer_${ci}`, name: text, x: ci * COL_GAP, y: -1.6 * ROW_GAP, ref: null, kind: 'header', category: ci,
      symbolSize: 0, itemStyle: { color: 'transparent' }, tooltip: { show: false }, emphasis: { disabled: true },
      label: { show: true, position: 'right' as const, fontSize: 11, color: p.muted, fontWeight: 600, formatter: text },
    }))
    const option: EChartsOption = {
      animation: false,
      tooltip: { formatter: (x: unknown) => { const d = (x as { data?: { name?: string; kind?: string } }).data; return d?.name ? `${d.name}（${KIND_LABEL[d.kind ?? ''] ?? d.kind}）` : '' } },
      series: [{
        type: 'graph', layout: 'none', roam: false, top: 12, left: 16, right: 190, bottom: 8,
        data: [...headers, ...data], links: edges.map((e) => ({ source: e.source, target: e.target })),
        edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 4,
        lineStyle: { color: p.muted, opacity: 0.22, curveness: 0.12 },
        emphasis: { focus: 'adjacency', lineStyle: { color: p.accent, width: 2, opacity: 1 }, label: { color: p.head } },
      }],
    }
    return { option, height: tallest * ROW_GAP + 70 }
  }, [nodes, edges, p])
  const onClick = useCallback((params: unknown) => {
    const x = params as { dataType?: string; data?: { ref?: string | null; kind?: string } }
    if (x.dataType === 'node' && x.data?.ref && x.data.kind !== 'source' && x.data.kind !== 'consumer') goTo('tables', { id: x.data.ref })
  }, [])
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ minWidth: 1180 }}><EChart option={option} height={height} onClick={onClick} /></div>
      <div style={{ fontSize: 11, color: p.muted }}>悬停节点高亮上下游；点真值 / 派生表打开表详情。有问题的表排在真值层最前。</div>
    </div>
  )
}
