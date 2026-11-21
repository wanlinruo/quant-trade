import { Drawer, Result, Table } from 'antd'
import { useState } from 'react'
import { fetchOrders, type TradingOrder } from '../../api/trading'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, label, useTradingDay, yuan } from './common'
import { DrawerNav } from '../../page/DrawerNav'

function Lifecycle({ order, rows, close, onPick }: { order: TradingOrder; rows: TradingOrder[]; close(): void; onPick(row: TradingOrder): void }) {
  const p = usePalette()
  return <Drawer open title={`委托 ${order.id}`} size="large" onClose={close} extra={<DrawerNav rows={rows} current={order} identify={(x) => x.id} onPick={onPick} copyText={order.id} />}>
    {order.unknown_no_retry && <div role="alert" style={{ padding: 10, color: p.bad, border: `1px solid ${p.bad}`, marginBottom: 12 }}>状态未知：按交易契约不自动重发，先查券商事实并对账。</div>}
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {order.stages.map((s, i) => <span key={`${s.state}-${i}`} style={{ display: 'contents' }}>
        {i > 0 && <span style={{ color: p.muted }}>→</span>}
        <span style={{ border: `1px solid ${p.border}`, padding: '6px 9px', borderRadius: 3 }}><b>{label(s.state)}</b><br /><small>{dateTime(s.at)}{s.detail ? ` · ${s.detail}` : ''}</small></span>
      </span>)}
      {!order.stages.length && <Missing why="没有生命周期记录" />}
    </div>
    <dl><dt>券商单号</dt><dd>{order.broker_order_id ?? <Missing why="未记录，不编造" />}</dd><dt>券商受理时刻</dt><dd>{order.broker_accepted_at ? dateTime(order.broker_accepted_at) : <Missing why="未记录，不用成交时刻反推" />}</dd></dl>
  </Drawer>
}

function LifecycleLanes({ rows, onPick }: { rows: TradingOrder[]; onPick(row: TradingOrder): void }) {
  const p = usePalette()
  if (!rows.length) return <Missing why="当日没有委托" />
  return <div style={{ display: 'grid', gap: 10, minWidth: 620 }}>
    {rows.map((order) => {
      const start = new Date(order.created_at).getTime()
      const points = order.stages.filter((s) => s.at !== null).map((s) => ({ ...s, ms: Math.max(0, new Date(s.at!).getTime() - start) })).filter((x) => Number.isFinite(x.ms))
      const max = Math.max(1, ...points.map((x) => x.ms), order.unknown_no_retry ? 60_000 : 1)
      const pos = (ms: number) => 4 + 92 * Math.log1p(ms) / Math.log1p(max)
      return <button key={order.id} onClick={() => onPick(order)} style={{ border: 0, background: 'transparent', color: p.text, cursor: 'pointer', display: 'grid', gridTemplateColumns: '150px 1fr', gap: 10, textAlign: 'left' }}>
        <span><b>{order.symbol}</b><br /><small style={{ color: p.muted }}>{label(order.side)} {order.qty}</small></span>
        <span style={{ position: 'relative', height: 42, borderBottom: `1px ${order.unknown_no_retry ? 'dashed' : 'solid'} ${order.unknown_no_retry ? p.bad : p.border}` }}>
          {points.map((s, i) => <span key={`${s.state}-${i}`} title={`${label(s.state)} · ${dateTime(s.at)}`} style={{ position: 'absolute', left: `${pos(s.ms)}%`, bottom: -5, transform: 'translateX(-50%)' }}>
            <i style={{ display: 'block', width: 9, height: 9, borderRadius: '50%', background: order.unknown_no_retry && i === points.length - 1 ? p.bad : p.info, border: `1px solid ${p.panel}` }} />
            <small style={{ display: 'block', transform: 'translate(-35%, 7px)', whiteSpace: 'nowrap', color: p.muted }}>{label(s.state)}</small>
          </span>)}
          {order.unknown_no_retry && <small style={{ position: 'absolute', right: 0, top: 0, color: p.bad }}>未知 · 不重发</small>}
        </span>
      </button>
    })}
    <div style={{ color: p.muted, fontSize: 11 }}>横轴为距意图生成的对数耗时；只画实际记录节点。点击一行查看完整事实。</div>
  </div>
}

