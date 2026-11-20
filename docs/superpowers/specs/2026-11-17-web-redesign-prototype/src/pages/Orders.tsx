import { useMemo, useState } from 'react'
import { Drawer, Table, Timeline, Typography, Segmented, Button, Tooltip } from 'antd'
import { CheckCircleFilled, WarningFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import { Copy, EChart, Num, Panel, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'
import { DrawerNav, EmptyNote, Hero } from './common'
import { orders, rejects } from './tradingMock'
import { PM, BAD } from '../scene'

type Order = (typeof orders)[number]
const STAGES = ['意图', '风控通过', '网关 ACK', '券商受理', '终态']
const t0 = (o: Order) => o.stages[0].t!
const NOW = BAD ? 83 * 60 + 20 : 77 * 60 + 15 // 10:42:15（异常日 10:48:20）相对 09:25:00
const hms = (sec: number) => { const s = 9 * 3600 + 25 * 60 + sec; return `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${(s % 60).toFixed(2).padStart(5, '0')}` }

export function Orders({ p }: { p: Palette }) {
  const [sel, setSel] = useState<Order | null>(null)
  const [side, setSide] = useState<'all' | 'open'>('all')

  /* 生命周期泳道：x＝距意图的耗时（对数），每行一笔；缺环节不画点 */
  const laneOption = useMemo(() => {
    const series: unknown[] = []
    orders.forEach((o, yi) => {
      const pts = o.stages.map((s, si) => (s.t == null ? null : { value: [Math.max(0.01, s.t - t0(o)), yi], name: s.name, si, raw: s.t })).filter(Boolean) as { value: [number, number]; name: string; si: number; raw: number }[]
      const c = statusColor(p, o.status)
      series.push({
        type: 'line', data: pts.map((x) => ({ ...x, symbolSize: x.si === 0 ? 7 : 9 })), symbol: 'circle',
        lineStyle: { color: c, width: 2 }, itemStyle: { color: c }, z: 3,
        label: { show: true, position: 'top', color: p.muted, fontSize: 10, formatter: (q: { data: { name: string } }) => q.data.name },
      })
      if (o.status === 'warn') {
        const last = pts.at(-1)!
        series.push({ type: 'line', data: [last.value, [NOW - t0(o), yi]], symbol: ['none', 'triangle'], symbolSize: 10, lineStyle: { color: p.warn, type: 'dashed' }, itemStyle: { color: p.warn }, silent: true,
          endLabel: { show: true, formatter: `▲ 已等 ${Math.floor((NOW - t0(o)) / 60)} 分钟 · 不重发`, color: p.warn, fontSize: 11 } })
      }
    })
    return {
      animation: false, grid: { left: 170, right: 140, top: 22, bottom: 30 },
      tooltip: { ...tooltipStyle(p), trigger: 'item', formatter: (q: { data: { name: string; raw: number; value: [number, number] } }) => q.data?.name ? `<b>${orders[q.data.value[1]].id}</b><br/>${q.data.name}　${hms(q.data.raw)}<br/>距意图 +${q.data.value[0].toFixed(2)}s` : '' },
      xAxis: { type: 'log', min: 0.01, max: PM ? 40000 : 4000, name: '距意图（秒，对数）', nameLocation: 'middle', nameGap: 22, nameTextStyle: { color: p.muted, fontSize: 11 }, ...axisStyle(p), axisLabel: { ...axisStyle(p).axisLabel, formatter: (v: number) => (v < 1 ? `${Math.round(v * 1000)}ms` : v >= 3600 ? `${Math.round(v / 3600)}h` : v >= 60 ? `${Math.round(v / 60)}min` : `${v}s`) } },
      yAxis: { type: 'category', inverse: true, data: orders.map((o) => `${o.at}  ${o.side} ${o.code}`), ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12, fontFamily: 'Menlo, monospace' }, splitLine: { show: false } },
      series,
    }
  }, [p])

  const data = side === 'all' ? orders : orders.filter((o) => o.status === 'warn')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s={PM ? 'ok' : BAD ? 'bad' : 'warn'} icon={PM ? <CheckCircleFilled /> : <WarningFilled />} title={PM ? '今日 4 笔意图，全部终态' : BAD ? '停机闸已触发 · 1 笔等待券商回报' : '今日 4 笔意图，1 笔等待券商回报'}
        sub={BAD ? 'q-1116-0004 已 ACK 42 分钟未回报，仍不重发、不释放预留；10:32:35 起停机闸触发；下一个时点 11:25 午盘检查照常运行，但新开仓意图会被拦，减仓与撤单保留' : PM ? 'q-1116-0004 券商全天没有回报，15:30 收盘对账从券商快照确认已成交 100 股；券商受理时刻仍缺失，不推算' : 'q-1116-0004 已 ACK 36 分钟未回报：按规则不重发、不释放预留，等 15:30 收盘对账确认'}
        kpis={[
          { k: '全部成交', v: PM ? '3' : '2', sub: PM ? '含对账确认 1' : undefined, onClick: () => setSide('all') }, { k: '已撤', v: '1', onClick: () => setSide('all') },
          { k: '状态未知', v: PM ? '0' : '1', c: PM ? p.ok : p.warn, sub: PM ? '15:30 前曾有 1 笔' : '点击只看未决', onClick: () => setSide('open') },
          { k: '风控拦截', v: String(rejects.length), sub: '不计入委托', onClick: () => document.getElementById('rejects')?.scrollIntoView({ behavior: 'smooth' }) },
          { k: '意图 → ACK 中位', v: '0.45s' },
        ]} />

      <Panel p={p} title="每笔委托走到哪一步了？" info="每行一笔，从左到右依次经过：意图 → 风控通过 → 网关 ACK → 券商受理 → 终态。横轴是距意图的耗时（对数）。journal 没记时间的环节不画点，不推算。">
        <EChart height={230} option={laneOption} onClick={(q) => { const d = q.data as { value?: [number, number] }; if (d?.value) setSel(orders[d.value[1]]) }} />
      </Panel>

      <Panel p={p} title="委托" info="券商单号缺失时显示 —，不编造。点击行看完整事件与原始 journal。" bodyStyle={{ padding: '4px 0 0' }}
        extra={<Segmented size="small" value={side} onChange={(v) => setSide(v as 'all' | 'open')} options={[{ label: '全部', value: 'all' }, { label: '只看未决', value: 'open' }]} />}>
        <Table size="small" pagination={false} rowKey="id" dataSource={data} className="qs-click" onRow={(r) => ({ onClick: () => setSel(r) })}
          rowClassName={(r) => (r.id === sel?.id ? 'qs-row-sel' : '')} locale={{ emptyText: <EmptyNote p={p} text="没有未决委托" /> }}
          columns={[
            { title: '时间', dataIndex: 'at', width: 90, render: (v) => <Num>{v}</Num> },
            { title: '代码', dataIndex: 'code', render: (v) => <Num color={p.head}>{v}</Num> },
            { title: '方向', dataIndex: 'side', width: 60, filters: [{ text: '买', value: '买' }, { text: '卖', value: '卖' }], onFilter: (v, r) => r.side === v, render: (v) => <span style={{ color: v === '买' ? p.up : p.down }}>{v}</span> },
            { title: '价格', dataIndex: 'px', align: 'right', render: (v: number) => <Num>{v.toFixed(2)}</Num> },
            { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
            { title: '已成', dataIndex: 'filled', align: 'right', render: (v) => <Num>{v}</Num> },
            { title: '金额', align: 'right', render: (_, o) => <Num color={p.muted}>{(o.px * o.qty).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</Num> },
            { title: '状态', width: 110, render: (_, o) => <StatusTag s={o.status} p={p} text={o.statusText} /> },
            { title: 'client_order_id', dataIndex: 'id', render: (v) => <Copy>{v}</Copy> },
            { title: '券商单号', dataIndex: 'broker', render: (v) => v === '—' ? <Tooltip title="券商未回报，不编造单号"><span style={{ color: p.muted }}>—</span></Tooltip> : <Num color={p.text}>{v}</Num> },
          ]} />
      </Panel>

      <Panel p={p} style={{ scrollMarginTop: 60 }} title={<span id="rejects">被风控拦下的</span>} info="意图在发送前被规则拦截，没有到达网关；不占用额度。" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="at" dataSource={rejects}
          columns={[
            { title: '时间', dataIndex: 'at', width: 90, render: (v) => <Num>{v}</Num> },
            { title: '代码', dataIndex: 'code', render: (v) => <Num color={p.head}>{v}</Num> },
            { title: '数量', dataIndex: 'qty', align: 'right', render: (v) => <Num>{v}</Num> },
            { title: '规则', dataIndex: 'rule', render: (v) => <StatusTag s="bad" p={p} text={v === '停机闸' ? '✕ 停机闸' : v} /> },
            { title: '说明', dataIndex: 'detail', render: (v) => <span style={{ color: p.muted }}>{v}</span> },
          ]} />
      </Panel>

      <Drawer open={!!sel} onClose={() => setSel(null)} size={520} title={sel && `${sel.id} · ${sel.side} ${sel.code}`} extra={sel && <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><DrawerNav p={p} list={data} cur={sel} set={setSel} /><StatusTag s={sel.status} p={p} text={sel.statusText} /></span>}>
        {sel && (
          <>
            <Timeline items={(sel.stages.length > STAGES.length ? sel.stages.map((x) => x.name) : STAGES).map((name, i, arr) => {
              const st = sel.stages[i]
              const missing = !st || st.t == null
              const pending = !st && sel.status === 'warn'
              return {
                color: pending ? p.warn : missing ? p.idle : statusColor(p, i === arr.length - 1 ? sel.status : 'ok'),
                content: <div><b style={{ color: p.head }}>{st?.name ?? name}</b>　{missing ? <span style={{ color: pending ? p.warn : p.muted }}>{pending ? '▲ 等待中（不重发）' : '未记录'}</span> : <Num>{hms(st.t!)}</Num>}</div>,
              }
            })} />
            {PM && sel.id === 'q-1116-0004' && (
              <div style={{ border: `1px solid ${p.info}66`, background: p.info + '14', borderRadius: 4, padding: '8px 10px', margin: '0 0 12px', fontSize: 12 }}>
                <b style={{ color: p.info }}>终态怎么来的？</b> 券商全天没有推送回报；15:30 收盘对账读取券商当日成交流水，找到 10:05:51 成交 100 股，按此记为全部成交并释放预留。券商受理时刻没有直接记录，保持“未记录”，不用成交时刻反推。
              </div>
            )}
            {sel.status === 'warn' && (
              <div style={{ border: `1px solid ${p.warn}66`, background: p.warn + '14', borderRadius: 4, padding: '8px 10px', margin: '0 0 12px', fontSize: 12 }}>
                <b style={{ color: p.warn }}>▲ 为什么不重发？</b> 网关已 ACK，券商可能已经受理。重发可能造成重复卖出；预留 100 股不释放，收盘对账以券商流水确认终态。
              </div>
            )}
            <Typography.Title level={5} style={{ display: 'flex', justifyContent: 'space-between' }}>原始 journal（示意）<Button size="small" onClick={() => navigator.clipboard?.writeText(sel.id)}>复制委托号</Button></Typography.Title>
            <pre style={{ background: p.panel2, border: `1px solid ${p.border}`, borderRadius: 4, padding: 10, fontSize: 11, color: p.text, whiteSpace: 'pre-wrap' }}>
{`{"event":"intent","client_order_id":"${sel.id}","symbol":"${sel.code}","side":"${sel.side === '买' ? 'BUY' : 'SELL'}","qty":${sel.qty},"limit":${sel.px}}
{"event":"risk_pass","rules":6}
{"event":"gateway_ack","transport":"redis","seq":"…"}${sel.status === 'ok' || sel.status === 'closed' ? `\n{"event":"${sel.status === 'closed' ? 'cancelled' : sel.id === 'q-1116-0004' && PM ? 'recon_fill' : 'fill'}"${sel.id === 'q-1116-0004' && PM ? ',"source":"broker_snapshot_15:30","filled":100' : ''}}` : ''}`}
            </pre>
          </>
        )}
      </Drawer>
    </div>
  )
}
