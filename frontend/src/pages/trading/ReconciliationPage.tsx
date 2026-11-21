import { Result, Table, Tag } from 'antd'
import { useMemo } from 'react'
import { fetchReconciliation } from '../../api/trading'
import type { ReconCell } from '../../api/contracts.gen'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { useFetch } from '../../page/useFetch'
import { setSearch } from '../../shell/router'
import { dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, useTradingDay, yuan } from './common'

const REVIEW = { pass: ['ok', '通过'], fail: ['bad', '失败'], unknown: ['idle', '未知'], confirm: ['warn', '待确认'], info: ['idle', '信息'] } as const
const LAYERS = ['cash', 'positions', 'orders'] as const
const LAYER_NAME = { cash: '资金', positions: '持仓', orders: '委托 / 成交' } as const
const CELL = { consistent: ['ok', '一致'], inconsistent: ['bad', '不一致'], unknown: ['idle', '未对账'], not_started: ['closed', '未启动'] } as const
type CellState = ReconCell['state']
const CELL_ORDER: CellState[] = ['consistent', 'inconsistent', 'unknown', 'not_started']
const CELL_LABEL: Record<CellState, string> = { consistent: '● 一致', inconsistent: '✕ 不一致', unknown: '○ 未对账', not_started: '— 未启动' }
const cellColor = (p: Palette, s: CellState) => s === 'consistent' ? p.ok : s === 'inconsistent' ? p.bad : s === 'unknown' ? p.idle : p.panel2
/** 不一致条数：只认对账明细里写明的数字，取不到就不标数字。 */
const countOf = (c: ReconCell) => { const m = c.detail?.match(/(\d+)\s*(条|项|处)/); return m ? Number(m[1]) : null }