export function OrdersPage() {
  const p = usePalette()
  const day = useTradingDay(); const [picked, setPicked] = useState<TradingOrder | null>(null)
  const { data, error } = useFetch((s) => fetchOrders(day, s), [day])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="委托查询未启用" />
  const d = data; const unknown = d.orders.filter((o) => o.unknown_no_retry).length
  const pending = d.orders.filter((o) => !o.unknown_no_retry && o.status !== 'terminal' && o.remaining_qty > 0).length
  const filled = d.orders.filter((o) => o.status === 'terminal' && o.remaining_qty === 0 && d.fills.some((f) => f.order_id === o.id)).length
  const title = !d.orders.length ? '当日没有委托'
    : unknown ? `${d.orders.length} 笔委托，${unknown} 笔状态未知`
      : pending ? `${d.orders.length} 笔委托，${pending} 笔未终结` : `${d.orders.length} 笔委托，全部终态`
  return <>
    <Hero s={unknown ? 'bad' : pending ? 'running' : d.orders.length ? 'ok' : 'idle'} title={title}
      subtitle={`${dayNote(d.day)} · ${unknown ? `${unknown} 笔状态未知，均不自动重发` : '生命周期只画真实记录节点；未记录的券商事实保持缺失。'}`}
      kpis={[
        { k: '有成交的委托', v: `${filled} / ${d.orders.length}`, sub: `成交记录 ${d.fills.length} 笔` },
        { k: '未终结', v: pending, color: pending ? p.running : undefined },
        { k: '状态未知', v: unknown, color: unknown ? p.bad : undefined, sub: '不自动重发' },
        { k: '风控拦截', v: d.rejects.length, color: d.rejects.length ? p.warn : undefined, sub: '不计入委托' },
      ]} />
    <Panel title="每笔委托走到哪一步？" info="横轴为距意图的对数耗时；未记录环节不画点，未知状态以虚线延伸并明确不重发。">
      <LifecycleLanes rows={d.orders} onPick={setPicked} />
    </Panel>
    <Panel title="委托明细" info="点击一行查看生命周期、券商编号和受理时刻。">
      <Table size="small" rowKey="id" dataSource={d.orders} scroll={{ x: 'max-content' }} onRow={(r) => ({ onClick: () => setPicked(r), style: { cursor: 'pointer' } })} columns={[
        { title: '时间', dataIndex: 'created_at', render: dateTime }, { title: '指令号', dataIndex: 'id' }, { title: '标的', dataIndex: 'symbol' },
        { title: '方向', dataIndex: 'side', render: label }, { title: '数量', dataIndex: 'qty', align: 'right' }, { title: '剩余', dataIndex: 'remaining_qty', align: 'right' },
        { title: '类型', dataIndex: 'order_type', render: label }, { title: '限价', dataIndex: 'limit_price', align: 'right', render: (v) => v ?? '—' },
        { title: '状态', dataIndex: 'status', render: (v, r) => r.unknown_no_retry ? <StatusTag s="bad" text="未知·不重发" />
          : <StatusTag s={v === 'terminal' || v === 'filled' ? 'ok' : v === 'rejected' || v === 'failed' ? 'bad' : v === 'cancelled' ? 'idle' : 'running'} text={label(v)} /> },
        { title: '券商单号', dataIndex: 'broker_order_id', render: (v) => v ?? <Missing why={d.missing.broker_accepted_at} /> },
      ]} />
    </Panel>
    <Panel title="成交事实">
      <Table size="small" pagination={false} rowKey={(r) => `${r.order_id}-${r.filled_at}-${r.broker_trade_id}`} dataSource={d.fills} scroll={{ x: 'max-content' }} columns={[
        { title: '时间', dataIndex: 'filled_at', render: dateTime }, { title: '指令号', dataIndex: 'order_id' }, { title: '标的', dataIndex: 'symbol' },
        { title: '方向', dataIndex: 'side', render: label }, { title: '数量', dataIndex: 'qty', align: 'right' }, { title: '价格', dataIndex: 'price', align: 'right' },
        { title: '费用', dataIndex: 'fee', align: 'right', render: yuan }, { title: '券商成交号', dataIndex: 'broker_trade_id', render: (v) => v ?? '—' },
      ]} />
    </Panel>
    <Panel title="哪些意图被拒绝？">
      <Table size="small" pagination={false} rowKey={(r) => `${r.order_id}-${r.at}-${r.reason}`} dataSource={d.rejects} scroll={{ x: 'max-content' }} columns={[
        { title: '时间', dataIndex: 'at', render: dateTime }, { title: '指令号', dataIndex: 'order_id' }, { title: '标的', dataIndex: 'symbol' },
        { title: '方向', dataIndex: 'side', render: label }, { title: '数量', dataIndex: 'qty', align: 'right' }, { title: '原因', dataIndex: 'reason' },
      ]} />
    </Panel>
    {picked && <Lifecycle order={picked} rows={d.orders} onPick={setPicked} close={() => setPicked(null)} />}
  </>
}
