import { Drawer, Result, Table, Tag } from 'antd'
import { useState } from 'react'
import { fetchOrders, type TradingOrder } from '../../api/trading'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { DaySelect, label, useTradingDay, yuan } from './common'

function Lifecycle({ order, close }: { order: TradingOrder; close(): void }) {
  const p = usePalette()
  return <Drawer open title={`委托 ${order.id}`} size="large" onClose={close}>
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

export function OrdersPage() {
  const day = useTradingDay(); const [picked, setPicked] = useState<TradingOrder | null>(null)
  const { data, error } = useFetch((s) => fetchOrders(day, s), [day])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="委托查询未启用" />
  const d = data; const unknown = d.orders.filter((o) => o.unknown_no_retry).length
  return <>
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}><DaySelect value={d.day} /></div>
    <Hero s={unknown ? 'bad' : 'ok'} title={`${d.orders.length} 笔委托 · ${d.fills.length} 笔成交 · ${d.rejects.length} 笔拒单`}
      subtitle={unknown ? `${unknown} 笔状态未知，均不自动重发` : '生命周期只画真实记录节点；未记录的券商事实保持缺失。'} />
    <Panel title="委托走到哪一步？" info="点击一行查看生命周期。未知状态用虚线语义并明确不重发。">
      <Table size="small" rowKey="id" dataSource={d.orders} scroll={{ x: 'max-content' }} onRow={(r) => ({ onClick: () => setPicked(r), style: { cursor: 'pointer' } })} columns={[
        { title: '时间', dataIndex: 'created_at', render: dateTime }, { title: '指令号', dataIndex: 'id' }, { title: '标的', dataIndex: 'symbol' },
        { title: '方向', dataIndex: 'side', render: label }, { title: '数量', dataIndex: 'qty', align: 'right' }, { title: '剩余', dataIndex: 'remaining_qty', align: 'right' },
        { title: '类型', dataIndex: 'order_type', render: label }, { title: '限价', dataIndex: 'limit_price', align: 'right', render: (v) => v ?? '—' },
        { title: '状态', dataIndex: 'status', render: (v, r) => <>{r.unknown_no_retry ? <StatusTag s="bad" text="未知·不重发" /> : <Tag>{label(v)}</Tag>}</> },
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
    {picked && <Lifecycle order={picked} close={() => setPicked(null)} />}
  </>
}
