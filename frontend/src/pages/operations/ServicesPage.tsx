import { Drawer, Result, Table } from 'antd'
import { useState } from 'react'
import { fetchServices, type ServiceRunItem } from '../../api/operations'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { pct } from '../../page/format'
import { dateTime } from '../../shell/time'
import { Missing } from '../../ui/Missing'
import { stateTag } from './common'

export function ServicesPage() {
  const day = new URLSearchParams(location.search).get('day')
  const { data, error } = useFetch((s) => fetchServices(day, s), [day])
  const [run, setRun] = useState<ServiceRunItem | null>(null)
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="服务运行记录未配置" />
  const failed = data.services.filter((x) => x.latest_state === 'failed').length
  const unknown = data.services.filter((x) => x.latest_state === 'unknown').length
  return <>
    <Hero s={failed ? 'bad' : unknown ? 'warn' : 'ok'} title={failed ? `${failed} 个服务最近失败` : unknown ? `${unknown} 个服务状态未知` : '服务运行正常'}
      subtitle="历史失败保留；后续成功显示已修复，不覆盖原失败。网页不提供补跑。"
      kpis={[{ k: '服务数', v: data.services.length }, { k: '当日运行', v: data.runs.length }, { k: '状态未知', v: unknown }]} />
    <Panel title="近 30 日服务运行怎样？" info="格子只来自 ServiceRun 或 health-observer 观测；无记录为未知。">
      <div data-testid="service-matrix" style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'separate', borderSpacing: 3, minWidth: 1050 }}><thead><tr><th style={{ position: 'sticky', left: 0 }}>服务</th>{data.days.map((d) => <th key={d} title={d}>{d.slice(5)}</th>)}</tr></thead><tbody>{data.services.map((r) => <tr key={r.id}><th style={{ textAlign: 'left', position: 'sticky', left: 0 }}>{r.name}</th>{r.cells.map((c) => <td key={c.day} title={`${c.day} ${c.detail ?? ''}`}>{stateTag(c.state)}</td>)}</tr>)}</tbody></table></div>
    </Panel>
    <Panel title={`${data.selected_day} 服务健康时间线`} info="来自 health-observer 持久化观测；没有观测不推断正常。">
      {data.health.length ? <Table size="small" pagination={false} rowKey={(x) => `${x.service_id}-${x.observed_at}`} dataSource={data.health} columns={[
        { title: '时间', dataIndex: 'observed_at', render: dateTime }, { title: '服务', dataIndex: 'service_id' },
        { title: '总体', dataIndex: 'state', render: stateTag }, { title: '进程', dataIndex: 'process_state' },
        { title: '业务', dataIndex: 'business_state' }, { title: '原因', dataIndex: 'reason_code' },
      ]} /> : <Missing why={data.missing.health ?? '当日没有健康观察记录'} />}
    </Panel>
    <Panel title="各服务现在与 30 日表现如何？">
      <Table size="small" rowKey="id" pagination={false} dataSource={data.services} scroll={{ x: 'max-content' }} columns={[
        { title: '服务', dataIndex: 'name' }, { title: '节点', dataIndex: 'node' }, { title: '计划', dataIndex: 'schedule' },
        { title: '最近状态', dataIndex: 'latest_state', render: stateTag }, { title: '最近观测', dataIndex: 'latest_at', render: (v) => v ? dateTime(v) : <Missing why="没有观测" /> },
        { title: '30 日完成率', dataIndex: 'completion_ratio_30d', render: (v) => v == null ? <Missing why="没有可判定日期" /> : pct(v) },
        { title: '最近耗时', dataIndex: 'duration_seconds', render: (v) => v == null ? '—' : `${v.toFixed(1)}s` },
      ]} />
    </Panel>
    <Panel title={`${data.selected_day} 运行步骤与证据`} info="点击一次运行查看步骤；质检步骤的 affects_run=false 不改变任务总状态。">
      <Table size="small" rowKey="run_id" pagination={false} dataSource={data.runs} onRow={(r) => ({ onClick: () => setRun(r), style: { cursor: 'pointer' } })} columns={[
        { title: '服务', dataIndex: 'service_id' }, { title: '开始', dataIndex: 'started_at', render: dateTime }, { title: '结束', dataIndex: 'finished_at', render: dateTime },
        { title: '结果', dataIndex: 'state' }, { title: '说明', key: 'detail', render: (_, x) => x.error || x.note || '—' },
      ]} />
    </Panel>
    <Drawer open={!!run} onClose={() => setRun(null)} title={run?.run_id} width={620}><Table size="small" pagination={false} rowKey={(x) => `${x.name}-${x.started_at}`} dataSource={run?.steps ?? []} columns={[
      { title: '步骤', dataIndex: 'name' }, { title: '状态', dataIndex: 'state' }, { title: '影响总结果', dataIndex: 'affects_run', render: (v) => v ? '是' : '否' }, { title: '说明', dataIndex: 'detail' },
    ]} /></Drawer>
  </>
}
