import { Button, Result, Table } from 'antd'
import { useState } from 'react'
import { fetchRuns, reportUrl } from '../../api/trading'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { DaySelect, label, useTradingDay } from './common'

export function RunsPage() {
  const p = usePalette(); const day = useTradingDay(); const [copied, setCopied] = useState(false)
  const { data, error } = useFetch((s) => fetchRuns(day, s), [day])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="运行记录未启用" />
  const d = data; const failed = d.runs.filter((r) => r.status === 'failed').length
  const copy = async () => { if (!d.report_text) return; await navigator.clipboard.writeText(d.report_text); setCopied(true) }
  return <>
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}><DaySelect value={d.day} /></div>
    <Hero s={failed ? 'bad' : d.runs.length ? 'ok' : 'idle'} title={failed ? `${failed} 个策略事件失败` : d.runs.length ? `${d.runs.length} 个策略事件已记录` : '当日没有运行记录'}
      subtitle="计划时刻与实际开始分别展示；缺计划记录时不推算。" />
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
