import { Drawer, Result, Table } from 'antd'
import { useState } from 'react'
import { fetchReleases, type ComponentRelease } from '../../api/operations'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { Missing } from '../../ui/Missing'
import { stateTag } from './common'

export function ReleasesPage() {
  const { data, error } = useFetch(fetchReleases, []); const [component, setComponent] = useState<ComponentRelease | null>(null)
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="发布与版本快照未配置" subTitle="Web 不直接读取节点白名单或部署记录。" />
  const all = data.component_count; const ok = data.consistent_components
  return <>
    <Hero s={data.stale || ok == null || all == null || ok !== all ? 'warn' : 'ok'} title={data.stale ? '发布快照已过期' : ok == null || all == null ? '三方一致性未知' : `${ok}/${all} 个组件三方一致`}
      subtitle="三方＝workflow、GitHub Deployment、节点 record；缺任一项不算一致。" kpis={[{ k: 'main', v: data.main_sha ?? <Missing why={data.missing.main_sha ?? '未采集'} /> }, { k: '当前组件', v: all ?? '—' }]} />
    <Panel title="各组件当前版本与回退目标是什么？"><Table size="small" pagination={false} rowKey="id" dataSource={data.components} onRow={(r) => ({ onClick: () => setComponent(r), style: { cursor: 'pointer' } })} scroll={{ x: 'max-content' }} columns={[
      { title: '组件', dataIndex: 'label' }, { title: '状态', dataIndex: 'state', render: stateTag }, { title: '当前版本', dataIndex: 'current_sha', render: (v) => v ?? <Missing why="未采集" /> }, { title: '回退目标', dataIndex: 'rollback_sha', render: (v) => v ?? <Missing why="未采集" /> },
      { title: '发布时间', dataIndex: 'released_at', render: dateTime }, { title: '三方一致', dataIndex: 'three_way_consistent', render: (v) => v == null ? <Missing why="证据不完整" /> : v ? '是' : '否' },
    ]} /></Panel>
    <Panel title="近 30 日发布发生了什么？"><Table size="small" pagination={false} rowKey="id" dataSource={data.events} columns={[
      { title: '时间', dataIndex: 'at', render: dateTime }, { title: '组件', dataIndex: 'component_id' }, { title: '版本', dataIndex: 'sha', render: (v) => v ?? '—' }, { title: '结果', dataIndex: 'state', render: stateTag }, { title: '当前', dataIndex: 'current', render: (v) => v ? '是' : '否' }, { title: '说明', dataIndex: 'detail', render: (v) => v ?? '—' },
    ]} /></Panel>
    <Drawer open={!!component} onClose={() => setComponent(null)} title={`${component?.label ?? ''} 发布证据`} width={640}><Table size="small" pagination={false} rowKey="id" dataSource={component?.steps ?? []} columns={[
      { title: '步骤', dataIndex: 'label' }, { title: '状态', dataIndex: 'state', render: stateTag }, { title: '时间', dataIndex: 'at', render: dateTime }, { title: '说明', dataIndex: 'detail', render: (v) => v ?? '—' },
    ]} /></Drawer>
  </>
}
