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
  /** 计划（空心）与实际（实心）分开画，连线表示偏差；午间休市以阴影带标出；没有计划时刻不补点。 */
  const timing = useMemo<EChartsOption | null>(() => {
    if (!data?.enabled || !data.runs.length || !data.day.selected) return null
    const day = data.day.selected
    const at = (hm: string) => Date.parse(`${day}T${hm}:00+08:00`)
    const t = (iso: string | null) => iso ? Date.parse(iso) : null
    const times = data.runs.flatMap((x) => [t(x.scheduled_at), t(x.started_at)]).filter((x): x is number => x != null)
    const lo = Math.min(at('09:00'), ...times); const hi = Math.max(at('15:30'), ...times)
    return {
      animation: false,
      grid: { left: 130, right: 24, top: 26, bottom: 30 },
      legend: { top: 0, right: 0, textStyle: { color: p.muted }, data: ['计划', '实际'] },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const x = raw as { seriesName: string; data: [number, number, string] }
        const r = data.runs[x.data[1]]
        if (!r) return ''
        const lag = r.scheduled_at && r.started_at ? (Date.parse(r.started_at) - Date.parse(r.scheduled_at)) / 1000 : null
        return `<b>${label(r.phase)}</b> <span style="opacity:.6">${r.event_key}</span><br/>计划 ${r.scheduled_at ? dateTime(r.scheduled_at).slice(11) : '未记录'}<br/>实际 ${r.started_at ? dateTime(r.started_at).slice(11) : '未记录'}${lag == null ? '' : `（${lag >= 0 ? '+' : ''}${lag.toFixed(1)}s）`}<br/>耗时 ${(r.duration_ms / 1000).toFixed(1)}s · ${r.status === 'failed' ? '失败' : '完成'}`
      } },
      xAxis: { type: 'time', min: lo, max: hi, axisLabel: { color: p.muted, formatter: (v: number) => axisHm(v) }, axisLine: { lineStyle: { color: p.border } }, splitLine: { lineStyle: { color: p.grid } } },
      yAxis: { type: 'category', inverse: true, data: data.runs.map((x) => `${dateTime(x.scheduled_at ?? x.started_at).slice(11, 16)} ${label(x.phase)}`),
        axisLabel: { color: p.text }, axisTick: { show: false }, axisLine: { show: false } },
      series: [
        { name: '计划', type: 'scatter', symbolSize: 10, itemStyle: { color: 'transparent', borderColor: p.muted, borderWidth: 2 },
          data: data.runs.flatMap((x, i) => x.scheduled_at ? [[Date.parse(x.scheduled_at), i, x.event_key]] : []),
          markArea: { silent: true, itemStyle: { color: p.panel2 }, label: { color: p.muted, fontSize: 11, position: 'insideTop' },
            data: [[{ name: '午间休市', xAxis: at('11:30') }, { xAxis: at('13:00') }]] } },
        { name: '实际', type: 'scatter', symbolSize: 10, z: 3,
          data: data.runs.flatMap((x, i) => x.started_at ? [{ value: [Date.parse(x.started_at), i, x.event_key], itemStyle: { color: x.status === 'failed' ? p.bad : p.info } }] : []) },
        ...data.runs.flatMap((x, i) => x.scheduled_at && x.started_at ? [{ name: x.event_key, type: 'line' as const, silent: true, showSymbol: false,
          lineStyle: { color: x.status === 'failed' ? p.bad : p.muted, width: 1 }, data: [[Date.parse(x.scheduled_at), i], [Date.parse(x.started_at), i]] }] : []),
      ],
    }
  }, [data, p])
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
