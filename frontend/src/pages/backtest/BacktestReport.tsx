import { useMemo, useState } from 'react'
import { Alert, Checkbox, Collapse, Segmented, Table } from 'antd'
import { backtestFileUrl, type BacktestDetail } from '../../api/research'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { Value } from '../../page/Value'
import { signedPct } from '../../page/format'
import { usePalette } from '../../theme/context'
import { MonthlyHeatmap } from './MonthlyHeatmap'

type Report = Extract<BacktestDetail, { report: true }>
type Range = 'm1' | 'm3' | 'y1' | 'all'
const ENTRY: Record<string, string> = { continuous: '连续竞价判定（09:31 分钟线）', open: '开盘价成交（原口径）' }
const num = (v: unknown, digits: number) => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : String(v ?? ''))
const known = (v: number | null) => (v === null ? '未知' : v)

export function BacktestReport({ d }: { d: Report }) {
  const p = usePalette()
  const s = d.summary
  const last = d.series.at(-1)
  const entry = String(d.job?.config?.entry_fill ?? s.entry_fill ?? '')
  const [range, setRange] = useState<Range>('all')
  const [showBench, setShowBench] = useState(true)
  /** 原型：收益与回撤上下两个子图共用横轴与缩放；基准缺某日时断开，不连线。 */
  const chart = useMemo<EChartsOption>(() => {
    const dates = d.series.map((x) => x.date)
    const lastDate = dates.at(-1)
    const back = { m1: 1, m3: 3, y1: 12 }[range as Exclude<Range, 'all'>]
    let start = dates[0]
    if (back && lastDate) {
      const t = new Date(`${lastDate}T00:00:00Z`); t.setUTCMonth(t.getUTCMonth() - back)
      const cut = t.toISOString().slice(0, 10)
      start = dates.find((x) => x >= cut) ?? dates[0]
    }
    const pctAxis = (v: number) => `${(v * 100).toFixed(0)}%`
    return {
      animation: false,
      legend: { top: 0, textStyle: { color: p.muted }, data: showBench ? ['策略', '基准', '超额', '回撤'] : ['策略', '回撤'] },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] }, valueFormatter: (v) => typeof v === 'number' ? signedPct(v) ?? '—' : '—' },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [{ left: 56, right: 16, top: 28, height: '52%' }, { left: 56, right: 16, top: '68%', height: '18%' }],
      xAxis: [
        { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: p.border } } },
        { type: 'category', data: dates, gridIndex: 1, axisLabel: { color: p.muted }, axisLine: { lineStyle: { color: p.border } } },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, axisLabel: { color: p.muted, formatter: pctAxis }, splitLine: { lineStyle: { color: p.grid } } },
        { type: 'value', gridIndex: 1, max: 0, axisLabel: { color: p.muted, formatter: pctAxis }, splitLine: { lineStyle: { color: p.grid } } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], startValue: start, endValue: lastDate },
        { type: 'slider', xAxisIndex: [0, 1], startValue: start, endValue: lastDate, height: 16, bottom: 4, borderColor: p.border, textStyle: { color: p.muted } },
      ],
      series: [
        { name: '策略', type: 'line', showSymbol: false, connectNulls: false, data: d.series.map((x) => [x.date, x.strategy]), lineStyle: { color: p.line }, itemStyle: { color: p.line } },
        ...(showBench ? [
          { name: '基准', type: 'line' as const, showSymbol: false, connectNulls: false, data: d.series.map((x) => [x.date, x.benchmark]), lineStyle: { color: p.muted, type: 'dashed' as const }, itemStyle: { color: p.muted } },
          { name: '超额', type: 'line' as const, showSymbol: false, connectNulls: false, data: d.series.map((x) => [x.date, x.excess]), lineStyle: { color: p.accent }, itemStyle: { color: p.accent } },
        ] : []),
        {
          name: '回撤', type: 'line', xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, connectNulls: false, areaStyle: { color: `${p.down}33` }, lineStyle: { color: p.down }, itemStyle: { color: p.down },
          data: d.series.map((x) => [x.date, x.drawdown]),
          markArea: { silent: true, itemStyle: { color: `${p.warn}22` },
            data: d.episodes.map((e) => [{ xAxis: e.peak_date }, { xAxis: e.recovery_date ?? d.series.at(-1)?.date }]) },
        },
      ],
    }
  }, [d.series, d.episodes, p, range, showBench])

  return (
    <>
      {d.warnings.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} title="这次回测有需要注意的地方"
          description={<ul style={{ margin: 0, paddingLeft: 18 }}>{d.warnings.map((w) => <li key={w}>{w}</li>)}</ul>} />
      )}
      <Hero s={d.warnings.length ? 'warn' : 'ok'} statusText={d.warnings.length ? `${d.warnings.length} 条警告` : '已完成'}
        title={`${s.strategy} · ${s.start} ~ ${s.end}`}
        subtitle={`回测 ${d.name} · 入场口径 ${ENTRY[entry] ?? '未记录'} · 费用模型 ${s.cost_model || '未知'}`}
        kpis={[
          { k: '区间收益', v: <Value v={signedPct(s.cumulative_return)} why="报告没有累计收益" /> },
          { k: '基准收益', v: <Value v={signedPct(d.metrics.benchmark_return)} why="报告没有基准收益" /> },
          { k: '超额（期末）', v: <Value v={signedPct(last?.excess ?? null)} why="期末缺基准，无法计算超额" /> },
          { k: '最大回撤', v: <Value v={signedPct(d.metrics.max_drawdown)} why="报告没有最大回撤" /> },
          { k: '买 / 卖', v: `${known(s.buys)} / ${known(s.sells)}` },
          { k: '被拒', v: known(s.rejects) },
        ]} />
      <Panel title="跑赢基准了吗？" info="上：累计收益相对首日（基准缺某日时该日断开，不连线）；下：回撤 = 净值 ÷ 此前最高 − 1，阴影为最深 5 段。两图共用横轴，可拖动下方滑条或滚轮缩放。"
        extra={<div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Checkbox checked={showBench} onChange={(e) => setShowBench(e.target.checked)}>显示基准</Checkbox>
          <Segmented size="small" value={range} onChange={(v) => setRange(v as Range)} options={[{ label: '1月', value: 'm1' }, { label: '3月', value: 'm3' }, { label: '1年', value: 'y1' }, { label: '全部', value: 'all' }]} />
        </div>}>
        <EChart option={chart} height={420} />
        {d.episodes.length === 0 ? <div style={{ color: p.muted }}>没有回撤</div> : (
          <Table size="small" pagination={false} rowKey="peak_date" dataSource={d.episodes} scroll={{ x: 'max-content' }}
            columns={[
              { title: '前高', dataIndex: 'peak_date' }, { title: '谷底', dataIndex: 'trough_date' },
              { title: '收复', dataIndex: 'recovery_date', render: (v: string | null) => v ?? <span style={{ color: p.warn }}>未收复</span> },
              { title: '深度', dataIndex: 'depth', render: (v: number) => <span style={{ color: p.down }}>{signedPct(v)}</span> },
            ]} />
        )}
      </Panel>
      <div className="qs-grid-12">
      <Panel title="每月表现如何？" info="来自 monthly_returns.csv。">
        {d.monthly.length === 0 ? <div style={{ color: p.muted }}>没有月度数据</div> : <MonthlyHeatmap months={d.monthly} />}
      </Panel>
      <Panel title="交易明细" extra={<span style={{ fontSize: 12, color: p.muted }}>共 {d.fills_total} 笔，显示最后 {d.fills.length} 笔</span>}>
        <Table size="small" rowKey={(r, i) => `${String(r.filled_at)}-${i}`} dataSource={d.fills} scroll={{ x: 'max-content' }} pagination={{ pageSize: 20, hideOnSinglePage: true }}
          columns={[
            // 回测成交时刻是无时区的市场本地时间：只格式化到秒，不按浏览器时区重新解释
            { title: '时间', dataIndex: 'filled_at', render: (v) => String(v).replace('T', ' ').slice(0, 19) },
            { title: '代码', dataIndex: 'symbol' },
            { title: '方向', dataIndex: 'side', render: (v) => <span style={{ color: v === 'buy' ? p.up : p.down }}>{v === 'buy' ? '买' : v === 'sell' ? '卖' : String(v)}</span> },
            { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => num(v, 0) },
            { title: '价格', dataIndex: 'price', align: 'right', render: (v) => num(v, 3) },
            { title: '费用', dataIndex: 'fee', align: 'right', render: (v) => num(v, 2) },
            { title: '金额', dataIndex: 'notional', align: 'right', render: (v) => num(v, 2) },
          ]} />
        {d.rejects.length > 0 && (
          <Collapse size="small" style={{ marginTop: 8 }} items={[{ key: 'r', label: `被拒的单（${d.rejects.length}）`, children: (
            <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={d.rejects} scroll={{ x: 'max-content' }}
              columns={Object.keys(d.rejects[0]).map((k) => ({ title: k, dataIndex: k, render: (v: unknown) => String(v ?? '') }))} />
          ) }]} />
        )}
      </Panel>
      </div>
      <Panel title="报告文件">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: 12 }}>
          {d.files.map((f) => <a key={f.name} href={backtestFileUrl(d.name, f.name)}>{f.name}</a>)}
        </div>
      </Panel>
    </>
  )
}
