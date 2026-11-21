import { Result, Table, Tooltip } from 'antd'
import { fetchReconciliation } from '../../api/trading'
import type { ReconCell } from '../../api/contracts.gen'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { useFetch } from '../../page/useFetch'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import { Missing } from '../../ui/Missing'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, useTradingDay, yuan } from './common'

const REVIEW = { pass: ['ok', '通过'], fail: ['bad', '失败'], unknown: ['idle', '未知'], confirm: ['warn', '待确认'], info: ['idle', '信息'] } as const
const LAYER_NAME = { cash: '资金', positions: '持仓', orders: '委托 / 成交' } as const
const CELL = { consistent: ['ok', '一致'], inconsistent: ['bad', '不一致'], unknown: ['idle', '未对账'], not_started: ['closed', '未启动'] } as const

export function ReconciliationPage() {
  const p = usePalette(); const day = useTradingDay(); const { data, error } = useFetch((s) => fetchReconciliation(day, s), [day])
  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="对账查询未启用" />
  const d = data; const failed = d.records.filter((x) => !x.consistent).length
  const days = [...new Set(d.heatmap.map((x) => x.day))]
  const cell = (dt: string, layer: ReconCell['layer']) => d.heatmap.find((x) => x.day === dt && x.layer === layer)
  // 标题只讲所选交易日：逐层写出一致 / 未对账，不把未对账算成一致
  const selectedLayers = (['cash', 'positions', 'orders'] as const).map((layer) => ({ layer, state: d.day.selected ? cell(d.day.selected, layer)?.state ?? 'unknown' : 'unknown' }))
  const okLayers = selectedLayers.filter((x) => x.state === 'consistent').map((x) => LAYER_NAME[x.layer])
  const openLayers = selectedLayers.filter((x) => x.state === 'unknown' || x.state === 'not_started').map((x) => LAYER_NAME[x.layer])
  const dayTitle = !d.day.selected ? '没有对账记录'
    : `${d.day.selected.slice(5)} ${okLayers.length ? `${okLayers.join('、')}对账一致` : '没有一致的对账层'}${openLayers.length ? `，${openLayers.join('、')}未对账` : ''}`
  const badDays = days.filter((dt) => d.heatmap.some((x) => x.day === dt && x.state === 'inconsistent')).length
  const reviewState = !d.review ? 'idle' : d.review.stale || d.review.overall === 'blocked' ? 'bad' : d.review.overall === 'needs_confirmation' ? 'warn' : 'ok'
  return <>
    <Hero s={failed ? 'bad' : reviewState} title={failed ? `${failed} 次对账不一致` : dayTitle}
      subtitle={`${dayNote(d.day)} · 页面只读，不提供解除入口；解除必须走受管入口。`}
      kpis={[
        ...(['cash', 'positions', 'orders'] as const).map((layer) => {
          const c = d.day.selected ? cell(d.day.selected, layer) : undefined
          const v = c ? CELL[c.state] : CELL.unknown
          return { k: LAYER_NAME[layer], v: `${v[0] === 'ok' ? '✓' : v[0] === 'bad' ? '✕' : '○'} ${v[1]}`, color: v[0] === 'ok' ? p.ok : v[0] === 'bad' ? p.bad : p.muted }
        }),
        { k: '近 30 日不一致', v: `${badDays} 天`, color: badDays ? p.warn : p.ok, sub: `共 ${days.length} 个有记录交易日` },
        { k: '停机闸', v: d.kill.label, color: d.kill.state === 'stopped' ? p.bad : d.kill.state === 'clear' ? p.ok : p.muted },
      ]} />
    <Panel title="近 30 个有记录交易日的三层对账" info="资金与持仓来自同一现有对账事实；委托/成交尚无独立层时明确显示未对账。">
      <div style={{ overflowX: 'auto' }}><table style={{ borderCollapse: 'separate', borderSpacing: 3, width: '100%', minWidth: 520 }}><thead><tr><th>交易日</th><th>资金</th><th>持仓</th><th>委托/成交</th></tr></thead><tbody>
        {days.map((dt) => <tr key={dt}><td>{dt}</td>{(['cash', 'positions', 'orders'] as const).map((layer) => { const c = cell(dt, layer); const v = c ? CELL[c.state] : CELL.unknown; return <td key={layer}><Tooltip title={c?.detail || (c?.at ? dateTime(c.at) : '没有独立记录')}><span style={{ display: 'block', padding: 5, textAlign: 'center', background: v[0] === 'ok' ? `${p.ok}22` : v[0] === 'bad' ? `${p.bad}22` : p.bg }}><StatusTag s={v[0]} text={v[1]} /></span></Tooltip></td> })}</tr>)}
      </tbody></table></div>
    </Panel>
    <Panel title="解除停机闸前还差什么？" info="读取 kill.py review live --record 的机器可读输出；记录过期也不能当通过。">
      {!d.review ? <Missing why={d.missing.kill_review ?? '核对记录缺失'} /> : <>
        <div style={{ marginBottom: 8 }}><StatusTag s={reviewState} text={`${d.review.overall}${d.review.stale ? ' · 已过期' : ''}`} /> <span style={{ color: p.muted }}>核对于 {dateTime(d.review.reviewed_at)}</span></div>
        <Table size="small" pagination={false} rowKey="id" dataSource={d.review.items} columns={[
          { title: '项目', dataIndex: 'label' }, { title: '判定', dataIndex: 'state', render: (v) => <StatusTag s={REVIEW[v as keyof typeof REVIEW][0]} text={REVIEW[v as keyof typeof REVIEW][1]} /> },
          { title: '事实与说明', dataIndex: 'detail' },
        ]} />
        <div style={{ marginTop: 8, color: p.warn }}>本页没有解除按钮。确认与解除继续使用受管命令。</div>
      </>}
    </Panel>
    <Panel title="风控门禁是什么？" info="只读配置与当前可计算值；修改走配置发布。">
      <Table size="small" pagination={false} rowKey="id" dataSource={d.risks} columns={[
        { title: '门禁', dataIndex: 'label' }, { title: '当前值', key: 'v', render: (_, r) => r.value == null ? <Missing why="当前事实不足" /> : r.unit === 'ratio' ? pct(r.value) : yuan(r.value) },
        { title: '硬上限', key: 'l', render: (_, r) => r.unit === 'ratio' ? pct(r.limit) : yuan(r.limit) },
        { title: '状态', dataIndex: 'state', render: (v) => <StatusTag s={v === 'blocked' ? 'bad' : v === 'warning' ? 'warn' : v === 'ok' ? 'ok' : 'idle'} text={{ blocked: '阻断', warning: '提醒', ok: '正常', unknown: '未知' }[v as string]} /> },
      ]} />
    </Panel>
    <Panel title="选中日的原始对账记录">
      <Table size="small" pagination={false} rowKey={(r) => `${r.at}-${r.layer}`} dataSource={d.records} scroll={{ x: 'max-content' }} columns={[
        { title: '时间', dataIndex: 'at', render: dateTime }, { title: '层', dataIndex: 'layer' }, { title: '模式', dataIndex: 'mode' },
        { title: '结果', dataIndex: 'consistent', render: (v) => <StatusTag s={v ? 'ok' : 'bad'} text={v ? '一致' : '不一致'} /> },
        { title: '现金差', dataIndex: 'cash_diff', render: (v) => v == null ? <Missing why="无法判定" /> : yuan(v) },
        { title: '不符项', dataIndex: 'mismatches' }, { title: '明细', dataIndex: 'detail' },
      ]} />
    </Panel>
  </>
}