export function ReconciliationPage() {
  const p = usePalette(); const day = useTradingDay(); const { data, error } = useFetch((s) => fetchReconciliation(day, s), [day])
  const days = useMemo(() => data?.enabled ? [...new Set(data.heatmap.map((x) => x.day))].sort() : [], [data])

  /** 30 日 × 3 层热力格：每列一个有记录的交易日、每行一个对账层；点格子切换到当天。 */
  const matrix = useMemo<EChartsOption | null>(() => {
    if (!data?.enabled || !days.length) return null
    const cells = LAYERS.flatMap((layer, y) => days.map((dt, x) => {
      const c = data.heatmap.find((h) => h.day === dt && h.layer === layer)
      const state: CellState = c?.state ?? 'unknown'
      const n = c && state === 'inconsistent' ? countOf(c) : null
      return {
        value: [x, y, CELL_ORDER.indexOf(state)], day: dt, layer, state, detail: c?.detail ?? null, at: c?.at ?? null,
        itemStyle: { color: cellColor(p, state), borderColor: p.panel, borderWidth: 2, borderRadius: 2 },
        label: { show: state === 'inconsistent', formatter: n == null ? '✕' : String(n), color: '#fff', fontSize: 11, fontWeight: 600 },
      }
    }))
    return {
      animation: false,
      grid: { left: 90, right: 10, top: 4, bottom: 24 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const c = (raw as { data: typeof cells[number] }).data
        return `<b>${LAYER_NAME[c.layer]}</b>　${c.day}<br/>${CELL_LABEL[c.state]}${c.detail ? `<br/><span style="opacity:.75">${c.detail}</span>` : ''}${c.at ? `<br/><span style="opacity:.6">${dateTime(c.at)}</span>` : ''}<br/><span style="opacity:.6">点击查看当天原始对账记录</span>`
      } },
      xAxis: { type: 'category', data: days.map((x) => x.slice(5)), axisLabel: { color: p.muted, interval: 2 }, axisTick: { show: false }, axisLine: { lineStyle: { color: p.border } } },
      yAxis: { type: 'category', inverse: true, data: LAYERS.map((l) => LAYER_NAME[l]), axisLabel: { color: p.text, fontSize: 12 }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'heatmap', data: cells, emphasis: { itemStyle: { borderColor: p.head, borderWidth: 1 } } }],
    }
  }, [data, days, p])

  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="对账查询未启用" />
  const d = data; const failed = d.records.filter((x) => !x.consistent).length
  const cell = (dt: string, layer: ReconCell['layer']) => d.heatmap.find((x) => x.day === dt && x.layer === layer)
  // 标题只讲所选交易日：逐层写出一致 / 未对账，不把未对账算成一致
  const selectedLayers = LAYERS.map((layer) => ({ layer, state: d.day.selected ? cell(d.day.selected, layer)?.state ?? 'unknown' : 'unknown' }))
  const okLayers = selectedLayers.filter((x) => x.state === 'consistent').map((x) => LAYER_NAME[x.layer])
  const openLayers = selectedLayers.filter((x) => x.state === 'unknown' || x.state === 'not_started').map((x) => LAYER_NAME[x.layer])
  const dayTitle = !d.day.selected ? '没有对账记录'
    : `${d.day.selected.slice(5)} ${okLayers.length ? `${okLayers.join('、')}对账一致` : '没有一致的对账层'}${openLayers.length ? `，${openLayers.join('、')}未对账` : ''}`
  const inconsistent = d.heatmap.filter((x) => x.state === 'inconsistent').sort((a, b) => b.day.localeCompare(a.day) || a.layer.localeCompare(b.layer))
  const badDays = new Set(inconsistent.map((x) => x.day)).size
  const reviewState = !d.review ? 'idle' : d.review.stale || d.review.overall === 'blocked' ? 'bad' : d.review.overall === 'needs_confirmation' ? 'warn' : 'ok'
  // 原型：停机闸生效（或核对正在进行）时才展开解除前核对清单
  const showReview = d.kill.state !== 'clear' || !!d.review?.engaged
  const pick = (dt: string) => setSearch({ day: dt })

  return <>
    <Hero s={failed ? 'bad' : reviewState} title={failed ? `${failed} 次对账不一致` : dayTitle}
      subtitle={`${dayNote(d.day)} · 页面只读，不提供解除入口；解除必须走受管入口。`}
      kpis={[
        ...LAYERS.map((layer) => {
          const c = d.day.selected ? cell(d.day.selected, layer) : undefined
          const v = c ? CELL[c.state] : CELL.unknown
          return { k: LAYER_NAME[layer], v: `${v[0] === 'ok' ? '✓' : v[0] === 'bad' ? '✕' : '○'} ${v[1]}`, color: v[0] === 'ok' ? p.ok : v[0] === 'bad' ? p.bad : p.muted }
        }),
        { k: '近 30 日不一致', v: `${badDays} 天`, color: badDays ? p.warn : p.ok, sub: `共 ${days.length} 个有记录交易日` },
        { k: '停机闸', v: d.kill.label, color: d.kill.state === 'stopped' ? p.bad : d.kill.state === 'clear' ? p.ok : p.muted },
      ]} />
    {showReview && <Panel title="解除停机闸前还差什么？" info="读取 kill.py review live --record 的机器可读输出；记录过期也不能当通过。" extra={<Tag color="error">只读 · 解除走受管入口</Tag>}>
      {!d.review ? <Missing why={d.missing.kill_review ?? '核对记录缺失'} /> : <>
        <div style={{ marginBottom: 8 }}><StatusTag s={reviewState} text={`${d.review.overall}${d.review.stale ? ' · 已过期' : ''}`} /> <span style={{ color: p.muted }}>核对于 {dateTime(d.review.reviewed_at)}</span></div>
        <Table size="small" pagination={false} rowKey="id" dataSource={d.review.items} columns={[
          { title: '项目', dataIndex: 'label' }, { title: '判定', dataIndex: 'state', render: (v) => <StatusTag s={REVIEW[v as keyof typeof REVIEW][0]} text={REVIEW[v as keyof typeof REVIEW][1]} /> },
          { title: '事实与说明', dataIndex: 'detail' },
        ]} />
        <div style={{ marginTop: 8, color: p.warn }}>本页没有解除按钮。确认与解除继续使用受管命令。</div>
      </>}
    </Panel>}
    <Panel title="最近 30 个交易日每一层都对上了吗？" info="每列一个有记录的交易日、每行一个对账层；红格里的数字取自对账明细。委托 / 成交尚无独立对账层时显示未对账。点格子查看当天原始记录。">
      <div aria-label="对账状态图例" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: '4px 12px', fontSize: 11, color: p.muted, marginBottom: 4 }}>
        {CELL_ORDER.map((s) => <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <i style={{ display: 'inline-block', width: 12, height: 10, borderRadius: 2, background: cellColor(p, s), outline: s === 'not_started' ? `1px solid ${p.border}` : undefined }} />{CELL_LABEL[s]}</span>)}
      </div>
      {matrix ? <div data-testid="recon-matrix" style={{ minWidth: 640 }}><EChart option={matrix} height={150}
        onClick={(raw) => { const x = (raw as { data?: { day?: string } }).data; if (x?.day) pick(x.day) }} /></div>
        : <Missing why="近 30 日没有对账记录" />}
    </Panel>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(380px, 100%), 1fr))', gap: 12 }}>
      <Panel title="风控门禁都在吗？" info="只读配置与当前可计算值；修改走配置发布。" extra={<Tag>只读 · 修改走配置发布</Tag>}>
        <Table size="small" pagination={false} rowKey="id" dataSource={d.risks} columns={[
          { title: '门禁', dataIndex: 'label', render: (v) => <span style={{ color: p.head }}>{v}</span> },
          { title: '硬上限', key: 'l', align: 'right', render: (_, r) => <Num>{r.unit === 'ratio' ? pct(r.limit) : yuan(r.limit)}</Num> },
          { title: '当前值', key: 'v', align: 'right', render: (_, r) => r.value == null ? <Missing why="当前事实不足" /> : <Num>{r.unit === 'ratio' ? pct(r.value) : yuan(r.value)}</Num> },
          { title: '状态', dataIndex: 'state', width: 90, render: (v) => <StatusTag s={v === 'blocked' ? 'bad' : v === 'warning' ? 'warn' : v === 'ok' ? 'ok' : 'idle'} text={{ blocked: '阻断', warning: '接近上限', ok: '正常', unknown: '未知' }[v as string]} /> },
        ]} />
      </Panel>
      <Panel title="三道闸现在什么状态？" info="停机闸各来源的当前状态与记录时刻；页面只展示，不提供清除入口。没有历史事件流时不伪造历史。" extra={<Tag>只读 · 清除走受管入口</Tag>}>
        <div style={{ marginBottom: 8 }}><StatusTag s={d.kill.state === 'stopped' ? 'bad' : d.kill.state === 'clear' ? 'ok' : 'idle'} text={d.kill.label} /> <span style={{ color: p.muted, fontSize: 12 }}>{d.kill.message}</span></div>
        {d.kill.sources.length ? <div style={{ display: 'grid', gap: 6 }}>
          {d.kill.sources.map((s) => {
            // 来源状态只有 stopped / clear / unknown（qsys.webapp.live_kill_status）
            const [tone, text] = s.state === 'stopped' ? [p.bad, '✕ 已生效'] : s.state === 'clear' ? [p.ok, '● 未生效'] : [p.muted, '○ 未知']
            return <div key={s.id} data-testid="kill-source" style={{ display: 'grid', gridTemplateColumns: '18px minmax(0, 1fr)', gap: 8, alignItems: 'start' }}>
              <span style={{ width: 10, height: 10, marginTop: 5, borderRadius: 5, background: tone }} />
              <div><b style={{ color: p.head, fontWeight: 500 }}>{s.name}</b> <span style={{ color: tone }}>{text}</span>
                <div style={{ fontSize: 12, color: p.muted }}>{s.recorded_at ? <Num size={12} color={p.muted}>{dateTime(s.recorded_at)}</Num> : '记录时刻未知'}{s.reason ? ` · ${s.reason}` : ''}</div></div>
            </div>
          })}
        </div> : <Missing why="没有读到各闸来源" />}
      </Panel>
    </div>
    <Panel title="不一致记录" info="近 30 个有记录交易日内的全部不一致格；点击切换到当天查看原始对账记录。">
      {inconsistent.length ? <Table size="small" pagination={false} rowKey={(r) => `${r.day}-${r.layer}`} dataSource={inconsistent}
        onRow={(r) => ({ onClick: () => pick(r.day), style: { cursor: 'pointer' } })} columns={[
          { title: '交易日', dataIndex: 'day', width: 110, render: (v) => <Num>{v}</Num> },
          { title: '层级', dataIndex: 'layer', width: 110, render: (v: ReconCell['layer']) => LAYER_NAME[v] },
          { title: '结论', key: 's', width: 100, render: () => <StatusTag s="bad" text="不一致" /> },
          { title: '说明', dataIndex: 'detail', render: (v) => v || <Missing why="对账明细没有说明" /> },
        ]} /> : <span style={{ color: p.muted }}>近 30 日没有不一致。</span>}
    </Panel>
    <Panel title={`${d.day.selected ?? '选中日'} 的原始对账记录`}>
      <Table size="small" pagination={false} rowKey={(r) => `${r.at}-${r.layer}`} dataSource={d.records} scroll={{ x: 'max-content' }} columns={[
        { title: '时间', dataIndex: 'at', render: dateTime }, { title: '层', dataIndex: 'layer' }, { title: '模式', dataIndex: 'mode' },
        { title: '结果', dataIndex: 'consistent', render: (v) => <StatusTag s={v ? 'ok' : 'bad'} text={v ? '一致' : '不一致'} /> },
        { title: '现金差', dataIndex: 'cash_diff', render: (v) => v == null ? <Missing why="无法判定" /> : yuan(v) },
        { title: '不符项', dataIndex: 'mismatches' }, { title: '明细', dataIndex: 'detail' },
      ]} />
    </Panel>
  </>
}
