import { Drawer, Result, Segmented, Table } from 'antd'
import { useMemo, useState } from 'react'
import { fetchOrders, type TradingOrder } from '../../api/trading'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { MONO, Num } from '../../ui/Num'
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

type Lane = { order: TradingOrder; points: { offset: number; names: string[]; at: string }[] }

/** 阶段合点：同一时刻的多个阶段合成一个点；连续重复的阶段（如券商成交时刻与入账时刻）只标一次。 */
export function laneOf(order: TradingOrder): Lane {
  const stamped = order.stages.filter((s) => s.at !== null).map((s) => ({ ...s, ms: Date.parse(s.at!) })).filter((s) => Number.isFinite(s.ms))
  const created = stamped.find((s) => s.state === 'created')?.ms ?? Date.parse(order.created_at)
  const origin = Math.min(created, ...stamped.map((s) => s.ms))
  const points: Lane['points'] = []
  let lastName = ''
  for (const st of [...stamped].sort((a, b) => a.ms - b.ms)) {
    const offset = Math.max(0.01, (st.ms - origin) / 1000)
    const name = label(st.state)
    const prev = points.at(-1)
    if (prev && Math.abs(prev.offset - offset) < 0.005) { if (name !== lastName) prev.names.push(name) }
    else points.push({ offset, names: name === lastName ? [] : [name], at: st.at! })
    lastName = name
  }
  return { order, points }
}

const orderTone = (p: ReturnType<typeof usePalette>, o: TradingOrder) => o.unknown_no_retry ? p.bad
  : o.status === 'terminal' || o.status === 'filled' ? p.ok : o.status === 'rejected' || o.status === 'failed' ? p.bad : o.status === 'cancelled' ? p.muted : p.running

/** 生命周期泳道（原型）：每行一笔，横轴为距意图的耗时（对数）；没记时刻的阶段不画点，不推算。 */
function LifecycleLanes({ rows, onPick }: { rows: TradingOrder[]; onPick(row: TradingOrder): void }) {
  const p = usePalette()
  const option = useMemo<EChartsOption | null>(() => {
    if (!rows.length) return null
    const lanes = rows.map(laneOf)
    const maxOffset = Math.max(1, ...lanes.flatMap((l) => l.points.map((x) => x.offset)))
    const axisMax = 10 ** Math.ceil(Math.log10(maxOffset * 3))
    const series: NonNullable<EChartsOption['series']> = []
    lanes.forEach((lane, y) => {
      const tone = orderTone(p, lane.order)
      series.push({
        type: 'line', symbol: 'circle', symbolSize: 9, z: 3, lineStyle: { color: tone, width: 2 }, itemStyle: { color: tone },
        data: lane.points.map((x) => ({ value: [x.offset, y], names: x.names, at: x.at, order: lane.order })),
        label: { show: true, position: 'top', color: p.muted, fontSize: 10, formatter: (q: unknown) => ((q as { data: { names: string[] } }).data.names).join(' · ') },
      })
      const last = lane.points.at(-1)
      if (lane.order.unknown_no_retry && last) {
        series.push({ type: 'line', silent: true, symbol: 'none', lineStyle: { color: p.bad, type: 'dashed' }, itemStyle: { color: p.bad },
          data: [[last.offset, y], { value: [axisMax, y], symbol: 'triangle', symbolSize: 9 }],
          endLabel: { show: true, formatter: '▲ 未知 · 不重发', color: p.bad, fontSize: 11 } })
      }
    })
    const fmt = (v: number) => v < 1 ? `${Math.round(v * 1000)}ms` : v >= 3600 ? `${Math.round(v / 3600)}h` : v >= 60 ? `${Math.round(v / 60)}min` : `${v}s`
    return {
      animation: false,
      grid: { left: 190, right: 120, top: 24, bottom: 34 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const x = (raw as { data?: { names?: string[]; at?: string; order?: TradingOrder; value?: [number, number] } }).data
        if (!x?.order) return ''
        return `<b>${x.order.id}</b><br/>${(x.names?.length ? x.names : ['（同上一阶段）']).join(' · ')}　${dateTime(x.at!)}<br/>距意图 +${x.value![0] < 1 ? `${Math.round(x.value![0] * 1000)}ms` : `${x.value![0].toFixed(2)}s`}`
      } },
      xAxis: { type: 'log', min: 0.01, max: axisMax, name: '距意图（秒，对数）', nameLocation: 'middle', nameGap: 22, nameTextStyle: { color: p.muted, fontSize: 11 },
        axisLabel: { color: p.muted, formatter: fmt }, axisLine: { lineStyle: { color: p.border } }, splitLine: { lineStyle: { color: p.grid } } },
      yAxis: { type: 'category', inverse: true, data: lanes.map((l) => `${dateTime(l.order.created_at).slice(11)}  ${label(l.order.side)} ${l.order.symbol}`),
        axisLabel: { color: p.text, fontSize: 12, fontFamily: MONO }, axisTick: { show: false }, axisLine: { show: false } },
      series,
    }
  }, [rows, p])
  if (!option) return <Missing why="当日没有委托" />
  return <div data-testid="order-lanes" style={{ minWidth: 620 }}>
    <EChart option={option} height={Math.max(160, rows.length * 46 + 60)}
      onClick={(raw) => { const x = (raw as { data?: { order?: TradingOrder } }).data; if (x?.order) onPick(x.order) }} />
  </div>
}

