import { useEffect, useRef, useState } from 'react'
import {
  createChart, BaselineSeries, HistogramSeries, createSeriesMarkers, ColorType, CrosshairMode, LineStyle,
  type UTCTimestamp, type Time,
} from 'lightweight-charts'
import type { Palette } from './theme'
import { intraday } from './mock'
import { Num } from './ui'
import { PM, BAD } from './scene'

const BJ = 8 * 3600 // 图表按 UTC 显示，平移到北京时间
const PREV_CLOSE = 3420.0

/** 上证分时（TradingView Lightweight Charts）：昨收为基线，上方红、下方绿；十字光标图例；策略事件标记。 */
export function Intraday({ p, height = 300 }: { p: Palette; height?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [legend, setLegend] = useState<{ t: string; v: number; vol: number } | null>(null)
  const data = intraday()
  const last = [...data].reverse().find((d) => d.value != null)!

  useEffect(() => {
    const chart = createChart(ref.current!, {
      height,
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: p.panel }, textColor: p.muted, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.border },
      timeScale: { borderColor: p.border, timeVisible: true, secondsVisible: false, fixRightEdge: true },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: p.muted, labelBackgroundColor: p.panel2 }, horzLine: { color: p.muted, labelBackgroundColor: p.panel2 } },
    })
    const bopt = {
      baseValue: { type: 'price' as const, price: PREV_CLOSE },
      topLineColor: p.up, topFillColor1: p.up + '40', topFillColor2: p.up + '05',
      bottomLineColor: p.down, bottomFillColor1: p.down + '05', bottomFillColor2: p.down + '40',
      lineWidth: 2 as const, priceLineVisible: false,
    }
    // 缺失区间前后拆成两条序列，避免图表把缺口连成直线（那等于补值）
    const cut = data.findIndex((d) => d.value == null)
    const seg = (keep: (i: number) => boolean) => data.map((d, i) => (d.value == null || !keep(i) ? { time: (d.time + BJ) as UTCTimestamp } : { time: (d.time + BJ) as UTCTimestamp, value: d.value }))
    const px = chart.addSeries(BaselineSeries, { ...bopt, lastValueVisible: cut < 0 })
    px.setData(seg((i) => cut < 0 || i < cut))
    const px2 = cut < 0 ? null : chart.addSeries(BaselineSeries, { ...bopt, lastValueVisible: true })
    px2?.setData(seg((i) => i > cut))
    px.createPriceLine({ price: PREV_CLOSE, color: p.muted, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: '昨收' })
    const vol = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false })
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    vol.setData(data.map((d, i) => (d.vol == null || d.value == null ? { time: (d.time + BJ) as UTCTimestamp } : { time: (d.time + BJ) as UTCTimestamp, value: d.vol, color: (d.value >= (data[i - 1]?.value ?? PREV_CLOSE) ? p.up : p.down) + '88' })))
    createSeriesMarkers(px, [
      { time: (data[3].time + BJ) as UTCTimestamp, position: 'aboveBar', color: p.info, shape: 'arrowDown', text: '竞价 ✓' },
      ...(BAD ? [{ time: (data[60].time + BJ) as UTCTimestamp, position: 'aboveBar' as const, color: p.bad, shape: 'square' as const, text: '10:31–10:36 无快照（隧道中断）' }] : []),
    ])
    chart.timeScale().fitContent()
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) return setLegend(null)
      const v = (param.seriesData.get(px) ?? (px2 && param.seriesData.get(px2))) as { value: number } | undefined
      const h = param.seriesData.get(vol) as { value: number } | undefined
      const t = new Date(((param.time as number) - BJ) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' })
      if (v) setLegend({ t, v: v.value, vol: h?.value ?? 0 })
    })
    return () => chart.remove()
  }, [p, height]) // eslint-disable-line react-hooks/exhaustive-deps

  const cur = legend ?? { t: PM ? '15:00 收' : BAD ? '10:48' : '10:42', v: last.value!, vol: last.vol ?? 0 }
  const pct = ((cur.v / PREV_CLOSE - 1) * 100)
  const c = pct >= 0 ? p.up : p.down
  return (
    <div style={{ position: 'relative' }}>
      <div style={{ position: 'absolute', zIndex: 2, left: 4, top: 2, display: 'flex', gap: 14, fontSize: 12, pointerEvents: 'none' }}>
        <span style={{ color: p.muted }}>{cur.t}</span>
        <Num color={c} weight={600}>{cur.v.toFixed(2)}</Num>
        <Num color={c}>{pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</Num>
        <span style={{ color: p.muted }}>量 <Num>{(cur.vol / 1e8).toFixed(2)}亿</Num></span>
      </div>
      <div ref={ref} style={{ height }} />
    </div>
  )
}

export type { Time }
