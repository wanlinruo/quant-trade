import { Button, Result, Table } from 'antd'
import { useMemo, useState } from 'react'
import { fetchRuns, reportUrl } from '../../api/trading'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { axisHm, dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, label, useTradingDay } from './common'
import { EChart, type EChartsOption } from '../../page/EChart'

export function RunsPage() {
  const p = usePalette(); const day = useTradingDay(); const [copied, setCopied] = useState(false)
  const { data, error } = useFetch((s) => fetchRuns(day, s), [day])
  const timing = useMemo<EChartsOption | null>(() => data?.enabled && data.runs.length ? ({
    grid: { left: 100, right: 24, top: 18, bottom: 34 }, tooltip: { trigger: 'item' },
    xAxis: { type: 'time', axisLabel: { formatter: (v: number) => axisHm(v) }, splitLine: { lineStyle: { color: p.grid } } },
    yAxis: { type: 'category', data: data.runs.map((x) => x.phase) },
    series: [
      { name: '计划', type: 'scatter', symbolSize: 9, itemStyle: { color: 'transparent', borderColor: p.muted, borderWidth: 2 }, data: data.runs.flatMap((x, i) => x.scheduled_at ? [[new Date(x.scheduled_at).getTime(), i, x.event_key]] : []) },
      { name: '实际', type: 'scatter', symbolSize: 9, itemStyle: { color: p.info }, data: data.runs.flatMap((x, i) => x.started_at ? [[new Date(x.started_at).getTime(), i, x.event_key]] : []) },
      ...data.runs.flatMap((x, i) => x.scheduled_at && x.started_at ? [{ name: x.event_key, type: 'line' as const, showSymbol: false, lineStyle: { color: x.status === 'failed' ? p.bad : p.muted, width: 1 }, data: [[new Date(x.scheduled_at).getTime(), i], [new Date(x.started_at).getTime(), i]] }] : []),
    ],
  }) : null, [data, p])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="运行记录未启用" />
  const d = data; const failed = d.runs.filter((r) => r.status === 'failed').length
  const auction = d.runs.find((r) => r.phase === 'auction')
  const orders = d.runs.reduce((n, r) => n + r.orders, 0); const rejects = d.runs.reduce((n, r) => n + r.rejects, 0)
  const lags = d.runs.flatMap((r) => r.scheduled_at && r.started_at ? [{ run: r, seconds: (Date.parse(r.started_at) - Date.parse(r.scheduled_at)) / 1000 }] : [])
  const maxLag = lags.length ? lags.reduce((a, b) => (b.seconds > a.seconds ? b : a)) : null
  const slowest = d.runs.length ? d.runs.reduce((a, b) => (b.duration_ms > a.duration_ms ? b : a)) : null
  const copy = async () => { if (!d.report_text) return; await navigator.clipboard.writeText(d.report_text); setCopied(true) }
  return <>
    <Hero s={failed ? 'bad' : d.runs.length ? 'ok' : 'idle'} title={failed ? `${failed} 个策略事件失败` : d.runs.length ? `${d.runs.length} 个策略事件全部完成` : '当日没有运行记录'}
      subtitle={`${dayNote(d.day)} · 计划时刻与实际开始分别展示；缺计划记录时不推算。`}
      kpis={[
        { k: '候选（竞价）', v: auction?.candidates ?? '—', sub: auction ? `入档 ${auction.routed ?? '—'} · 闸门通过 ${auction.passed ?? '—'}` : '当日没有竞价事件' },
        { k: '发出委托 / 被拒', v: `${orders} / ${rejects}`, color: rejects ? p.warn : undefined },
        { k: '最大开始偏差', v: maxLag == null ? '—' : `${maxLag.seconds.toFixed(1)}s`, sub: maxLag ? `${label(maxLag.run.phase)}（实际 − 计划）` : '缺计划时刻' },
        { k: '最慢一步', v: slowest ? `${(slowest.duration_ms / 1000).toFixed(1)}s` : '—', sub: slowest ? label(slowest.phase) : undefined },
      ]} />
    <Panel title="策略事件是否按时触发？" info="空心点是计划、实心点是实际；连线表示迟到或提前。没有计划时刻不补点。">
      {timing ? <EChart option={timing} height={Math.max(220, d.runs.length * 38)} /> : <Missing why="当日没有可绘制的计划/实际时点" />}
    </Panel>
    <Panel title="策略事件是否按时完成？" info="计划空心点/实际实心点的事实先以表格精确展示；计划时刻缺失不补。">
      <Table size="small" pagination={false} rowKey="event_key" dataSource={d.runs} scroll={{ x: 'max-content' }} columns={[
        { title: '事件', dataIndex: 'event_key' }, { title: '阶段', dataIndex: 'phase', render: label },
        { title: '计划', dataIndex: 'scheduled_at', render: (v) => v ? dateTime(v) : <Missing why="调度记录未关联" /> },
        { title: '实际开始', dataIndex: 'started_at', render: dateTime }, { title: '完成', dataIndex: 'finished_at', render: dateTime },
        { title: '耗时', dataIndex: 'duration_ms', render: (v) => `${(v / 1000).toFixed(2)}s` },
        { title: '结果', dataIndex: 'status', render: (v) => <StatusTag s={v === 'failed' ? 'bad' : 'ok'} text={v === 'failed' ? '失败' : '完成'} /> },
        { title: '摘要', dataIndex: 'summary' },
      ]} />
    </Panel>
    <Panel title="候选如何被过滤？">
      <Table size="small" pagination={false} rowKey="event_key" dataSource={d.runs.filter((r) => r.candidates != null)} columns={[
        { title: '事件', dataIndex: 'event_key' }, { title: '候选', dataIndex: 'candidates', align: 'right' },
        { title: '入档', dataIndex: 'routed', align: 'right', render: (v) => v ?? <Missing why="该版本未记录入档数" /> },
        { title: '闸门通过', dataIndex: 'passed', align: 'right' }, { title: '下单', dataIndex: 'orders', align: 'right' }, { title: '拒绝', dataIndex: 'rejects', align: 'right' },
        { title: '过滤原因', dataIndex: 'reasons', render: (v: Record<string, number>) => Object.entries(v).map(([k, n]) => `${k} ${n}`).join('；') || '—' },
      ]} />
    </Panel>
    <Panel title="当日日报" extra={<div style={{ display: 'flex', gap: 8 }}><Button size="small" disabled={!d.report_text} onClick={copy}>{copied ? '已复制' : '复制'}</Button>{d.report_text && <Button size="small" href={reportUrl(d.day.selected)}>下载 .txt</Button>}</div>}>
      {d.report_text ? <pre style={{ whiteSpace: 'pre-wrap', color: p.text, margin: 0 }}>{d.report_text}</pre> : <Missing why={d.missing.report ?? '没有日报'} />}
    </Panel>
  </>
}
