import { useMemo, useState } from 'react'
import { Table, Tabs, Tag, Input, Segmented, Descriptions, Select, Drawer } from 'antd'
import { DatabaseFilled, WarningFilled, BookFilled } from '@ant-design/icons'
import type { Palette } from '../theme'
import type { Status } from '../mock'
import { EChart, Num, Panel, StatusTag, axisStyle, statusColor, tooltipStyle } from '../ui'
import { DrawerNav, Hero } from './common'
import { tradingDays } from './marketMock'
import { PM } from '../scene'
const QV = PM ? '11-16 19:40–19:48' : '11-13 19:40–19:49'
import { DIMS, domains, metrics, qualityRuns, rules, tables, violationTrend, type Table as T } from './dataMock'

const ORDER: Status[] = ['ok', 'warn', 'bad', 'idle']
const Dots = ({ p, dims }: { p: Palette; dims: Status[] }) => (
  <span style={{ display: 'inline-flex', gap: 3 }}>{dims.map((d, i) => <span key={i} title={`${DIMS[i]}：${d === 'ok' ? '正常' : d === 'warn' ? '警告' : d === 'bad' ? '失败' : '不适用'}`} style={{ width: 9, height: 9, borderRadius: d === 'warn' ? 0 : 5, background: d === 'idle' ? 'transparent' : statusColor(p, d), border: d === 'idle' ? `1px solid ${p.muted}` : 'none', transform: d === 'warn' ? 'rotate(45deg) scale(.85)' : undefined }} />)}</span>
)

/** 血缘：数据源 → 表 → 消费者（固定布局，从左到右） */
function Lineage({ p, focus, height = 260 }: { p: Palette; focus?: string; height?: number }) {
  const opt = useMemo(() => {
    const cols: [string, string[]][] = [
      ['数据源', ['Tushare Pro', 'QMT 原生', '付费源（冻结）']],
      ['真值表', ['tushare_daily', 'daily_basic', 'stk_limit', 'limit_list_d', 'stock_st_daily', 'bar_minute_1m', 'paid_daily']],
      ['派生', ['market_regime_daily', 'limit_up_shadow']],
      ['消费者', ['live-core 策略', '模拟账户', '回测', '日报', '打板看板']],
    ]
    const nodes = cols.flatMap(([, ns], ci) => ns.map((n, i) => ({ name: n, x: ci * 300, y: (i - (ns.length - 1) / 2) * 42, symbol: 'roundRect', symbolSize: [ci === 1 || ci === 2 ? 150 : 120, 24],
      itemStyle: { color: n === focus ? p.accent + '33' : p.panel2, borderColor: n === focus ? p.accent : n === 'bar_minute_1m' || n === 'daily_basic' || n === 'tushare_daily' ? p.warn : p.border, borderWidth: 1 },
      label: { color: n === focus ? p.head : p.text, fontSize: 11, fontFamily: ci === 1 || ci === 2 ? 'Menlo, monospace' : undefined } })))
    const E = (a: string, b: string) => ({ source: a, target: b, lineStyle: { color: focus && (a === focus || b === focus) ? p.accent : p.border, width: focus && (a === focus || b === focus) ? 1.6 : 1, curveness: 0.15, opacity: 0.9 } })
    const links = [
      ...['tushare_daily', 'daily_basic', 'stk_limit', 'limit_list_d', 'stock_st_daily'].map((t) => E('Tushare Pro', t)), E('QMT 原生', 'bar_minute_1m'), E('付费源（冻结）', 'paid_daily'),
      ...['tushare_daily', 'limit_list_d', 'stk_limit'].map((t) => E(t, 'market_regime_daily')), ...['tushare_daily', 'daily_basic', 'limit_list_d', 'bar_minute_1m'].map((t) => E(t, 'limit_up_shadow')),
      E('stk_limit', 'live-core 策略'), E('stock_st_daily', 'live-core 策略'), E('market_regime_daily', 'live-core 策略'), E('limit_list_d', '打板看板'), E('limit_up_shadow', '打板看板'),
      E('tushare_daily', '回测'), E('bar_minute_1m', '回测'), E('paid_daily', '回测'), E('stk_limit', '模拟账户'), E('market_regime_daily', '日报'),
    ]
    return { animation: false, tooltip: { ...tooltipStyle(p) },
      series: [{ type: 'graph', layout: 'none', roam: true, top: 20, bottom: 20, left: 80, right: 80, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 5, label: { show: true }, data: nodes, links,
        emphasis: { focus: 'adjacency', lineStyle: { color: p.accent, width: 2 }, itemStyle: { borderColor: p.accent } }, blur: { itemStyle: { opacity: 0.25 }, lineStyle: { opacity: 0.08 }, label: { opacity: 0.35 } } }] }
  }, [p, focus])
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '0 30px', color: p.muted, fontSize: 11 }}>{['数据源', '真值表', '派生', '消费者'].map((t) => <span key={t}>{t}</span>)}</div>
      <EChart height={height - 16} option={opt} onClick={(q) => { if (q.dataType === 'node' && /^[a-z_0-9]+$/.test(q.name)) { sessionStorage.setItem('qs-table', q.name); location.hash = 'tables' } }} />
    </div>
  )
}

