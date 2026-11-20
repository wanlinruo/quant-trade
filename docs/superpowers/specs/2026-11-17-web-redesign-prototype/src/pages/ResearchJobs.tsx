import { useState } from 'react'
import { Table, Button, Drawer, Descriptions, Steps, Tag, Modal, Form, Select, Input, App, Space } from 'antd'
import { PlusOutlined, ExperimentOutlined } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { Num, Panel, StatusTag } from '../ui'
import { Hero } from './common'

const JOBS = [
  { id: 'rj-0412', kind: '回测', task: 's6-2024-2026 全量', snap: 'snap-1113', code: 'f7590c0', s: 'running' as Status, st: '运行中', at: '11-15 22:10', cost: '41m', prog: 64, go: '' },
  { id: 'rj-0411', kind: '研究', task: 'AI 涨停月度重训评估', snap: 'snap-1113', code: 'b20f6e19', s: 'ok' as Status, st: '完成', at: '11-15 20:02', cost: '1h12m', prog: 100, go: 'ailu' },
  { id: 'rj-0410', kind: '回测', task: 's6-stress-2024q1', snap: 'snap-1108', code: '11234d8', s: 'warn' as Status, st: '完成·有警告', at: '11-12 21:30', cost: '8m', prog: 100, go: 'bt:s6-stress-2024q1' },
  { id: 'rj-0409', kind: '回测', task: 'lightgbm_price 2025h2', snap: 'snap-1108', code: 'b20f6e19', s: 'bad' as Status, st: '失败', at: '11-12 20:51', cost: '2m', prog: 100, go: '' },
  { id: 'rj-0408', kind: '研究', task: '封单比阈值敏感性', snap: 'snap-1031', code: 'e6966ba', s: 'closed' as Status, st: '已取消', at: '11-01 10:20', cost: '—', prog: 30, go: '' },
]
type Job = (typeof JOBS)[number]

