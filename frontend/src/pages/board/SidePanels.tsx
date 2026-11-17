import { useMemo } from 'react'
import type { BoardData } from '../../api/board'
import { EChart, type EChartsOption } from '../../page/EChart'
import { hms, parseLocal } from '../../shell/time'
import { usePalette } from '../../theme/context'

const KIND: Record<string, string> = { seal: '首封', open: '开板', reseal: '回封', reassign: '改列', gap: '快照缺口' }

/** 题材涨停家数盘中累计（前 4 题材）：阶梯线，不画未来。 */
export function ConceptCurves({ data }: { data: BoardData }) {
  const p = usePalette()
  const curves = data.curves ?? []
  const option = useMemo<EChartsOption>(() => {
    const now = Date.now()
    return {
      grid: { left: 32, right: 12, top: 28, bottom: 24 }, legend: { top: 0, textStyle: { color: p.muted } },
      xAxis: { type: 'time', axisLabel: { color: p.muted } },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: p.grid } } },
      series: curves.map((c) => ({
        name: c.name, type: 'line' as const, step: 'end' as const, showSymbol: false,
        data: c.points.filter(([at]) => (parseLocal(at)?.getTime() ?? Infinity) <= now),
      })),
    }
  }, [curves, p])
  if (curves.length === 0) return <div style={{ color: p.muted }}>没有题材曲线（无概念快照或尚无封板）</div>
  return <EChart option={option} height={200} />
}

/** 封板时间轴：最新在上；盘中时刻带“≈”。点名称打开个股分时。 */
export function Timeline({ data, onPick }: { data: BoardData; onPick(s: string): void }) {
  const p = usePalette()
  const approx = data.mode === 'live' ? '≈' : ''
  const events = data.timeline ?? []
  if (events.length === 0) return <div style={{ color: p.muted }}>尚无封板事件</div>
  return (
    <div style={{ maxHeight: 360, overflow: 'auto', fontSize: 12 }}>
      {events.map((e, i) => {
        const at = parseLocal(e.at)
        const what = [KIND[e.kind] ?? e.kind, e.detail].filter(Boolean).join(' ')
        return (
          <div key={`${e.at}-${e.symbol}-${e.kind}-${i}`} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: `1px solid ${p.border}` }}>
            <span style={{ color: p.muted, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{at ? `${approx}${hms(at)}` : '未知'}</span>
            {e.symbol
              ? <button type="button" onClick={() => onPick(e.symbol)} style={{ all: 'unset', cursor: 'pointer', color: p.head }}>{e.name || e.symbol}</button>
              : <span />}
            <span style={{ color: e.kind === 'open' ? p.warn : e.kind === 'seal' || e.kind === 'reseal' ? p.up : p.muted }}>{what}</span>
          </div>
        )
      })}
    </div>
  )
}