/* ===================== 数据总览 ===================== */
export function DataOverview({ p }: { p: Palette }) {
  const matrix = useMemo(() => {
    const data: unknown[] = []
    tables.forEach((t, yi) => t.dims.forEach((s, xi) => data.push({ value: [xi, yi, ORDER.indexOf(s)], s })))
    return {
      animation: false, grid: { left: 150, right: 10, top: 30, bottom: 8 },
      tooltip: { ...tooltipStyle(p), formatter: (q: { value: [number, number]; data: { s: Status } }) => `<b>${tables[q.value[1]].name}</b><br/>${DIMS[q.value[0]]}：${{ ok: '● 正常', warn: '▲ 警告', bad: '✕ 失败', idle: '○ 不适用', running: '', closed: '' }[q.data.s]}` },
      xAxis: { type: 'category', position: 'top', data: DIMS, ...axisStyle(p), axisLabel: { color: p.text, fontSize: 12 }, splitLine: { show: false } },
      yAxis: { type: 'category', inverse: true, data: tables.map((t) => t.name), ...axisStyle(p), axisLabel: { color: p.text, fontSize: 11, fontFamily: 'Menlo, monospace' } },
      visualMap: { show: false, type: 'piecewise', pieces: [{ value: 0, color: p.ok }, { value: 1, color: p.warn }, { value: 2, color: p.bad }, { value: 3, color: p.idle }] },
      series: [{ type: 'heatmap', data, itemStyle: { borderColor: p.panel, borderWidth: 3, borderRadius: 3 },
        label: { show: true, color: '#000', fontSize: 10, formatter: (q: { data: { s: Status } }) => ({ ok: '●', warn: '▲', bad: '✕', idle: '', running: '', closed: '' }[q.data.s]) } }],
    }
  }, [p])
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="warn" icon={<WarningFilled />} title="12 张核心表 · 9 健康 · 3 有已知警告" sub={`最近一次全量质检 ${QV.slice(0, 6)}${QV.slice(-5)} · 无新增问题；警告均已登记 Issue`}
        kpis={[PM ? { k: '今日已到达', v: '11 / 11', sub: '盘前 3 张 · 收盘后 8 张', c: p.ok, tip: 'paid_daily 冻结不计' } : { k: '今日已到达', v: '3 / 11', sub: '盘前 3 张 · 其余收盘后', c: p.text, tip: 'stk_limit 08:58、stock_st_daily 08:40、suspend_d 09:05 已到；日线类 17:00 后到达。paid_daily 冻结不计' }, { k: PM ? '今日最晚到达' : '上一交易日全部到达', v: PM ? '17:10' : '11-13 17:10', sub: '最晚 limit_list_d' }, { k: '质检规则', v: '186', sub: '阻断 0 · 警告 34' }, { k: '仓库体量', v: '1.84 TB', sub: '数据盘余 41%' }]} />
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 3fr', gap: 10 }}>
        <Panel p={p} title="每张表在五个维度上都健康吗？" info="五维：新鲜度（按时到达）、完整性（覆盖/缺口）、有效性（网格/单位/价格）、一致性（跨源/日线对账）、关系（上下游一致）。○＝不适用。点击跳到该表。">
          <EChart height={420} option={matrix} onClick={(q) => { location.hash = 'tables'; sessionStorage.setItem('qs-table', tables[(q.value as number[])[1]].name) }} />
        </Panel>
        <Panel p={p} title="数据从哪来、被谁用？" info="数据源 → 真值表 → 派生 → 消费者。黄框＝有已知警告。悬停高亮上下游，点击表名打开详情；可拖动、滚轮缩放。">
          <Lineage p={p} height={420} />
        </Panel>
      </div>
      <Panel p={p} title="按数据域" bodyStyle={{ padding: '4px 0 0' }}>
        <Table size="small" pagination={false} rowKey="d" dataSource={domains} columns={[
          { title: '数据域', dataIndex: 'd', render: (v) => <b style={{ color: p.head }}>{v}</b> }, { title: '表数', dataIndex: 'n', align: 'right', render: (v) => <Num>{v}</Num> },
          { title: '状态', dataIndex: 's', render: (s: Status) => <StatusTag p={p} s={s} text={s === 'idle' ? '冻结' : undefined} /> }, { title: '说明', dataIndex: 'note', render: (v) => <span style={{ color: p.muted }}>{v || '—'}</span> },
        ]} />
      </Panel>
    </div>
  )
}