export function ResearchJobs({ p }: { p: Palette }) {
  const [sel, setSel] = useState<Job | null>(null)
  const [create, setCreate] = useState(false)
  const { modal, message } = App.useApp()
  const openResult = (j: Job) => { if (j.go.startsWith('bt:')) { sessionStorage.setItem('qs-bt', j.go.slice(3)); dispatchEvent(new CustomEvent('qs-bt', { detail: j.go.slice(3) })) } else if (j.go === 'ailu') { sessionStorage.setItem('qs-ailu-tab', 'research'); location.hash = 'ailu' } }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="running" icon={<ExperimentOutlined />} title="1 个任务运行中 · 队列空闲 0" sub="隔离沙箱：只读快照 + 固定源码哈希；不接生产账户、不写 warehouse"
        kpis={[{ k: '运行中', v: '1', c: p.info }, { k: '近 7 日完成', v: '6' }, { k: '失败', v: '1', c: p.bad, sub: 'rj-0409' }, { k: '沙箱资源', v: '2C / 6G', sub: 'MemoryMax 6G' }]} />
      <Panel p={p} title="研究任务" extra={<Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setCreate(true)}>新建任务</Button>} bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="id" dataSource={JOBS} onRow={(r) => ({ onClick: () => setSel(r), style: { cursor: 'pointer' } })} rowClassName={(r) => (r.id === sel?.id ? 'qs-row-sel' : '')}
          columns={[
            { title: '任务', dataIndex: 'task', render: (v, r) => <div><span style={{ color: p.head }}>{v}</span><div><Num size={11} color={p.muted}>{r.id}</Num></div></div> },
            { title: '类型', dataIndex: 'kind', width: 70, filters: [{ text: '回测', value: '回测' }, { text: '研究', value: '研究' }], onFilter: (v, r) => r.kind === v, render: (v) => <Tag>{v}</Tag> },
            { title: '快照', dataIndex: 'snap', render: (v) => <Num size={12}>{v}</Num> },
            { title: '源码', dataIndex: 'code', render: (v) => <Num size={12}>{v}</Num> },
            { title: '状态', width: 120, render: (_, r) => <StatusTag p={p} s={r.s} text={r.st} /> },
            { title: '进度', width: 160, render: (_, r) => <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><div style={{ flex: 1, height: 6, background: p.idle, borderRadius: 2 }}><div style={{ width: `${r.prog}%`, height: '100%', background: r.s === 'bad' ? p.bad : r.s === 'running' ? p.info : p.muted, borderRadius: 2 }} /></div><Num size={11}>{r.prog}%</Num></div> },
            { title: '创建', dataIndex: 'at', render: (v) => <Num color={p.muted} size={12}>{v}</Num> },
            { title: '耗时', dataIndex: 'cost', align: 'right', render: (v) => <Num>{v}</Num> },
          ]} />
      </Panel>
      <Drawer open={!!sel} onClose={() => setSel(null)} size={520} title={sel?.task} extra={sel && <StatusTag p={p} s={sel.s} text={sel.st} />}>
        {sel && (<>
          <Descriptions size="small" column={1} bordered items={[
            { key: 'a', label: '任务 ID', children: <Num>{sel.id}</Num> }, { key: 'b', label: '数据快照', children: <Num>{sel.snap}</Num> },
            { key: 'c', label: '源码哈希', children: <Num>{sel.code}</Num> }, { key: 'd', label: '创建', children: <Num>{sel.at}</Num> },
          ]} />
          <Steps style={{ marginTop: 16 }} orientation="vertical" size="small" items={[
            { title: '冻结快照与源码', status: 'finish' }, { title: '沙箱启动', status: 'finish' },
            { title: '运行', status: sel.s === 'running' ? 'process' : sel.s === 'bad' ? 'error' : 'finish', content: sel.s === 'bad' ? 'ValueError: 2025-09-12 缺分钟数据（显式报错，未填充）' : undefined },
            { title: '产出文件', status: sel.s === 'ok' || sel.s === 'warn' ? 'finish' : 'wait', content: sel.s === 'ok' || sel.s === 'warn' ? 'result.json · equity.parquet · log.txt' : undefined },
          ]} />
          <Space>
            {sel.go && <Button type="primary" size="small" onClick={() => openResult(sel)}>{sel.go === 'ailu' ? '查看研究结果 →' : '查看回测结果 →'}</Button>}
            {sel.s === 'bad' && <Button size="small" onClick={() => message.info('原型：将以同一快照与源码哈希重新提交（新任务 ID，原失败记录保留）')}>重新提交</Button>}
            {sel.s === 'running' && <Button danger size="small" onClick={() => modal.confirm({ title: `取消「${sel.task}」？`, content: '只停止这个沙箱任务，已产生的中间文件保留以便排查；不影响生产与其他任务。原型里不会真正执行。', okText: '取消任务', okButtonProps: { danger: true }, cancelText: '不取消' })}>取消任务</Button>}
          </Space>
          {sel.s === 'running' && <div style={{ color: p.muted, fontSize: 12, marginTop: 8 }}>进度 {sel.prog}% · 已运行 {sel.cost} · 预计还需约 23 分钟</div>}
        </>)}
      </Drawer>
      <Modal open={create} title="新建研究任务" okText="提交到沙箱" onOk={() => setCreate(false)} onCancel={() => setCreate(false)}>
        <Form layout="vertical" size="small">
          <Form.Item label="类型"><Select defaultValue="bt" options={[{ value: 'bt', label: '回测' }, { value: 'rs', label: '研究脚本' }]} /></Form.Item>
          <Form.Item label="策略 / 版本"><Select defaultValue="s6v7" options={[{ value: 's6v7', label: 'two_stage_limit_hold · v7' }]} /></Form.Item>
          <Form.Item label="数据快照"><Select defaultValue="1113" options={[{ value: '1113', label: 'snap-1113（最新）' }]} /></Form.Item>
          <Form.Item label="区间"><Input defaultValue="2024-01-02 ~ 2026-11-13" /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
