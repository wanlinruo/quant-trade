import { useEffect, useMemo, useRef, useState } from 'react'
import { Segmented, Select, Table, Tag, Checkbox } from 'antd'
import type { Palette } from '../theme'
import { BTS, backtest } from '../mock'
import { Alert } from 'antd'
import { EChart, Num, Panel, axisStyle, tooltipStyle } from '../ui'

const RANGES = { '1月': 21, '3月': 63, '1年': 244, '全部': 0 } as const
type RangeKey = keyof typeof RANGES

export function Backtest({ p }: { p: Palette }) {
  const [btId, setBtId] = useState(() => { const x = sessionStorage.getItem('qs-bt'); sessionStorage.removeItem('qs-bt'); return BTS.some((b) => b.id === x) ? x! : BTS[0].id })
  const cfg = BTS.find((b) => b.id === btId)!
  useEffect(() => { const f = (e: Event) => { sessionStorage.removeItem('qs-bt'); setBtId((e as CustomEvent<string>).detail) }; addEventListener('qs-bt', f); return () => removeEventListener('qs-bt', f) }, [])
  const bt = useMemo(() => backtest(cfg), [cfg])
  const [range, setRange] = useState<RangeKey>('全部')
  const [showBench, setShowBench] = useState(true)
  const groupId = useRef(`bt-${Math.random().toString(36).slice(2)}`).current
  const n = bt.days.length
  const startPct = RANGES[range] ? ((n - RANGES[range]) / n) * 100 : 0

  const markAreas = bt.top5.map((d, i) => [
    { xAxis: bt.days[d.s], itemStyle: { color: p.down, opacity: 0.08 + (4 - i) * 0.02 }, label: { show: true, formatter: `#${i + 1} ${d.depth.toFixed(1)}%`, color: p.muted, fontSize: 10, position: 'insideTop' as const } },
    { xAxis: bt.days[d.e] },
  ])

  const equityOption = useMemo(() => ({
    animation: false,
    grid: { left: 52, right: 16, top: 26, bottom: 8 },
    legend: { top: 0, left: 0, textStyle: { color: p.muted, fontSize: 12 }, itemWidth: 14, itemHeight: 3, selected: { 基准: showBench } },
    tooltip: { trigger: 'axis', ...tooltipStyle(p), axisPointer: { type: 'cross', lineStyle: { color: p.muted }, crossStyle: { color: p.muted }, label: { backgroundColor: p.panel2, color: p.text } },
      valueFormatter: (v: number) => (typeof v === 'number' ? `${((v - 1) * 100).toFixed(2)}%` : v) },
    xAxis: { type: 'category', data: bt.days, boundaryGap: false, ...axisStyle(p), axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => `${((v - 1) * 100).toFixed(0)}%` } },
    dataZoom: [{ type: 'inside', start: startPct, end: 100 }],
    series: [
      { name: '策略', type: 'line', data: bt.equity, symbol: 'none', lineStyle: { width: 1.8, color: p.line }, itemStyle: { color: p.line }, areaStyle: { color: p.line, opacity: 0.08 }, markArea: { silent: true, data: markAreas } },
      { name: '基准', type: 'line', data: bt.bench, symbol: 'none', lineStyle: { width: 1.2, color: p.muted, type: 'dashed' }, itemStyle: { color: p.muted } },
    ],
  }), [p, startPct, showBench, bt]) // eslint-disable-line react-hooks/exhaustive-deps

  const ddOption = useMemo(() => ({
    animation: false,
    grid: { left: 52, right: 16, top: 6, bottom: 52 },
    tooltip: { trigger: 'axis', ...tooltipStyle(p), axisPointer: { type: 'line', lineStyle: { color: p.muted } }, valueFormatter: (v: number) => `${v}%` },
    xAxis: { type: 'category', data: bt.days, boundaryGap: false, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', max: 0, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: '{value}%' } },
    dataZoom: [
      { type: 'inside', start: startPct, end: 100 },
      { type: 'slider', start: startPct, end: 100, height: 18, bottom: 6, borderColor: p.border, backgroundColor: p.panel2, fillerColor: p.accent + '22', handleStyle: { color: p.accent }, textStyle: { color: p.muted }, dataBackground: { lineStyle: { color: p.muted }, areaStyle: { color: p.idle } } },
    ],
    series: [{ name: '回撤', type: 'line', data: bt.dd, symbol: 'none', lineStyle: { width: 1.2, color: p.down }, itemStyle: { color: p.down }, areaStyle: { color: p.down, opacity: 0.22 }, markArea: { silent: true, data: markAreas.map(([a, b]) => [{ ...a, label: { show: false } }, b]) } }],
  }), [p, startPct, bt]) // eslint-disable-line react-hooks/exhaustive-deps

  const months = Object.entries(bt.monthly)
  const years = [...new Set(months.map(([m]) => m.slice(0, 4)))]
  const monthOption = useMemo(() => ({
    animation: false,
    grid: { left: 44, right: 8, top: 4, bottom: 22 },
    tooltip: { ...tooltipStyle(p), formatter: (q: { value: [number, number, number] }) => `${years[q.value[1]]}-${String(q.value[0] + 1).padStart(2, '0')}：${q.value[2] > 0 ? '+' : ''}${q.value[2]}%` },
    xAxis: { type: 'category', data: Array.from({ length: 12 }, (_, i) => `${i + 1}月`), ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'category', data: years, ...axisStyle(p), splitLine: { show: false } },
    visualMap: { show: false, min: -8, max: 8, inRange: { color: [p.down, p.panel2, p.up] } },
    series: [{ type: 'heatmap', data: months.map(([m, v]) => [Number(m.slice(5)) - 1, years.indexOf(m.slice(0, 4)), v]), label: { show: true, color: p.head, fontSize: 11, formatter: (q: { value: number[] }) => `${q.value[2] > 0 ? '+' : ''}${q.value[2]}` }, itemStyle: { borderColor: p.panel, borderWidth: 2, borderRadius: 2 } }],
  }), [p, bt]) // eslint-disable-line react-hooks/exhaustive-deps

  const s = bt.stats
  const banner = [
    { k: '年化收益', v: `${s.annual >= 0 ? '+' : ''}${s.annual.toFixed(1)}%`, c: s.annual >= 0 ? p.up : p.down },
    { k: '累计收益', v: `${s.total >= 0 ? '+' : ''}${s.total.toFixed(1)}%`, c: s.total >= 0 ? p.up : p.down },
    { k: '最大回撤', v: `${s.maxdd.toFixed(1)}%`, c: p.down },
    { k: 'Sharpe', v: s.sharpe.toFixed(2), c: p.head },
    { k: '胜率', v: `${s.win}%`, c: p.head },
    { k: '交易笔数', v: String(s.n), c: p.head },
    { k: '手续费', v: s.fee.toLocaleString(), c: p.head },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Select size="middle" value={btId} onChange={setBtId} style={{ width: 380 }} popupMatchSelectWidth={false}
          options={BTS.map((b) => ({ value: b.id, label: <span><span style={{ fontFamily: 'Menlo, monospace' }}>{b.id}</span><span style={{ color: p.muted }}> · {b.label} · {b.done} 完成</span></span> }))} />
        <Tag>{bt.days[0]} → {bt.days.at(-1)}</Tag><Tag>基准：沪深 300</Tag><Tag color="default">示意数据</Tag>
      </div>

      {cfg.warn && <Alert type="warning" showIcon message={`这次回测有警告：${cfg.warn}`} />}
      {/* 统计横幅（QuantConnect 做法） */}
      <section style={{ display: 'grid', gridTemplateColumns: `repeat(${banner.length},1fr)`, background: p.panel, border: `1px solid ${p.border}`, borderRadius: 4 }}>
        {banner.map((b, i) => (
          <div key={b.k} style={{ padding: '10px 14px', borderLeft: i ? `1px solid ${p.border}` : undefined }}>
            <div style={{ color: p.muted, fontSize: 12 }}>{b.k}</div>
            <Num size={20} weight={600} color={b.c}>{b.v}</Num>
          </div>
        ))}
      </section>

      <Panel
        p={p}
        title="策略跑赢基准了吗？"
        info="上：累计收益（策略 vs 基准）；下：水下回撤图，标出最深的 5 段。两张图共用十字光标，滚轮或拖动下方滑块缩放会同步。"
        extra={<div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <Checkbox checked={showBench} onChange={(e) => setShowBench(e.target.checked)}>显示基准</Checkbox>
          <Segmented size="small" value={range} onChange={(v) => setRange(v as RangeKey)} options={Object.keys(RANGES)} />
        </div>}
      >
        <EChart height={260} option={equityOption} group={groupId} />
        <div style={{ color: p.muted, fontSize: 12, margin: '2px 0 0 52px' }}>回撤（最深 5 段已标注）</div>
        <EChart height={150} option={ddOption} group={groupId} />
      </Panel>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 10 }}>
        <Panel p={p} title="哪些月份赚、哪些月份亏？" info="月度收益率（%），红为正、绿为负。">
          <EChart height={150} option={monthOption} />
        </Panel>
        <Panel p={p} title="交易明细" info="点击 + 展开该笔交易的入场依据与成交回报。可按收益排序。" bodyStyle={{ padding: '4px 0 0' }}>
          <Table
            size="small" dataSource={bt.trades} pagination={{ pageSize: 6, size: 'small' }}
            expandable={{ expandedRowRender: (r) => <div style={{ color: p.muted, fontSize: 12 }}>入场依据：{r.reason} · 委托 09:25:10 · 成交 09:30:02（示意）</div> }}
            columns={[
              { title: '日期', dataIndex: 'date', width: 100, render: (v) => <Num color={p.muted}>{v}</Num> },
              { title: '代码', dataIndex: 'code', width: 104, render: (v) => <Num>{v}</Num> },
              { title: '名称', dataIndex: 'name' },
              { title: '入场价', dataIndex: 'entry', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
              { title: '持有', dataIndex: 'hold', align: 'right', render: (v) => `${v} 天` },
              { title: '收益', dataIndex: 'ret', align: 'right', sorter: (a, b) => a.ret - b.ret, render: (v: number) => <Num color={v >= 0 ? p.up : p.down} weight={600}>{v >= 0 ? '+' : ''}{v.toFixed(2)}%</Num> },
            ]}
          />
        </Panel>
      </div>
    </div>
  )
}