/* ===================== 数据表 ===================== */
export function DataTables({ p }: { p: Palette }) {
  const [q, setQ] = useState('')
  const [dom, setDom] = useState<string>('全部')
  const [sel, setSel] = useState<T>(() => tables.find((t) => t.name === sessionStorage.getItem('qs-table')) ?? tables[1])
  const rows = tables.filter((t) => (dom === '全部' || t.domain === dom) && t.name.includes(q))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Panel p={p} title="数据表" bodyStyle={{ padding: '4px 0 0' }} extra={<div style={{ display: 'flex', gap: 8 }}>
        <Segmented size="small" value={dom} onChange={(v) => setDom(v as string)} options={['全部', '行情', '证券', '事件', '衍生']} />
        <Input size="small" allowClear placeholder="搜索表名" style={{ width: 180 }} value={q} onChange={(e) => setQ(e.target.value)} />
      </div>}>
        <Table size="small" pagination={false} rowKey="name" dataSource={rows} className="qs-click"
          onRow={(r) => ({ onClick: () => { setSel(r); setTimeout(() => document.getElementById('tbl-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0) } })}
          rowClassName={(r) => (r.name === sel.name ? 'qs-row-sel' : '')}
          locale={{ emptyText: <span style={{ color: p.muted }}>没有匹配「{q}」的表</span> }}
          columns={[
            { title: '表', dataIndex: 'name', render: (v, r) => <div><Num color={p.head}>{v}</Num><div style={{ color: p.muted, fontSize: 12 }}>{r.desc}</div></div> },
            { title: '域', dataIndex: 'domain', width: 60 }, { title: '层', dataIndex: 'layer', width: 90, render: (v) => <Tag>{v}</Tag> },
            { title: '粒度', dataIndex: 'grain', width: 110 }, { title: '行数', dataIndex: 'rows', align: 'right', width: 90, render: (v) => <Num>{v}</Num> },
            { title: '覆盖', dataIndex: 'cover', render: (v) => <Num size={12} color={p.muted}>{v}</Num> },
            { title: '最近更新', dataIndex: 'fresh', width: 130, render: (v: string) => v.startsWith('11-16')
              ? <span><Num size={12} color={p.ok}>{v.slice(6)}</Num> <span style={{ color: p.muted, fontSize: 11 }}>今天</span></span>
              : v === '冻结' ? <span style={{ color: p.muted }}>冻结</span>
              : <span title="今天的数据收盘后到达，属正常"><Num size={12}>{v}</Num> <span style={{ color: p.muted, fontSize: 11 }}>· 收盘后更新</span></span> },
            { title: '五维', dataIndex: 'dims', width: 90, render: (d) => <Dots p={p} dims={d} /> },
            { title: '角色', dataIndex: 'role', width: 120, render: (v) => <span style={{ color: v === '真值' ? p.info : p.muted }}>{v}</span> },
          ]} />
      </Panel>
      <Panel p={p} style={{ scrollMarginTop: 56 }} title={<span id="tbl-detail"><Num color={p.head} size={14} weight={600}>{sel.name}</Num> <span style={{ color: p.muted, fontWeight: 400, fontSize: 12 }}>{sel.desc}</span></span>} extra={<Dots p={p} dims={sel.dims} />}>
        <Tabs size="small" items={[
          { key: 'o', label: '概览', children: <Descriptions size="small" column={3} bordered items={[
            { key: 'a', label: '含义', children: sel.desc, span: 3 }, { key: 'b', label: '层 / 粒度', children: `${sel.layer} · ${sel.grain}` }, { key: 'c', label: '行数', children: <Num>{sel.rows}</Num> },
            { key: 'd', label: '覆盖', children: <Num>{sel.cover}</Num> }, { key: 'e', label: '构建器', children: <Num>{sel.owner}</Num> }, { key: 'f', label: '分区 / 排序', children: <Num>year / (trade_date, ts_code)</Num> },
            { key: 'g', label: '更新方式', children: sel.fresh === '冻结' ? '不再更新' : sel.fresh.startsWith('11-16') ? '每交易日盘前' : sel.layer === 'derived' ? '每交易日 19:00 后（依赖上游）' : '每交易日 17:00 增量' }, { key: 'h', label: '根源接口', children: <Num>{sel.name.replace('tushare_', '')}</Num> }, { key: 'i', label: '生命周期', children: sel.layer === 'archive' ? '冻结 · 不可删除' : '活跃' },
          ]} /> },
          { key: 'f', label: '字段', children: <Table size="small" pagination={false} rowKey="f" dataSource={[
            { f: 'trade_date', t: 'date', k: '直取', m: '交易日' }, { f: 'ts_code', t: 'string', k: '直取', m: '证券代码' }, { f: 'close', t: 'float64', k: '直取', m: '收盘价（元）' },
            { f: 'circ_mv', t: 'float64', k: '直取', m: '流通市值（万元）' }, { f: 'turnover_rate', t: 'float64', k: '直取', m: '换手率（%）' }, { f: 'float_share', t: 'float64', k: '直取', m: '流通股本（万股）' },
          ]} columns={[{ title: '字段', dataIndex: 'f', render: (v) => <Num>{v}</Num> }, { title: '类型', dataIndex: 't', render: (v) => <Num color={p.muted}>{v}</Num> }, { title: '口径', dataIndex: 'k', render: (v) => <Tag color={v === '直取' ? 'blue' : 'default'}>{v}</Tag> }, { title: '含义', dataIndex: 'm' }]} /> },
          { key: 'q', label: '质量', children: <Table size="small" pagination={false} rowKey="id" dataSource={rules.filter((r) => r.obj.includes(sel.name)).concat(rules.filter((r) => !r.obj.includes(sel.name)).slice(0, 0))}
            locale={{ emptyText: '这张表的规则全部通过' }}
            columns={[{ title: '规则', dataIndex: 'name' }, { title: '层', dataIndex: 'layer' }, { title: '违规', dataIndex: 'v', render: (v) => <Num>{v}</Num> }, { title: '结果', render: (_, r) => <StatusTag p={p} s={r.s} text={r.known || undefined} /> }, { title: 'Issue', dataIndex: 'issue' }]} /> },
          { key: 'l', label: '血缘', children: <Lineage p={p} focus={sel.name} /> },
          { key: 's', label: '样本', children: (
            <>
              <Table size="small" pagination={false} rowKey="k" dataSource={[
                { k: 1, d: '2026-11-13', c: '600xxx.SH', close: 16.31, mv: 812345.2 }, { k: 2, d: '2026-11-13', c: '000xxx.SZ', close: 19.42, mv: null },
                { k: 3, d: '2026-11-13', c: '300xxx.SZ', close: 9.85, mv: 211873.9 }, { k: 4, d: '2026-11-12', c: '600xxx.SH', close: 15.98, mv: 795912.0 },
              ]} columns={[{ title: 'trade_date', dataIndex: 'd', render: (v) => <Num size={12}>{v}</Num> }, { title: 'ts_code', dataIndex: 'c', render: (v) => <Num size={12}>{v}</Num> },
                { title: 'close', dataIndex: 'close', align: 'right', render: (v: number) => <Num size={12}>{v.toFixed(2)}</Num> },
                { title: 'circ_mv', dataIndex: 'mv', align: 'right', render: (v: number | null) => v == null ? <span title="源接口未返回，按规则记 null，不填充" style={{ color: p.warn, fontFamily: 'Menlo, monospace', fontSize: 12 }}>null</span> : <Num size={12}>{v.toLocaleString('zh-CN', { minimumFractionDigits: 1 })}</Num> }]} />
              <div style={{ color: p.muted, fontSize: 12, marginTop: 6 }}>只读抽样（示意，代码已打码）；拿不到的值显示 <span style={{ color: p.warn, fontFamily: 'Menlo, monospace' }}>null</span>，不填充。正式版走受限查询入口，带 memory_limit。</div>
            </>) },
        ]} />
      </Panel>
    </div>
  )
}

/* ===================== 指标字典 ===================== */
/** 抽样证据的示意值：按指标单位给出 */
const EV: Record<string, [string, string]> = {
  pct_chg: ['+2.07%', '−0.92%'], circ_mv: ['812,345.2 万元', '211,873.9 万元'], limit_up_price: ['17.94', '49.52'], is_st: ['否', '否'],
  seal_ratio: ['3.12', '2.40'], first_bar_vol: ['12,300 股', '0（上游原值）'], adv_ratio: ['63.1%', '48.7%'], prev_close: ['15.98', '45.02'],
}
export function Catalog({ p }: { p: Palette }) {
  const sorted = [...metrics].sort((a, b) => (a.s === 'warn' ? -1 : 0) - (b.s === 'warn' ? -1 : 0))
  const [sel, setSel] = useState(sorted[0])
  const [kind, setKind] = useState<string | null>(null)
  const [mq, setMq] = useState('')
  const days = tradingDays(30)
  const vt = violationTrend(sel.id.length * 31)
  const opt = useMemo(() => ({
    animation: false, grid: { left: 32, right: 8, top: 10, bottom: 22 }, tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    xAxis: { type: 'category', data: days, ...axisStyle(p), splitLine: { show: false } }, yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
    series: [{ type: 'bar', data: sel.s === 'ok' ? vt.map(() => 0) : vt, itemStyle: { color: p.warn } }],
  }), [p, sel]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="warn" icon={<BookFilled />} title="128 个指标 · 2 个有问题" sub="问题排在最前；每个指标标明直取或纯派生，以及来源字段"
        kpis={[{ k: '直取', v: '94' }, { k: '纯派生', v: '34' }, { k: '有问题', v: '2', c: p.warn }, { k: '同花顺抽样', v: '近 30 日 60 次', sub: '全部一致', c: p.ok }]} />
      <div style={{ display: 'grid', gridTemplateColumns: '2fr 3fr', gap: 10 }}>
        <Panel p={p} title="指标" bodyStyle={{ padding: '4px 0 0' }} extra={<div style={{ display: 'flex', gap: 6 }}><Input size="small" allowClear placeholder="搜索指标 / 字段" style={{ width: 140 }} value={mq} onChange={(e) => setMq(e.target.value)} /><Select size="small" allowClear placeholder="类型" style={{ width: 90 }} value={kind} onChange={setKind} options={[{ value: '直取', label: '直取' }, { value: '纯派生', label: '纯派生' }]} /></div>}>
          <Table size="small" pagination={false} rowKey="id" className="qs-click" dataSource={sorted.filter((m) => (!kind || m.kind === kind) && (!mq || (m.name + m.id + m.src).toLowerCase().includes(mq.toLowerCase())))}
            onRow={(r) => ({ onClick: () => setSel(r) })} rowClassName={(r) => (r.id === sel.id ? 'qs-row-sel' : '')}
            footer={() => <span style={{ color: p.muted, fontSize: 11 }}>原型显示 8 / 128 个指标；有问题的排在最前</span>}
            columns={[
              { title: '指标', dataIndex: 'name', render: (v, r) => <div><span style={{ color: p.head }}>{v}</span> <Num size={11} color={p.muted}>{r.id}</Num></div> },
              { title: '类型', dataIndex: 'kind', width: 70, render: (v) => <Tag color={v === '直取' ? 'blue' : 'default'}>{v}</Tag> },
              { title: '状态', dataIndex: 's', width: 90, render: (s: Status) => <StatusTag p={p} s={s} text={s === 'warn' ? '有问题' : '正常'} /> },
            ]} />
        </Panel>
        <Panel p={p} title={<>{sel.name} <Num size={12} color={p.muted}>{sel.id}</Num></>} extra={<StatusTag p={p} s={sel.s} text={sel.s === 'warn' ? `最近一次：${sel.valid}` : sel.valid} />}>
          <Descriptions size="small" column={2} bordered items={[
            { key: 'a', label: '含义', children: sel.desc, span: 2 }, { key: 'b', label: '类别', children: sel.kind }, { key: 'c', label: '来源', children: <Num size={12}>{sel.src}</Num> },
          ]} />
          <div style={{ color: p.head, fontWeight: 600, margin: '12px 0 4px' }}>近 30 日违规数</div>
          <EChart height={130} option={opt} />
          <div style={{ color: p.head, fontWeight: 600, margin: '8px 0 4px' }}>人工与第三方证据</div>
          <Table size="small" pagination={false} rowKey="d" dataSource={[
            { d: '11-13', code: '600xxx.SH', ours: EV[sel.id]?.[0] ?? '—', ref: EV[sel.id]?.[0] ?? '—', who: '维护者 · 同花顺', ok: true }, { d: '11-12', code: '300xxx.SZ', ours: EV[sel.id]?.[1] ?? '—', ref: EV[sel.id]?.[1] ?? '—', who: '维护者 · 同花顺', ok: true },
          ]} columns={[{ title: '日期', dataIndex: 'd', render: (v) => <Num>{v}</Num> }, { title: '标的', dataIndex: 'code', render: (v) => <Num>{v}</Num> }, { title: '我们的值', dataIndex: 'ours', render: (v) => <Num>{v}</Num> },
            { title: '对照值', dataIndex: 'ref', render: (v) => <Num>{v}</Num> }, { title: '验的人', dataIndex: 'who' }, { title: '判定', render: () => <StatusTag p={p} s="ok" text="一致" /> }]} />
        </Panel>
      </div>
    </div>
  )
}

/* ===================== 数据质量 ===================== */
export function Quality({ p }: { p: Palette }) {
  const runs = qualityRuns()
  const days = tradingDays(30)
  const [layer, setLayer] = useState('全部')
  const [rule, setRule] = useState<(typeof rules)[number] | null>(null)
  const shownRules = rules.filter((r) => layer === '全部' || r.layer === layer)
  const opt = useMemo(() => ({
    animation: false, grid: { left: 40, right: 10, top: 26, bottom: 22 },
    legend: { top: 0, right: 0, textStyle: { color: p.muted, fontSize: 11 } }, tooltip: { ...tooltipStyle(p), trigger: 'axis' },
    xAxis: { type: 'category', data: days, ...axisStyle(p), splitLine: { show: false } }, yAxis: { type: 'value', max: 186, ...axisStyle(p) },
    series: [
      { name: '● 通过', type: 'bar', stack: 'a', data: runs.map((r) => r.pass), itemStyle: { color: p.ok, opacity: 0.75 } },
      { name: '▲ 警告', type: 'bar', stack: 'a', data: runs.map((r) => r.warn), itemStyle: { color: p.warn } },
      { name: '✕ 阻断', type: 'bar', stack: 'a', data: runs.map((r) => r.block), itemStyle: { color: p.bad } },
    ],
  }), [p]) // eslint-disable-line react-hooks/exhaustive-deps
  const src = [{ k: '历史字段缺口（#594）', n: 28 }, { k: '上游数据自相矛盾（#653）', n: 4 }, { k: '其他（待归类）', n: 2 }]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <Hero p={p} s="warn" icon={<WarningFilled />} title="最近一次质检：0 阻断 · 34 警告（无新增）" sub={`catalog-verify ${QV} · 186 条规则 · 输入指纹已绑定 · 结果已传播到服务检查`}
        kpis={[{ k: '字段级', v: '0 / 32', sub: '阻断 / 警告' }, { k: '表级', v: '0 / 0', c: p.ok }, { k: '关系级', v: '0 / 2' }, { k: '待重验', v: '0', sub: '输入未变' }]} />
      <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: 10 }}>
        <Panel p={p} title="近 30 次质检的结果构成" info="每列一次 catalog-verify；警告为已知问题时不让任务失败，但仍如实计数。">
          <EChart height={220} option={opt} />
        </Panel>
        <Panel p={p} title="警告都来自哪？" info="按 known_issue 归类；“其他”需要尽快归类或开 Issue。">
          {src.map((s) => (
            <div key={s.k} style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}><span>{s.k}</span><Num>{s.n}</Num></div>
              <div style={{ height: 8, background: p.idle, borderRadius: 2, marginTop: 4 }}><div style={{ width: `${(s.n / 34) * 100}%`, height: '100%', background: s.k.startsWith('其他') ? p.warn : p.muted, borderRadius: 2 }} /></div>
            </div>
          ))}
        </Panel>
      </div>
      <Panel p={p} title="规则" bodyStyle={{ padding: '4px 0 0' }} extra={<Segmented size="small" value={layer} onChange={(v) => setLayer(v as string)} options={['全部', '字段', '表', '关系']} />}>
        <Table size="small" pagination={false} rowKey="id" dataSource={shownRules} className="qs-click"
          onRow={(r) => ({ onClick: () => setRule(r) })} rowClassName={(r) => (r.id === rule?.id ? 'qs-row-sel' : '')}
          columns={[
            { title: '规则', dataIndex: 'name', render: (v, r) => <div><span style={{ color: p.head }}>{v}</span> <Num size={11} color={p.muted}>{r.id}</Num></div> },
            { title: '对象', dataIndex: 'obj', render: (v) => <Num size={12}>{v}</Num> }, { title: '层', dataIndex: 'layer', width: 60 },
            { title: '违规 / 行数', render: (_, r) => <Num>{r.v} / {r.rows}</Num> },
            { title: '结果', width: 100, render: (_, r) => <StatusTag p={p} s={r.s} text={r.s === 'ok' ? '通过' : '警告'} /> },
            { title: '已知问题', dataIndex: 'known', render: (v) => (v ? <Tag>{v}</Tag> : <span style={{ color: p.muted }}>—</span>) },
            { title: 'Issue', dataIndex: 'issue', render: (v) => <Num color={p.info}>{v || '—'}</Num> },
          ]} />
      </Panel>
      <Drawer open={!!rule} onClose={() => setRule(null)} size={540} title={rule && <>{rule.name} <Num size={12} color={p.muted}>{rule.id}</Num></>}
        extra={rule && <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><DrawerNav p={p} list={shownRules} cur={rule} set={setRule} /><StatusTag p={p} s={rule.s} text={rule.s === 'ok' ? '通过' : '警告'} /></span>}>
        {rule && (
          <>
            <Descriptions size="small" column={1} bordered items={[
              { key: 'a', label: '对象 / 层', children: <><Num size={12}>{rule.obj}</Num> · {rule.layer}级</> },
              { key: 'b', label: '违规 / 检查行数', children: <Num>{rule.v} / {rule.rows}</Num> },
              { key: 'c', label: '判定', children: rule.s === 'ok' ? '通过' : rule.known === '上游数据自相矛盾' ? '警告（比较的字段都是同一上游的直取原值，只警告，不让任务失败）' : '警告（已知问题，登记在 Issue）' },
              { key: 'd', label: '最近一次', children: <Num>{QV} · catalog-verify</Num> },
            ]} />
            {rule.v > 0 ? (
              <>
                <div style={{ color: p.head, fontWeight: 600, margin: '14px 0 6px' }}>违规样本（前 {Math.min(rule.v, 3)} 条，示意）</div>
                <Table size="small" pagination={false} rowKey="k" dataSource={Array.from({ length: Math.min(rule.v, 3) }, (_, i) => ({ k: i, d: (PM ? ['11-16', '11-13', '11-12'] : ['11-13', '11-12', '11-10'])[i], c: ['600xxx.SH', '300xxx.SZ', '000xxx.SZ'][i] }))}
                  columns={[{ title: '交易日', dataIndex: 'd', render: (v) => <Num size={12}>{v}</Num> }, { title: '代码', dataIndex: 'c', render: (v) => <Num size={12}>{v}</Num> },
                    { title: '原值', render: () => <span style={{ color: p.muted, fontSize: 12 }}>按原样保存，不改写</span> }]} />
                <div style={{ color: p.muted, fontSize: 12, marginTop: 8 }}>处理：在 {rule.issue} 回源核实；修正只能用真实直取数据，不填充、不推算。</div>
              </>
            ) : <div style={{ color: p.muted, marginTop: 14 }}>这条规则最近一次全部通过。</div>}
          </>
        )}
      </Drawer>
    </div>
  )
}

export { DatabaseFilled }
