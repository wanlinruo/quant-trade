import { useEffect, useRef, useState } from 'react'
import { Drawer, Table, Descriptions, Button } from 'antd'
import { CheckCircleFilled, WarningFilled, ArrowRightOutlined } from '@ant-design/icons'
import { createChart, AreaSeries, createSeriesMarkers, ColorType, LineStyle, type UTCTimestamp } from 'lightweight-charts'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { Copy, Num, Panel, StatusTag, statusColor } from '../ui'
import { Bullet, DrawerNav, Hero, Missing, signed } from './common'
import { PM, BAD, KILL_AT } from '../scene'
import { MINUTES } from '../mock'
import { EQUITY_BASE, equityIntraday, orders, positions } from './tradingMock'

const BJ = 8 * 3600

/** 链路拓扑：每个节点一个状态，每段连线标心跳延迟。 */
function Topology({ p }: { p: Palette }) {
  const nodes: { name: string; host: string; s: Status; note: string }[] = [
    { name: 'live-core', host: 'Linux', s: BAD ? 'bad' : 'ok', note: BAD ? '停机闸已触发 · 只减仓' : 'PID 2128485 · 重启 0' },
    { name: 'Redis 隧道', host: 'Linux ⇄ Win', s: BAD ? 'warn' : 'ok', note: BAD ? '10:36:40 恢复 · 曾断 5m35s' : 'stream lag 0' },
    { name: '执行网关', host: 'Windows', s: 'ok', note: '队列 0' },
    { name: 'QMT 执行器', host: 'Windows', s: 'ok', note: '第 5 行策略 · 运行' },
  ]
  const edges = [{ ms: 4, s: 'ok' as Status }, { ms: 38, s: 'ok' as Status }, { ms: 12, s: 'ok' as Status }]
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '18px 4px 10px' }}>
      {nodes.map((n, i) => (
        <div key={n.name} style={{ display: 'contents' }}>
          <div className="qs-kpi-link" title="点击看节点与链路" onClick={() => { location.hash = 'nodes' }} style={{ flex: '0 0 150px', border: `1px solid ${statusColor(p, n.s)}88`, borderRadius: 4, padding: '8px 10px', background: p.panel2 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}><b style={{ color: p.head }}>{n.name}</b><StatusTag s={n.s} p={p} /></div>
            <div style={{ color: p.muted, fontSize: 11, marginTop: 2 }}>{n.host}</div>
            <div style={{ color: p.text, fontSize: 11 }}>{n.note}</div>
          </div>
          {i < edges.length && (
            <div style={{ flex: 1, textAlign: 'center', minWidth: 60 }}>
              <Num size={11} color={statusColor(p, edges[i].s)}>{edges[i].ms} ms</Num>
              <div style={{ height: 2, background: statusColor(p, edges[i].s), margin: '3px 6px', position: 'relative' }}>
                <ArrowRightOutlined style={{ position: 'absolute', right: -6, top: -6, fontSize: 12, color: statusColor(p, edges[i].s) }} />
              </div>
              <span style={{ color: p.muted, fontSize: 10 }}>心跳 2s 前</span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

/** 账户总资产分时 + 委托标记；上方一条心跳状态带。 */
function EquityChart({ p }: { p: Palette }) {
  const ref = useRef<HTMLDivElement>(null)
  const data = equityIntraday()
  const [leg, setLeg] = useState<{ t: string; v: number } | null>(null)
  useEffect(() => {
    const chart = createChart(ref.current!, {
      height: 220, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: p.panel }, textColor: p.muted, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
      rightPriceScale: { borderColor: p.border }, timeScale: { borderColor: p.border, timeVisible: true, fixRightEdge: true },
    })
    const aopt = { lineColor: p.line, topColor: p.line + '40', bottomColor: p.line + '00', lineWidth: 2 as const, priceLineVisible: false }
    // 缺失区间前后拆成两条序列，不把缺口连成直线
    const cut = data.findIndex((d) => d.value == null)
    const seg = (keep: (i: number) => boolean) => data.map((d, i) => (d.value == null || !keep(i) ? { time: (d.time + BJ) as UTCTimestamp } : { time: (d.time + BJ) as UTCTimestamp, value: d.value }))
    const s = chart.addSeries(AreaSeries, { ...aopt, lastValueVisible: cut < 0 })
    s.setData(seg((i) => cut < 0 || i < cut))
    const s2 = cut < 0 ? null : chart.addSeries(AreaSeries, { ...aopt, lastValueVisible: true })
    s2?.setData(seg((i) => i > cut))
    s.createPriceLine({ price: EQUITY_BASE, color: p.muted, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: '开盘' })
    const at = (hhmm: string) => (new Date(`2026-11-16T${hhmm}+08:00`).getTime() / 1000 + BJ) as UTCTimestamp
    createSeriesMarkers(s, [
      { time: at('09:31:00'), position: 'belowBar', color: p.up, shape: 'arrowUp', text: '买 ×2 成交' },
      { time: at('09:32:00'), position: 'aboveBar', color: p.muted, shape: 'circle', text: '撤' },
      PM ? { time: at('10:06:00'), position: 'aboveBar', color: p.down, shape: 'arrowDown', text: '卖 成交（15:30 对账确认）' } : { time: at('10:05:00'), position: 'aboveBar', color: p.warn, shape: 'square', text: '▲ 卖单状态未知' },
    ])
    if (s2) createSeriesMarkers(s2, [{ time: at('10:37:00'), position: 'aboveBar', color: p.bad, shape: 'square', text: '✕ 停机闸 10:32:35 起（10:31–10:36 无观测）' }])
    chart.timeScale().fitContent()
    chart.subscribeCrosshairMove((q) => {
      const v = q.time ? ((q.seriesData.get(s) ?? (s2 && q.seriesData.get(s2))) as { value: number } | undefined) : undefined
      if (!v) return setLeg(null)
      setLeg({ t: new Date(((q.time as number) - BJ) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' }), v: v.value })
    })
    return () => chart.remove()
  }, [p]) // eslint-disable-line react-hooks/exhaustive-deps
  const beats: Status[] = Array.from({ length: MINUTES }, (_, i) => (BAD && i >= 61 && i <= 66 ? 'bad' : i >= 34 && i <= 35 ? 'warn' : 'ok'))
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ color: p.muted, fontSize: 11, width: 52 }}>链路心跳</span>
        <div style={{ display: 'flex', gap: 1, flex: 1, height: 10, marginRight: 62 }}>
          {beats.map((b, i) => <span key={i} title={b === 'bad' ? '10:31–10:36 Redis 隧道中断，无心跳' : b === 'warn' ? '10:04–10:05 网关心跳 9s（阈值 5s）' : ''} style={{ flex: 1, background: statusColor(p, b), opacity: 0.85 }} />)}
        </div>
      </div>
      <div style={{ position: 'relative' }}>
        {(() => { const cur = leg ?? { t: PM ? '15:00 收' : BAD ? '10:48' : '10:42', v: data.at(-1)!.value! }; const d = cur.v - EQUITY_BASE; return (
          <div style={{ position: 'absolute', zIndex: 2, left: 4, top: 2, display: 'flex', gap: 12, fontSize: 12, pointerEvents: 'none' }}>
            <span style={{ color: p.muted }}>{cur.t}</span><Num color={p.head} weight={600}>{cur.v.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</Num>
            <span style={{ color: p.muted }}>较开盘 <Num color={d >= 0 ? p.up : p.down}>{signed(d)}</Num></span>
            <span style={{ color: p.muted }}>（含入出金，不是收益）</span>
          </div>) })()}
        <div ref={ref} style={{ height: 220 }} />
      </div>
    </div>
  )
}

export function Live({ p }: { p: Palette }) {
  const [sel, setSel] = useState<(typeof positions)[number] | null>(null)
  const mv = positions.reduce((a, x) => a + x.qty * x.last, 0)
  const total = equityIntraday().at(-1)!.value!
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={PM ? 'ok' : BAD ? 'bad' : 'warn'} icon={PM ? <CheckCircleFilled /> : <WarningFilled />} title={PM ? '已收盘 · 今日 4 笔委托全部终态' : BAD ? '只允许减仓 · 停机闸已触发' : '可下单 · 1 笔委托状态未知'}
        sub={BAD ? <>10:31:05 Redis 隧道中断，{KILL_AT} watchdog 触发停机闸；隧道 10:36:40 已恢复，链路现在正常，但停机闸要维护者核对后经受管入口解除。另有 1 笔卖单状态未知。 <a href="#recon">去看解除前核对清单 →</a></> : PM ? <>15:30 收盘对账三层一致；10:05:44 卖 601xxx.SH 100 股券商一直未回报，对账从券商快照确认已成交。 <a href="#recon">去对账与风控 →</a></> : <>10:05:44 卖 601xxx.SH 100 股：网关已 ACK，券商未回报。不重发、预留不释放。 <a href="#orders">去委托与成交 →</a></>}
        kpis={[
          { k: '总资产（原始观测）', v: total.toLocaleString('zh-CN', { minimumFractionDigits: 2 }), sub: <>收益率 <Missing p={p} why="缺资金变动流水，不能把总资产变化当投资收益" /></> },
          { k: '持仓市值 / 敞口', v: `${(mv / 1e4).toFixed(2)} 万`, sub: `${((mv / total) * 100).toFixed(1)}% · 上限 10%` },
          { k: '今日委托', v: '4', sub: PM ? '成 3 · 撤 1 · 未知 0' : '成 2 · 撤 1 · 未知 1', go: 'orders' },
          BAD ? { k: '停机闸', v: '已触发', c: p.bad, sub: `${KILL_AT} · watchdog`, go: 'recon' } : { k: '急停', v: '未触发', c: p.ok, sub: '最后清除 11-11 09:12', go: 'recon' },
        ]} />

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
        <Panel p={p} title="链路每一段现在通吗？" info="从决策到下单的 4 个环节；连线上是最近一次心跳往返延迟。任一段断开，结论条变红并触发停机闸。">
          <Topology p={p} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0, borderTop: `1px solid ${p.border}`, marginTop: 6 }}>
            {[
              BAD ? { k: '今日断线', v: '1 次', c: p.bad, sub: 'Redis 隧道 10:31:05–10:36:40，5 分 35 秒' } : { k: '今日断线', v: '0 次', c: p.ok, sub: '最近一次 11-10 14:03（Windows 重启窗口）' },
              { k: '最近一次下单往返', v: '1.1s', c: p.head, sub: '意图 → 券商受理 · 09:25:10' },
              { k: '执行器版本', v: 'f7590c0', c: p.head, sub: '与 live-core 一致 ✓' },
            ].map((x, i) => (
              <div key={x.k} style={{ padding: '8px 10px', borderLeft: i ? `1px solid ${p.border}` : 'none' }}>
                <div style={{ color: p.muted, fontSize: 11 }}>{x.k}</div>
                <Num color={x.c} weight={600}>{x.v}</Num>
                <div style={{ color: p.muted, fontSize: 11 }}>{x.sub}</div>
              </div>
            ))}
          </div>
        </Panel>
        <Panel p={p} title="风险额度用了多少？" info="子弹图：灰竖线＝70% 提醒线，红线＝硬上限。数值来自 live-core 风控快照。">
          <Bullet p={p} label="单票持仓占比（最大一只）" used={4.9} limit={5} unit="%" />
          <Bullet p={p} label="总敞口" used={+((mv / total) * 100).toFixed(1)} limit={10} unit="%" />
          <Bullet p={p} label="单日亏损" used={PM ? 0 : null} limit={2} unit="%" />
          <Bullet p={p} label="单笔委托（今日最大）" used={0.47} limit={1} unit=" 万" fmt={(v) => v.toFixed(2)} />
        </Panel>
      </div>

      <Panel p={p} title="今天账户怎么走的？" info={PM ? '总资产原始观测（含入出金，不是收益）。标记：▲红＝买入成交，○＝撤单，▼绿＝卖出成交。滚轮缩放、拖动平移，双击复位。' : '总资产原始观测（含入出金，不是收益）。标记：▲红＝买入成交，○＝撤单，■黄＝状态未知。滚轮缩放、拖动平移，双击复位。'}>
        <EquityChart p={p} />
      </Panel>

      <Panel p={p} title="持仓" info="点击行查看这只票今天的全部委托与成交。T+1：今日买入可用为 0。本系统预留＝已发出、尚未终态的卖单占用，以券商快照为准对账。" bodyStyle={{ padding: '4px 0 0' }}
        extra={<span style={{ color: p.muted, fontSize: 12 }}>券商快照 {PM ? '15:30:10' : '10:42:10'} · {positions.length} 只</span>}>
        <Table size="small" pagination={false} rowKey="code" dataSource={positions} className="qs-click" onRow={(r) => ({ onClick: () => setSel(r) })}
          rowClassName={(r) => (r.code === sel?.code ? 'qs-row-sel' : '')}
          summary={() => {
            const pnl = positions.reduce((a, r) => a + (r.last - r.cost) * r.qty, 0)
            return (
              <Table.Summary.Row style={{ background: p.panel2 }}>
                <Table.Summary.Cell index={0} colSpan={8}><span style={{ color: p.muted }}>合计 · 市值 <Num color={p.text}>{mv.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</Num></span></Table.Summary.Cell>
                <Table.Summary.Cell index={8} align="right"><Num color={pnl >= 0 ? p.up : p.down}>{signed(pnl)}</Num></Table.Summary.Cell>
                <Table.Summary.Cell index={9}><Num size={12}>{((mv / total) * 100).toFixed(1)}%</Num><span style={{ color: p.muted, fontSize: 11 }}> / 上限 10%</span></Table.Summary.Cell>
              </Table.Summary.Row>
            )
          }}
          columns={[
            { title: '代码', dataIndex: 'code', render: (v) => <Num color={p.head}>{v}</Num> },
            { title: '名称', dataIndex: 'name' },
            { title: '持仓', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
            { title: '可用', dataIndex: 'avail', align: 'right', render: (v) => <Num color={v ? p.text : p.muted}>{v}</Num> },
            { title: '本系统预留', dataIndex: 'reserved', align: 'right', render: (v: number) => v ? <span title="q-1116-0004 卖单状态未知，预留不释放"><Num color={p.warn}>▲ {v}</Num></span> : <Num color={p.muted}>0</Num> },
            { title: '成本', dataIndex: 'cost', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
            { title: '现价', dataIndex: 'last', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
            { title: '今日涨跌', dataIndex: 'chg', align: 'right', render: (v: number) => <Num color={v >= 0 ? p.up : p.down}>{signed(v)}%</Num> },
            { title: '浮动盈亏', align: 'right', render: (_, r) => { const v = (r.last - r.cost) * r.qty; return <Num color={v >= 0 ? p.up : p.down}>{signed(v)}</Num> } },
            { title: '占总资产（单票上限 5%）', width: 190, render: (_, r) => { const w = ((r.qty * r.last) / total) * 100; return <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><div style={{ flex: 1, height: 6, background: p.idle, borderRadius: 2 }}><div style={{ width: `${(w / 5) * 100}%`, height: '100%', background: w > 4.5 ? p.warn : p.info, borderRadius: 2 }} /></div><Num size={12}>{w.toFixed(1)}%</Num></div> } },
          ]} />
      </Panel>

      <Drawer open={!!sel} onClose={() => setSel(null)} size={520} title={sel && `${sel.code} ${sel.name}`} extra={<DrawerNav p={p} list={positions} cur={sel} set={setSel} />}>
        {sel && (
          <>
            <Descriptions size="small" column={2} bordered items={[
              { key: 'a', label: '持仓', children: <Num>{sel.qty}</Num> }, { key: 'b', label: '可用', children: <Num>{sel.avail}</Num> },
              { key: 'c', label: '成本', children: <Num>{sel.cost.toFixed(2)}</Num> }, { key: 'd', label: '现价', children: <Num>{sel.last.toFixed(2)}</Num> },
              { key: 'e', label: '浮动盈亏', children: <Num color={sel.last >= sel.cost ? p.up : p.down}>{signed((sel.last - sel.cost) * sel.qty)}</Num> },
              { key: 'f', label: '本系统预留', children: sel.reserved ? <Num color={p.warn}>▲ {sel.reserved}（待回报）</Num> : <Num>0</Num> },
            ]} />
            <div style={{ margin: '14px 0 6px', color: p.head, fontWeight: 600 }}>今天这只票的委托</div>
            <Table size="small" pagination={false} rowKey="id" dataSource={orders.filter((o) => o.code === sel.code)}
              columns={[{ title: '时间', dataIndex: 'at', render: (v) => <Num>{v}</Num> }, { title: '方向', dataIndex: 'side' }, { title: '数量', dataIndex: 'qty' },
                { title: '状态', render: (_, o) => <StatusTag s={o.status} p={p} text={o.statusText} /> }, { title: 'client_order_id', dataIndex: 'id', render: (v) => <Copy>{v}</Copy> }]} />
            <Button style={{ marginTop: 12 }} href="#orders">在委托与成交中查看 →</Button>
          </>
        )}
      </Drawer>
    </div>
  )
}
