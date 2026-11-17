import { useEffect, useRef, type ReactNode, type CSSProperties } from 'react'
import * as echarts from 'echarts'
import { Tooltip, App } from 'antd'
import { InfoCircleOutlined } from '@ant-design/icons'
import type { Palette } from './theme'
import { statusIcon, statusText, type Status } from './mock'

/** ECharts 包装：自适应尺寸；group 相同的图表共用十字光标与缩放。 */
export function EChart({ option, height, group, onClick, style }: {
  option: echarts.EChartsCoreOption; height: number; group?: string
  onClick?: (p: echarts.ECElementEvent) => void; style?: CSSProperties
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inst = useRef<echarts.ECharts | null>(null)
  const clickRef = useRef(onClick)
  clickRef.current = onClick
  useEffect(() => {
    const c = echarts.init(ref.current!, undefined, { renderer: 'canvas' })
    inst.current = c
    if (group) {
      c.group = group
      echarts.connect(group)
    }
    c.on('click', (p) => clickRef.current?.(p as echarts.ECElementEvent))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(ref.current!)
    return () => { ro.disconnect(); c.dispose() }
  }, [group])
  useEffect(() => {
    const o = option as Record<string, unknown>
    const tt = o.tooltip
    // 统一：提示框不超出图表区域；可点击的图表悬停显示手型
    const tooltip = tt && !Array.isArray(tt) ? { confine: true, transitionDuration: 0, ...(tt as object) } : tt
    inst.current?.setOption({ ...o, ...(tooltip ? { tooltip } : {}) }, true)
  }, [option])
  return <div ref={ref} role="img" style={{ height, width: '100%', cursor: onClick ? 'pointer' : undefined, ...style }} />
}

/** 面板：标题写它回答的问题；ⓘ 放口径；右上角放操作。 */
export function Panel({ title, info, extra, children, p, style, bodyStyle }: {
  title: ReactNode; info?: ReactNode; extra?: ReactNode; children: ReactNode; p: Palette
  style?: CSSProperties; bodyStyle?: CSSProperties
}) {
  return (
    <section style={{ background: p.panel, border: `1px solid ${p.border}`, borderRadius: 4, display: 'flex', flexDirection: 'column', minWidth: 0, ...style }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px 0', minHeight: 30 }}>
        <span style={{ color: p.head, fontWeight: 600, fontSize: 13 }}>{title}</span>
        {info && (
          <Tooltip title={info} placement="topLeft">
            <InfoCircleOutlined style={{ color: p.muted, fontSize: 12, cursor: 'help' }} />
          </Tooltip>
        )}
        <span style={{ flex: 1 }} />
        {extra}
      </header>
      <div style={{ padding: '6px 12px 10px', flex: 1, minWidth: 0, ...bodyStyle }}>{children}</div>
    </section>
  )
}

export function statusColor(p: Palette, s: Status) {
  return { ok: p.ok, warn: p.warn, bad: p.bad, idle: p.idle, running: p.info, closed: p.idle }[s]
}

/** 状态徽标：符号 + 文字 + 颜色（IBM Carbon：不只靠颜色）。 */
export function StatusTag({ s, p, text }: { s: Status; p: Palette; text?: string }) {
  const c = statusColor(p, s)
  const fg = s === 'idle' || s === 'closed' ? p.muted : c
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, lineHeight: '18px', padding: '0 7px', borderRadius: 3, border: `1px solid ${fg}66`, color: fg, whiteSpace: 'nowrap' }}>
      <span style={{ fontSize: 10 }}>{statusIcon[s]}</span>{text ?? statusText[s]}
    </span>
  )
}

export function Num({ children, color, size = 13, weight = 500 }: { children: ReactNode; color?: string; size?: number; weight?: number }) {
  return <span style={{ fontFamily: '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace', fontVariantNumeric: 'tabular-nums', color, fontSize: size, fontWeight: weight }}>{children}</span>
}

export function Spark({ data, color, height = 26, area = true }: { data: number[]; color: string; height?: number; area?: boolean }) {
  return (
    <EChart
      height={height}
      option={{
        animation: false,
        grid: { left: 0, right: 0, top: 2, bottom: 2 },
        xAxis: { type: 'category', show: false, data: data.map((_, i) => i) },
        yAxis: { type: 'value', show: false, scale: true },
        tooltip: { show: false },
        series: [{ type: 'line', data, symbol: 'none', smooth: false, lineStyle: { width: 1.4, color }, areaStyle: area ? { color, opacity: 0.12 } : undefined }],
      }}
    />
  )
}

/** 常用 ECharts 坐标与提示框样式 */
export function axisStyle(p: Palette) {
  return {
    axisLine: { lineStyle: { color: p.border } },
    axisTick: { show: false },
    axisLabel: { color: p.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: p.grid } },
  }
}
export function tooltipStyle(p: Palette) {
  return {
    backgroundColor: p.panel2,
    borderColor: p.border,
    textStyle: { color: p.text, fontSize: 12 },
    extraCssText: 'box-shadow:0 6px 18px rgba(0,0,0,.35);border-radius:4px',
  }
}

/** 编号类文本：等宽、点击复制（委托号、哈希、run-id）。 */
export function Copy({ children, color, size = 12 }: { children: string; color?: string; size?: number }) {
  const { message } = App.useApp()
  return (
    <Tooltip title="点击复制" mouseEnterDelay={0.4}>
      <span className="qs-copy" onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(children); message.success({ content: `已复制 ${children}`, duration: 1.2 }) }}
        style={{ fontFamily: '"SF Mono", Menlo, Consolas, monospace', fontSize: size, color }}>{children}</span>
    </Tooltip>
  )
}
