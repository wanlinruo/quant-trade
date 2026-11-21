import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, CustomChart, GraphChart, HeatmapChart, LineChart, ScatterChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import { usePalette } from '../theme/context'

echarts.use([LineChart, BarChart, CustomChart, GraphChart, HeatmapChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomComponent, CanvasRenderer])

export type { EChartsOption }

/** 图表容器：按需注册组件，跟随容器尺寸与主题。缺口靠调用方保留 null 并设 connectNulls:false。 */
export function EChart({ option, height = 240, onClick }: { option: EChartsOption; height?: number; onClick?: (params: unknown) => void }) {
  const p = usePalette()
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    if (!el.current) return
    const c = echarts.init(el.current)
    chart.current = c
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); c.dispose(); chart.current = null }
  }, [])

  useEffect(() => {
    chart.current?.setOption({
      backgroundColor: 'transparent',
      textStyle: { color: p.text, fontSize: 11 },
      color: [p.line, p.line2, p.up, p.down, p.accent],
      tooltip: { trigger: 'axis' },
      ...option,
    }, { notMerge: true })
  }, [option, p])

  useEffect(() => {
    const c = chart.current
    if (!c || !onClick) return
    c.on('click', onClick)
    return () => { c.off('click', onClick) }
  }, [onClick])

  return <div ref={el} style={{ width: '100%', height }} />
}
