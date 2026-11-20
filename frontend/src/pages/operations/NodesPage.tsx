import { Result, Table } from 'antd'
import { fetchNodes } from '../../api/operations'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { Missing } from '../../ui/Missing'
import { bytes, stateTag } from './common'

export function NodesPage() {
  const { data, error } = useFetch(fetchNodes, [])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="节点与链路快照未配置" subTitle="Web 不回退现场探测。" />
  const bad = [...data.nodes, ...data.links, ...data.processes, ...data.security].filter((x) => x.state === 'failed').length
  return <>
    <Hero s={data.stale || bad ? 'bad' : 'ok'} title={data.stale ? '运维快照已过期' : bad ? `${bad} 项失败` : '节点与链路正常'}
      subtitle={`快照 ${dateTime(data.captured_at)}；页面只读，不提供重启或停用。`} kpis={[{ k: '节点', v: data.nodes.length }, { k: '链路', v: data.links.length }, { k: '进程', v: data.processes.length }]} />
    <Panel title="节点资源与磁盘余量怎样？" info="系统盘和数据盘分别显示；失败线来自采集快照，不相互替代。">
      {data.nodes.map((n) => <section key={n.id} style={{ marginBottom: 14 }}><h4>{n.label} {stateTag(n.state)}</h4><Table size="small" pagination={false} rowKey="id" dataSource={n.disks} columns={[
        { title: '磁盘', dataIndex: 'label' }, { title: '挂载点', dataIndex: 'mount' }, { title: '状态', dataIndex: 'state', render: stateTag },
        { title: '可用', dataIndex: 'free_bytes', render: bytes }, { title: '总量', dataIndex: 'total_bytes', render: bytes }, { title: '失败线', dataIndex: 'fail_below_bytes', render: bytes },
      ]} /></section>)}
      {!data.nodes.length && <Missing why={data.missing.nodes ?? '没有节点快照'} />}
    </Panel>
    <Panel title="链路最近是否可用？"><Table size="small" pagination={false} rowKey="id" dataSource={data.links} columns={[
      { title: '链路', dataIndex: 'label' }, { title: '状态', dataIndex: 'state', render: stateTag }, { title: '最后正常', dataIndex: 'last_ok_at', render: dateTime }, { title: '最近异常', dataIndex: 'last_error', render: (v) => v ?? '—' },
    ]} /></Panel>
    <Panel title="常驻进程与 QMT 现在怎样？"><Table size="small" pagination={false} rowKey="id" dataSource={data.processes} scroll={{ x: 'max-content' }} columns={[
      { title: '进程', dataIndex: 'label' }, { title: '节点', dataIndex: 'node_id' }, { title: '状态', dataIndex: 'state', render: stateTag }, { title: 'PID', dataIndex: 'pid', render: (v) => v ?? '—' },
      { title: '重启次数', dataIndex: 'restart_count', render: (v) => v ?? '—' }, { title: '身份', dataIndex: 'identity', render: (v) => v ?? '—' }, { title: '资源上限', dataIndex: 'resource_limit', render: (v) => v ?? '—' }, { title: '脱敏日志', dataIndex: 'last_log', render: (v) => v ?? '—' },
    ]} /></Panel>
    <Panel title="安全核验结论是什么？" info="只展示结论、项名和计数，不展示 key、指纹、地址或配置原文。"><Table size="small" pagination={false} rowKey="id" dataSource={data.security} columns={[
      { title: '检查', dataIndex: 'label' }, { title: '状态', dataIndex: 'state', render: stateTag }, { title: '不一致项', dataIndex: 'mismatches', render: (v: string[]) => v.length ? v.join('、') : '—' },
      { title: '失败登录 24h', dataIndex: 'failed_logins_24h', render: (v) => v ?? <Missing why="未采集" /> }, { title: '拒绝未认证连接 24h', dataIndex: 'rejected_unauthenticated_24h', render: (v) => v ?? <Missing why="未采集" /> },
    ]} /></Panel>
  </>
}
