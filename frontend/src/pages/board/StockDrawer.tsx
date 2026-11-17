import { useEffect, useMemo, useState } from 'react'
import { Drawer } from 'antd'
import { fetchBoardSeries, type Card, type SeriesData } from '../../api/board'
import { EChart, type EChartsOption } from '../../page/EChart'
import { hms, parseLocal } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'

const KIND: Record<string, string> = { seal: '首封', open: '开板', reseal: '回封' }

function heightText(card?: Card): string {
  if (!card) return '未进入看板'
  if (card.status !== 'sealed') return '未封'
  return card.height === null ? '高度未知' : card.height === 1 ? '首板' : `${card.height} 板`
}

/** 个股分时（按涨跌幅 = 价 ÷ 昨收 − 1），标涨停价线与首封、开板、回封；缺的不补。 */
export function StockDrawer({ day, symbol, card, onClose }: { day: string; symbol: string | null; card?: Card; onClose(): void }) {
  const p = usePalette()
  const [s, setS] = useState<{ data: SeriesData | null; error: string | null }>({ data: null, error: null })
  useEffect(() => {
    if (!symbol) return
    const c = new AbortController()
    setS({ data: null, error: null })
    fetchBoardSeries(day, symbol, c.signal).then((data) => setS({ data, error: null }),
      (e: Error) => { if (e.name !== 'AbortError') setS({ data: null, error: e.message }) })
    return () => c.abort()
  }, [day, symbol])

  const d = s.data
  const option = useMemo<EChartsOption | null>(() => {
    if (!d?.available || !d.points || !d.prev_close) return null
    const prev = d.prev_close
    const line: [string, number | null][] = d.points.map((x) => [x.at, x.price / prev - 1])
    const marks: object[] = []
    if (d.limit_up) marks.push({ yAxis: d.limit_up / prev - 1, name: '涨停价', label: { formatter: '涨停价', color: p.up, position: 'insideStartTop' }, lineStyle: { color: p.up, type: 'dashed' } })
    for (const e of d.events ?? []) {
      const at = parseLocal(e.at)
      const label = `${KIND[e.kind] ?? e.kind} ${at ? hms(at) : '未知'}`
      marks.push({ xAxis: e.at, name: label, label: { show: false }, lineStyle: { color: e.kind === 'open' ? p.warn : p.up, type: 'dotted' } })
    }
    return {
      grid: [{ left: 48, right: 16, top: 16, height: '58%' }, { left: 48, right: 16, top: '74%', bottom: 24 }],
      xAxis: [{ type: 'time', gridIndex: 0, axisLabel: { show: false } }, { type: 'time', gridIndex: 1 }],
      yAxis: [{ type: 'value', gridIndex: 0, scale: true, axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(1)}%` }, splitLine: { lineStyle: { color: p.grid } } },
        { type: 'value', gridIndex: 1, splitLine: { show: false } }],
      series: [
        { name: '涨跌幅', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: line, connectNulls: false, showSymbol: false, lineStyle: { color: p.line },
          markLine: { silent: true, symbol: 'none', data: marks } },
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: d.points.map((x) => [x.at, x.volume]), itemStyle: { color: p.muted } },
      ],
    } as EChartsOption
  }, [d, p])

  const name = d?.name || card?.name || ''
  return (
    <Drawer open={!!symbol} onClose={onClose} size="large" destroyOnHidden
      title={symbol ? `${name} ${symbol} · ${heightText(card)}` : ''}>
      {s.error && <div style={{ color: p.bad }}>{s.error}</div>}
      {!d && !s.error && <div style={{ color: p.muted }}>加载中</div>}
      {d && !d.available && <div style={{ color: p.muted }}>{d.message}</div>}
      {d?.available && !d.prev_close && <div>分时 <Missing why="缺昨收价，无法换算涨跌幅" /></div>}
      {option && <EChart option={option} height={360} />}
      {d?.available && (d.events ?? []).length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', fontSize: 12, marginTop: 6 }}>
          {(d.events ?? []).map((e, i) => {
            const at = parseLocal(e.at)
            return <span key={i} style={{ color: e.kind === 'open' ? p.warn : p.up }}>{KIND[e.kind] ?? e.kind} {at ? hms(at) : '未知'}{e.detail && ` · ${e.detail}`}</span>
          })}
        </div>
      )}
      {d?.available && <div style={{ fontSize: 11, color: p.muted, marginTop: 8 }}>15 秒快照留存；没有逐笔数据，快照之间的封板与开板时刻未知。成交量为相邻快照累计量之差，首点为空。</div>}
    </Drawer>
  )
}