const isOpen = (o: TradingOrder) => o.unknown_no_retry || (o.status !== 'terminal' && o.status !== 'cancelled' && o.status !== 'rejected' && o.remaining_qty > 0)

export function OrdersPage() {
  const p = usePalette()
  const day = useTradingDay(); const [picked, setPicked] = useState<TradingOrder | null>(null)
  const [scope, setScope] = useState<'all' | 'open'>('all')
  const { data, error } = useFetch((s) => fetchOrders(day, s), [day])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="委托查询未启用" />
  const d = data; const unknown = d.orders.filter((o) => o.unknown_no_retry).length
  const pending = d.orders.filter((o) => !o.unknown_no_retry && isOpen(o)).length
  const fillsOf = (id: string) => d.fills.filter((f) => f.order_id === id)
  const filled = d.orders.filter((o) => o.status === 'terminal' && o.remaining_qty === 0 && fillsOf(o.id).length > 0).length
  const title = !d.orders.length ? '当日没有委托'
    : unknown ? `${d.orders.length} 笔委托，${unknown} 笔状态未知`
      : pending ? `${d.orders.length} 笔委托，${pending} 笔未终结` : `${d.orders.length} 笔委托，全部终态`
  const rows = scope === 'all' ? d.orders : d.orders.filter(isOpen)
  return <>
    <Hero s={unknown ? 'bad' : pending ? 'running' : d.orders.length ? 'ok' : 'idle'} title={title}
      subtitle={`${dayNote(d.day)} · ${unknown ? `${unknown} 笔状态未知，均不自动重发` : '生命周期只画真实记录节点；未记录的券商事实保持缺失。'}`}
      kpis={[
        { k: '有成交的委托', v: `${filled} / ${d.orders.length}`, sub: `成交记录 ${d.fills.length} 笔`, onClick: () => setScope('all') },
        { k: '未终结', v: pending, color: pending ? p.running : undefined, onClick: () => setScope('open') },
        { k: '状态未知', v: unknown, color: unknown ? p.bad : undefined, sub: '不自动重发', onClick: () => setScope('open') },
        { k: '风控拦截', v: d.rejects.length, color: d.rejects.length ? p.warn : undefined, sub: '不计入委托',
          onClick: () => document.getElementById('order-rejects')?.scrollIntoView({ behavior: 'smooth' }) },
      ]} />
    <Panel title="每笔委托走到哪一步了？" info="每行一笔，依次经过：意图生成 → 风控通过 → 已发送 → 券商受理 → 成交 / 终态。横轴为距意图的耗时（对数）；同一时刻的阶段合成一点；journal 没记时刻的环节不画点，不推算。状态未知以红色虚线延伸并明确不重发。">
      <LifecycleLanes rows={d.orders} onPick={setPicked} />
    </Panel>
    <Panel title="委托" info="展开行看该笔的成交事实（价格、费用、券商成交号）；点击行看完整生命周期。券商单号缺失时显示缺失，不编造。"
      extra={<Segmented size="small" value={scope} onChange={(v) => setScope(v as 'all' | 'open')} options={[{ label: '全部', value: 'all' }, { label: `只看未决（${pending + unknown}）`, value: 'open' }]} />}>
      <Table size="small" rowKey="id" dataSource={rows} scroll={{ x: 'max-content' }} pagination={{ pageSize: 30, hideOnSinglePage: true }}
        locale={{ emptyText: scope === 'open' ? '没有未决委托' : '当日没有委托' }}
        onRow={(r) => ({ onClick: () => setPicked(r), style: { cursor: 'pointer' } })}
        expandable={{ rowExpandable: (r) => fillsOf(r.id).length > 0, expandedRowRender: (r) => <Table size="small" pagination={false} rowKey={(f) => `${f.filled_at}-${f.broker_trade_id}`} dataSource={fillsOf(r.id)} columns={[
          { title: '成交时间', dataIndex: 'filled_at', render: (v) => <Num>{dateTime(v)}</Num> }, { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
          { title: '价格', dataIndex: 'price', align: 'right', render: (v) => <Num>{v}</Num> }, { title: '费用', dataIndex: 'fee', align: 'right', render: (v) => <Num>{yuan(v)}</Num> },
          { title: '券商成交号', dataIndex: 'broker_trade_id', render: (v) => v ?? '—' },
        ]} /> }}
        columns={[
          { title: '时间', dataIndex: 'created_at', render: (v) => <Num>{dateTime(v).slice(11)}</Num> },
          { title: '标的', dataIndex: 'symbol', render: (v) => <Num color={p.head}>{v}</Num> },
          { title: '方向', dataIndex: 'side', render: (v) => <span style={{ color: v === 'buy' ? p.up : v === 'sell' ? p.down : p.text }}>{label(v)}</span> },
          { title: '类型', dataIndex: 'order_type', render: label },
          { title: '限价', dataIndex: 'limit_price', align: 'right', render: (v) => v == null ? '—' : <Num>{v}</Num> },
          { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
          { title: '已成', key: 'filled', align: 'right', render: (_, r) => <Num>{r.qty - r.remaining_qty}</Num> },
          { title: '状态', dataIndex: 'status', render: (v, r) => r.unknown_no_retry ? <StatusTag s="bad" text="未知·不重发" />
            : <StatusTag s={v === 'terminal' || v === 'filled' ? 'ok' : v === 'rejected' || v === 'failed' ? 'bad' : v === 'cancelled' ? 'idle' : 'running'} text={label(v)} /> },
          { title: '指令号', dataIndex: 'id', render: (v) => <Num size={12} color={p.muted}>{v}</Num> },
          { title: '券商单号', dataIndex: 'broker_order_id', render: (v) => v ?? <Missing why={d.missing.broker_accepted_at} /> },
        ]} />
    </Panel>
    <Panel title={<span id="order-rejects" style={{ scrollMarginTop: 60 }}>被风控拦下的</span>} info="意图在发送前被规则拦截，没有到达网关；不计入委托。">
      {d.rejects.length ? <Table size="small" pagination={false} rowKey={(r) => `${r.order_id}-${r.at}-${r.reason}`} dataSource={d.rejects} scroll={{ x: 'max-content' }} columns={[
        { title: '时间', dataIndex: 'at', render: (v) => <Num>{dateTime(v).slice(11)}</Num> }, { title: '标的', dataIndex: 'symbol', render: (v) => <Num>{v}</Num> },
        { title: '方向', dataIndex: 'side', render: label }, { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
        { title: '原因', dataIndex: 'reason', render: (v) => <StatusTag s="bad" text={v} /> }, { title: '指令号', dataIndex: 'order_id', render: (v) => <Num size={12} color={p.muted}>{v}</Num> },
      ]} /> : <span style={{ color: p.muted }}>当日没有被风控拦下的意图。</span>}
    </Panel>
    {picked && <Lifecycle order={picked} rows={d.orders} onPick={setPicked} close={() => setPicked(null)} />}
  </>
}
