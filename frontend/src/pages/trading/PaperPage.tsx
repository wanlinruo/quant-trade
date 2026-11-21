import { Button, Input, Modal, Result, Table, Tabs } from 'antd'
import { useMemo, useState } from 'react'
import { fetchPaper, fetchPaperAccounts, paperAction, paperNotify, type PaperAccountDetail } from '../../api/trading'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { useFetch } from '../../page/useFetch'
import { setSearch, useSearch } from '../../shell/router'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, label, yuan } from './common'

const text = (v: unknown) => v == null || v === '' ? '—' : String(v)
const status = (v: string) => v === 'running' ? ['ok', '运行中'] : v === 'error' ? ['bad', '出错'] : v === 'ended' ? ['closed', '已结束'] : ['warn', ({ scheduled: '待生效', paused: '已暂停' } as Record<string, string>)[v] ?? v]

function Detail({ id, day, rev, refresh }: { id: string; day: string | null; rev: number; refresh(): void }) {
  const p = usePalette(); const { data, error } = useFetch((s) => fetchPaper(id, day, s), [id, day, rev]); const [hook, setHook] = useState(''); const [busy, setBusy] = useState(false)
  const option = useMemo<EChartsOption | null>(() => data?.enabled && data.equity.length ? ({
    grid: { left: 54, right: 12, top: 24, bottom: 25 }, legend: { data: ['模拟', '来源回测'] }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: data.equity.map((x) => x.day) }, yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(1)}%` } },
    series: [{ name: '模拟', type: 'line', data: data.equity.map((x) => x.strategy_ratio), connectNulls: false },
      { name: '来源回测', type: 'line', data: data.equity.map((x) => data.source_backtest.find((b) => b.day === x.day)?.return_ratio ?? null), connectNulls: false }],
  }) : null, [data])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载账户'} />
  if (!data.enabled) return <Result status="info" title="模拟账户未启用" />
  const d = data as PaperAccountDetail; const st = status(d.status)
  const act = (action: 'pause' | 'resume' | 'end') => Modal.confirm({ title: action === 'end' ? '确认结束模拟账户？' : `确认${action === 'pause' ? '暂停' : '恢复'}？`,
    content: action === 'end' ? '结束后账户只读，且不会自动平仓。' : '此操作只影响该模拟账户，不影响实盘。', okType: action === 'end' ? 'danger' : 'primary',
    onOk: async () => { await paperAction(id, action); refresh() } })
  const save = async () => { setBusy(true); try { await paperNotify(id, hook.trim()); setHook(''); refresh() } finally { setBusy(false) } }
  const table = (rows: Record<string, unknown>[], keys: [string, string][]) => <Table size="small" pagination={{ pageSize: 30, hideOnSinglePage: true }} rowKey={(_, i) => String(i)} dataSource={rows} scroll={{ x: 'max-content' }} columns={keys.map(([k, name]) => ({ title: name, dataIndex: k, render: k.endsWith('_at') || k === 'at' || k === 'created_at' || k === 'filled_at' ? dateTime : (v: unknown) => text(v) }))} />
  return <>
    {d.error && <div role="alert" style={{ border: `1px solid ${p.bad}`, padding: 10, color: p.bad, marginBottom: 12 }}><b>运行异常：</b>{d.error}<br /><small>{d.missing.recovery}</small></div>}
    <Panel title={<span><StatusTag s={st[0] as 'ok' | 'bad' | 'warn' | 'closed'} text={st[1]} /> {d.name} <span style={{ color: p.muted, fontWeight: 400, fontSize: 12 }}>{d.strategy_name} · 版本 {d.version_id.slice(0, 12)} · {dayNote(d.day)}</span></span>} info="只影响模拟账户；错误恢复不在页面直接改库。" extra={<div style={{ display: 'flex', gap: 6 }}>{['scheduled', 'running', 'error'].includes(d.status) && <Button size="small" onClick={() => act('pause')}>暂停</Button>}{d.status === 'paused' && <Button size="small" onClick={() => act('resume')}>恢复</Button>}{d.status !== 'ended' && <Button size="small" danger onClick={() => act('end')}>结束</Button>}</div>}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}><Input.Password aria-label="通知 Webhook" value={hook} onChange={(e) => setHook(e.target.value)} placeholder={d.notify_mask ?? 'https://…；留空关闭'} style={{ maxWidth: 480 }} /><Button loading={busy} onClick={save}>保存通知</Button><span style={{ color: p.muted }}>{d.notify_mask ? `已配置 ${d.notify_mask}` : '未配置'}（原值不回显）</span></div>
    </Panel>
    <Panel title={`${d.name}：净值和来源回测对得上吗？`} info="两条线使用各自初始资金；缺来源回测点保持断开。">{option ? <EChart option={option} height={220} /> : <Missing why="没有权益记录" />}</Panel>
    <Panel title="账户明细">
      <Tabs items={[
        { key: 'positions', label: `持仓 ${d.positions.length}`, children: table(d.positions, [['symbol', '标的'], ['total_qty', '总量'], ['available_qty', '可卖'], ['last_price', '最新价'], ['updated_at', '更新']]) },
        { key: 'orders', label: `委托 ${d.orders.length}`, children: table(d.orders, [['created_at', '时间'], ['symbol', '标的'], ['side', '方向'], ['qty', '数量'], ['remaining_qty', '剩余'], ['status', '状态']]) },
        { key: 'fills', label: `成交 ${d.fills.length}`, children: table(d.fills, [['filled_at', '时间'], ['symbol', '标的'], ['side', '方向'], ['qty', '数量'], ['price', '价格'], ['fee', '费用']]) },
        { key: 'rejects', label: `拒单 ${d.rejects.length}`, children: table(d.rejects, [['at', '时间'], ['symbol', '标的'], ['qty', '数量'], ['reason', '原因']]) },
        { key: 'events', label: `事件 ${d.events.length}`, children: table(d.events, [['scheduled_at', '计划'], ['phase', '阶段'], ['status', '状态'], ['detail', '详情']]) },
        { key: 'runs', label: `运行 ${d.runs.length}`, children: <Table size="small" pagination={false} rowKey="event_key" dataSource={d.runs} columns={[{ title: '事件', dataIndex: 'event_key' }, { title: '开始', dataIndex: 'started_at', render: dateTime }, { title: '结果', dataIndex: 'status', render: label }, { title: '摘要', dataIndex: 'summary' }]} /> },
      ]} />
      <div style={{ marginTop: 8, color: p.muted }}>{d.missing.fill_reference}</div>
    </Panel>
  </>
}

export function PaperPage() {
  const p = usePalette()
  const q = useSearch(); const id = q.get('id'); const rawDay = q.get('day'); const day = rawDay && /^\d{4}-\d{2}-\d{2}$/.test(rawDay) ? rawDay : null; const [rev, setRev] = useState(0)
  const { data, error } = useFetch(fetchPaperAccounts, [rev])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="模拟账户未启用" />
  const selected = id && data.accounts.some((a) => a.id === id) ? id : data.accounts[0]?.id
  if (!selected) return <Hero s="idle" title="还没有模拟账户" />
  const rows = data.accounts
  const n = (k: string) => rows.filter((a) => a.status === k).length
  const live = n('running') + n('scheduled')
  const returns = rows.map((a) => a.return_ratio).filter((x): x is number => x != null)
  return <>
    <Hero s={n('error') ? 'bad' : live ? 'ok' : 'idle'} title={`${rows.length} 个模拟账户 · ${live} 个运行中${n('error') ? ` · ${n('error')} 个出错` : ''}`}
      subtitle="模拟账户只影响自身，不影响实盘；收益与回撤取各账户最新权益快照。"
      kpis={[
        { k: '运行中', v: live, color: live ? p.ok : undefined, sub: n('scheduled') ? `含待生效 ${n('scheduled')}` : undefined },
        { k: '已暂停 / 已结束', v: `${n('paused')} / ${n('ended')}` },
        { k: '出错', v: n('error'), color: n('error') ? p.bad : p.ok },
        { k: '最好 / 最差收益', v: returns.length ? `${pct(Math.max(...returns))} / ${pct(Math.min(...returns))}` : '—', sub: returns.length < rows.length ? `${rows.length - returns.length} 个无权益快照` : undefined },
      ]} />
    <Panel title="账户列表" info="点击一行查看该账户明细。">
      <Table size="small" rowKey="id" pagination={false} dataSource={rows} scroll={{ x: 'max-content' }}
        onRow={(r) => ({ onClick: () => setSearch({ id: r.id }), style: { cursor: 'pointer', background: r.id === selected ? p.panel2 : undefined } })}
        columns={[
          { title: '账户', dataIndex: 'name', render: (v, r) => <span style={{ color: p.head }}>{r.id === selected ? '▸ ' : ''}{v}</span> },
          { title: '策略 / 版本', key: 'sv', render: (_, r) => <span style={{ color: p.muted }}>{r.strategy_name} · {r.version_id.slice(0, 8)}</span> },
          { title: '状态', dataIndex: 'status', render: (v: string) => { const x = status(v); return <StatusTag s={x[0] as 'ok' | 'bad' | 'warn' | 'closed'} text={x[1]} /> } },
          { title: '收益', dataIndex: 'return_ratio', align: 'right', render: (v) => v == null ? <Missing why="无权益快照" /> : <Num color={v > 0 ? p.up : v < 0 ? p.down : undefined}>{pct(v)}</Num> },
          { title: '最大回撤', dataIndex: 'max_drawdown_ratio', align: 'right', render: (v) => v == null ? <Missing why="无权益快照" /> : <Num>{pct(v)}</Num> },
          { title: '总资产', dataIndex: 'total_value', align: 'right', render: (v) => v == null ? <Missing why="无权益快照" /> : <Num>{yuan(v)}</Num> },
          { title: '持仓', dataIndex: 'position_count', align: 'right', render: (v) => v ?? '—' },
          { title: '启用日', dataIndex: 'activation_date', render: (v) => <Num color={p.muted}>{v}</Num> },
        ]} />
    </Panel>
    <Detail id={selected} day={day} rev={rev} refresh={() => setRev((x) => x + 1)} />
  </>
}
