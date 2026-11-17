import { useMemo } from 'react'
import { EChart, type EChartsOption } from '../../page/EChart'
import { clipFuture, toSeries, type Point } from '../../page/series'
import type { RegimeRow } from '../../api/market'
import { usePalette } from '../../theme/context'

const closeOf = (x: string) => new Date(`${x}T15:00:00`)

function col(rows: RegimeRow[], c: string, now: Date): Point[] {
  return clipFuture(toSeries(rows, (r) => r.dt, (r) => r[c] as number | null), now, closeOf)
}

/** 近 60 日情绪：涨停实心柱、炸板虚线框柱、跌停柱、上涨占比折线（右轴）；斜率单独小图。null 断开。 */
export function TrendChart({ rows }: { rows: RegimeRow[] }) {
  const p = usePalette()
  const [main, slope] = useMemo(() => {
    const now = new Date()
    const xs = col(rows, 'limit_up_count', now).map(([x]) => x)
    const grid = { left: 44, right: 48, top: 28, bottom: 24 }
    const main: EChartsOption = {
      grid, legend: { top: 0, textStyle: { color: p.muted } },
      xAxis: { type: 'category', data: xs, axisLabel: { color: p.muted } },
      yAxis: [
        { type: 'value', name: '家数', splitLine: { lineStyle: { color: p.grid } } },
        { type: 'value', name: '上涨占比', min: 0, max: 1, splitLine: { show: false }, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` } },
      ],
      series: [
        { name: '涨停', type: 'bar', data: col(rows, 'limit_up_count', now), itemStyle: { color: p.up } },
        { name: '炸板', type: 'bar', data: col(rows, 'broken_board_count', now), itemStyle: { color: 'transparent', borderColor: p.up, borderType: 'dashed', borderWidth: 1 } },
        { name: '跌停', type: 'bar', data: col(rows, 'limit_down_count', now), itemStyle: { color: p.down } },
        { name: '上涨占比', type: 'line', yAxisIndex: 1, data: col(rows, 'market_advance_ratio', now), connectNulls: false, showSymbol: false, lineStyle: { color: p.line } },
      ],
    }
    const slope: EChartsOption = {
      grid: { ...grid, top: 12 },
      xAxis: { type: 'category', data: xs, axisLabel: { color: p.muted } },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: p.grid } } },
      series: [{ name: 'HS300 MA20 五日斜率', type: 'line', data: col(rows, 'index_ma20_slope_5d', now), connectNulls: false, showSymbol: false, lineStyle: { color: p.line2 }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0 }], lineStyle: { color: p.muted, type: 'dashed' } } }],
    }
    return [main, slope]
  }, [rows, p])
  return (
    <>
      <EChart option={main} height={240} />
      <div style={{ fontSize: 12, color: p.muted, margin: '8px 0 0' }}>HS300 MA20 五日斜率</div>
      <EChart option={slope} height={120} />
    </>
  )
}
