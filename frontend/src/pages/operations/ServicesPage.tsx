import { Descriptions, Drawer, Result, Segmented, Table, Tag, Typography } from 'antd'
import type { CustomSeriesRenderItem } from 'echarts'
import { useMemo, useState } from 'react'
import { fetchServices, type ServiceRow, type ServiceRunItem } from '../../api/operations'
import { EChart, type EChartsOption } from '../../page/EChart'
import { DrawerNav } from '../../page/DrawerNav'
import { pct } from '../../page/format'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { useFetch } from '../../page/useFetch'
import { axisHm, dateTime } from '../../shell/time'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { stateTag, type OpsState } from './common'

type View = 'matrix' | 'timeline'

const STATE_ORDER: OpsState[] = ['ok', 'warning', 'failed', 'fixed', 'running', 'unknown', 'not_due', 'paused']
/** 图例与提示用“符号 + 文字”，不单靠颜色（设计文档 §7）。 */
const STATE_LABEL: Record<OpsState, string> = {
  ok: '● 正常', warning: '▲ 警告', failed: '✕ 失败', fixed: '◆ 已修复', running: '◐ 运行中',
  unknown: '○ 未知', not_due: '— 休市 / 未到期', paused: '— 已暂停',
}
const LEGEND: OpsState[] = ['ok', 'warning', 'failed', 'fixed', 'running', 'unknown', 'not_due']
/** 矩阵只放每日任务；月度任务一个月只跑一次，放进矩阵整行空着，只在服务清单展示。 */
const MATRIX_CADENCE = new Set(['daily', 'everyday'])
const KIND_LABEL: Record<string, string> = { resident: '常驻', supervisor: '守护' }
const CADENCE_LABEL: Record<string, string> = { monthly: '月度', monthly_closed: '月度', everyday: '含休市日' }

function color(p: Palette, state: OpsState) {
  return state === 'ok' ? p.ok : state === 'warning' ? p.warn : state === 'failed' ? p.bad
    : state === 'fixed' ? p.info : state === 'running' ? p.running
      // 未知是“缺证据”，用中性暗灰；休市/未到期贴近背景，让交易日的结果一眼可见
      : state === 'unknown' ? p.idle : p.panel2
}

const md = (day: string) => day.slice(5)
const clip = (text: string, n = 48) => text.length > n ? `${text.slice(0, n)}…` : text

function duration(value: number | null) {
  if (value == null) return null
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)}s`
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`
}

function runState(value: string): OpsState {
  return value === 'succeeded' ? 'ok' : value === 'warning' ? 'warning' : value === 'repaired' ? 'fixed'
    : value === 'running' ? 'running' : value === 'skipped' ? 'not_due' : 'failed'
}

function DurationSpark({ row }: { row: ServiceRow }) {
  const points = row.duration_history.map((x) => x.duration_seconds)
  if (points.length < 2) return <Missing why="至少需要两次完整运行" />
  const width = 116; const height = 24
  const lo = Math.min(...points); const hi = Math.max(...points); const span = hi - lo || 1
  const path = points.map((value, index) => {
    const x = index * width / (points.length - 1)
    const y = height - 3 - (value - lo) / span * (height - 6)
    return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return <svg role="img" aria-label={`${row.name} 近 ${points.length} 次耗时趋势`} viewBox={`0 0 ${width} ${height}`} width={width} height={height}>
    <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
  </svg>
}

function RunDrawer({ run, runs, onPick, onClose }: {
  run: ServiceRunItem; runs: ServiceRunItem[]; onPick: (run: ServiceRunItem) => void; onClose: () => void
}) {
  return <Drawer open onClose={onClose} title={`${run.service_id} · ${run.session ?? run.started_at.slice(0, 10)}`} width={620}
    extra={<DrawerNav rows={runs} current={run} identify={(x) => x.run_id} onPick={onPick} copyText={run.run_id} />}>
    <Descriptions size="small" bordered column={1} items={[
      { key: 'state', label: '结果', children: stateTag(runState(run.state)) },
      { key: 'start', label: '开始', children: dateTime(run.started_at) },
      { key: 'finish', label: '结束', children: run.finished_at ? dateTime(run.finished_at) : <Missing why="运行尚未结束" /> },
      { key: 'id', label: 'Run ID', children: <Typography.Text code copyable>{run.run_id}</Typography.Text> },
      { key: 'detail', label: '说明', children: run.error || run.note || '—' },
    ]} />
    <Typography.Title level={5} style={{ marginTop: 18 }}>运行步骤与证据</Typography.Title>
    {run.steps.length ? <Table size="small" pagination={false} rowKey={(x) => `${x.name}-${x.started_at ?? ''}`} dataSource={run.steps} columns={[
      { title: '步骤', dataIndex: 'name' }, { title: '状态', dataIndex: 'state' },
      { title: '开始', dataIndex: 'started_at', render: (v) => v ? dateTime(v) : '—' },
      { title: '结束', dataIndex: 'finished_at', render: (v) => v ? dateTime(v) : '—' },
      { title: '影响总结果', dataIndex: 'affects_run', render: (v) => v ? '是' : '否' },
      { title: '说明 / 证据', dataIndex: 'detail', render: (v) => v || '—' },
    ]} /> : <Missing why="本次运行没有结构化步骤记录" />}
  </Drawer>
}

export function ServicesPage() {
  const p = usePalette()
  const day = new URLSearchParams(location.search).get('day')
  const { data, error } = useFetch((signal) => fetchServices(day, signal), [day])
  const [view, setView] = useState<View>('matrix')
  const [run, setRun] = useState<ServiceRunItem | null>(null)

  const model = useMemo(() => {
    if (!data?.enabled) return null
    const selected = data.selected_day
    const tradingDays = data.trading_days ?? {}
    const hasCalendar = Object.keys(tradingDays).length > 0
    const selectedClosed = tradingDays[selected] === false
    const timers = data.services.filter((x) => x.kind === 'timer')
    const residents = data.services.filter((x) => x.kind !== 'timer')
    const matrixRows = timers.filter((x) => MATRIX_CADENCE.has(x.cadence))
    const byRun = new Map(data.runs.map((x) => [x.run_id, x]))
    const runDay = (x: ServiceRunItem) => x.session ?? x.started_at.slice(0, 10)
    const completeRuns = data.runs.filter((x) => x.finished_at)
    // 时间线：所选日是交易日且有完整运行就画当日，否则画上一交易日。交易日只认 trade_cal 直取；
    // 拿不到日历时退回“此前最近一个有完整运行的日期”，不按星期推算。
    const candidates = [...new Set(completeRuns.map(runDay))].filter((d) => d <= selected
      && (!hasCalendar || tradingDays[d] === true)).sort()
    const timelineDay = candidates.at(-1) ?? null
    const timelineRuns = timelineDay == null ? [] : completeRuns.filter((x) => runDay(x) === timelineDay)
    const timelineLabel = timelineDay === selected ? '当日时间线' : hasCalendar ? '上一交易日时间线' : '最近运行日时间线'

    const cellOf = (row: ServiceRow, d: string) => row.cells.find((x) => x.day === d)
    const selectedCells = timers.map((row) => cellOf(row, selected)).filter((x) => x !== undefined)
    const knownDue = selectedCells.filter((x) => x.state !== 'not_due' && x.state !== 'unknown')
    const finished = knownDue.filter((x) => !['running', 'paused'].includes(x.state)).length
    const running = knownDue.filter((x) => x.state === 'running').length
    const unknown = selectedCells.filter((x) => x.state === 'unknown').length

    // 结论日：所选日有可判定的到期任务就说所选日；否则（休市或尚未运行）说上一交易日。
    const conclusionDay = !selectedClosed && knownDue.length ? selected : (timelineDay ?? selected)
    const conclusionLabel = conclusionDay === selected ? (selectedClosed ? `${selected} 休市日` : selected) : `上一交易日 ${md(conclusionDay)}`
    const verdict = timers.map((row) => ({ row, cell: cellOf(row, conclusionDay) }))
      .filter((x): x is { row: ServiceRow; cell: NonNullable<typeof x.cell> } => x.cell !== undefined)
    const failedRows = verdict.filter((x) => x.cell.state === 'failed')
    const warnRows = verdict.filter((x) => x.cell.state === 'warning')
    const okRows = verdict.filter((x) => ['ok', 'fixed'].includes(x.cell.state))
    const pendingRows = verdict.filter((x) => ['running', 'unknown'].includes(x.cell.state))

    const allCells = timers.flatMap((x) => x.cells)
    const observed = allCells.filter((x) => !['not_due', 'unknown', 'paused', 'running'].includes(x.state))
    const completed = observed.filter((x) => ['ok', 'warning', 'fixed'].includes(x.state)).length
    const failedDays = allCells.filter((x) => x.state === 'failed').length
    const fixedDays = allCells.filter((x) => x.state === 'fixed').length
    const residentKnown = residents.filter((x) => !['unknown', 'not_due'].includes(x.latest_state))
    const residentOk = residentKnown.filter((x) => ['ok', 'fixed'].includes(x.latest_state)).length
    const residentBad = residentKnown.filter((x) => x.latest_state === 'failed').length
    const timed = timelineRuns.map((x) => ({ run: x, seconds: (Date.parse(x.finished_at!) - Date.parse(x.started_at)) / 1000 })).filter((x) => x.seconds >= 0)
    const slowest = timed.sort((a, b) => b.seconds - a.seconds)[0]
    return { selected, selectedClosed, hasCalendar, timers, residents, matrixRows, byRun, timelineDay, timelineRuns, timelineLabel,
      knownDue, finished, running, unknown, conclusionDay, conclusionLabel, failedRows, warnRows, okRows, pendingRows,
      ratio: observed.length ? completed / observed.length : null, failedDays, fixedDays,
      residentKnown, residentOk, residentBad, slowest }
  }, [data])

  const matrix = useMemo<EChartsOption | null>(() => {
    if (!data?.enabled || !model) return null
    const cells = model.matrixRows.flatMap((row, y) => row.cells.map((cell, x) => ({
      value: [x, y, STATE_ORDER.indexOf(cell.state)], state: cell.state, runId: cell.run_id,
      service: row.name, day: cell.day, detail: cell.detail,
      itemStyle: { color: color(p, cell.state), borderColor: p.panel, borderWidth: 2, borderRadius: 2 },
    })))
    return {
      animation: false,
      grid: { left: 150, right: 10, top: 4, bottom: 24 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const item = (raw as { data: typeof cells[number] }).data
        const extra = item.detail ? `<br/><span style="opacity:.75">${clip(item.detail, 80)}</span>` : ''
        const hint = item.runId ? '<br/><span style="opacity:.7">点击打开这次运行</span>' : ''
        return `<b>${item.service}</b>　${item.day}<br/>${STATE_LABEL[item.state]}${extra}${hint}`
      } },
      xAxis: { type: 'category', data: data.days.map(md), axisLabel: { color: p.muted, interval: 2 }, axisTick: { show: false }, axisLine: { lineStyle: { color: p.border } }, splitArea: { show: false } },
      yAxis: { type: 'category', inverse: true, data: model.matrixRows.map((x) => x.name), axisLabel: { color: p.text, fontSize: 12, width: 138, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'heatmap', data: cells, emphasis: { itemStyle: { borderColor: p.head, borderWidth: 1 } } }],
    }
  }, [data, model, p])

  const timeline = useMemo<EChartsOption | null>(() => {
    if (!model) return null
    const complete = model.timelineRuns
    if (!complete.length) return null
    const minStart = Math.min(...complete.map((x) => Date.parse(x.started_at)))
    const maxEnd = Math.max(...complete.map((x) => Date.parse(x.finished_at!)))
    const padding = Math.max(60_000, (maxEnd - minStart) * 0.08)
    const renderItem: CustomSeriesRenderItem = (_params, api) => {
      const row = Number(api.value(0))
      const start = api.coord([api.value(1), row])
      const end = api.coord([api.value(2), row])
      const height = Math.min(18, api.getHeight() / complete.length * 0.5)
      return {
        type: 'rect',
        shape: { x: start[0], y: start[1] - height / 2, width: Math.max(3, end[0] - start[0]), height, r: 2 },
        style: { fill: color(p, runState(complete[_params.dataIndex].state)) },
      }
    }
    return {
      animation: false,
      grid: { left: 150, right: 18, top: 8, bottom: 36 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const item = (raw as { data: { run: ServiceRunItem } }).data.run
        return `<b>${item.service_id}</b><br/>${dateTime(item.started_at)} – ${dateTime(item.finished_at!)}`
      } },
      xAxis: { type: 'time', min: minStart - padding, max: maxEnd + padding, axisLabel: { color: p.muted, formatter: axisHm }, axisLine: { lineStyle: { color: p.border } }, splitLine: { lineStyle: { color: p.grid } } },
      yAxis: { type: 'category', inverse: true, data: complete.map((x) => `${x.service_id} ${x.started_at.slice(11, 16)}`), axisLabel: { color: p.text, width: 138, overflow: 'truncate' }, axisLine: { lineStyle: { color: p.border } } },
      series: [{
        type: 'custom', renderItem, encode: { x: [1, 2], y: 0 },
        data: complete.map((x, index) => ({ value: [index, Date.parse(x.started_at), Date.parse(x.finished_at!)], run: x, itemStyle: { color: color(p, runState(x.state)) } })),
      }],
    }
  }, [model, p])

  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled || !model) return <Result status="info" title="服务运行记录未配置" />

  const openChartRun = (raw: unknown) => {
    const item = (raw as { data?: { runId?: string | null; run?: ServiceRunItem } }).data
    const picked = item?.run ?? (item?.runId ? model.byRun.get(item.runId) : undefined)
    if (picked) setRun(picked)
  }

  const m = model
  const heroState = m.failedRows.length ? 'bad' : m.warnRows.length ? 'warn' : m.okRows.length ? 'ok' : 'idle'
  const heroTitle = m.failedRows.length ? `${m.conclusionLabel} ${m.failedRows.length} 项失败`
    : m.warnRows.length ? `${m.conclusionLabel} ${m.warnRows.length} 项警告`
      : m.okRows.length ? `${m.conclusionLabel} 定时任务全部正常` : `${m.conclusionLabel} 还没有可判定的任务结论`
  // 原因优先取这次运行里出问题的步骤（ServiceRun 的 note/error 常为空），取不到只列任务名，不编造
  const reason = (x: { row: ServiceRow; cell: { run_id: string | null; detail: string | null } }) => {
    const item = x.cell.run_id ? m.byRun.get(x.cell.run_id) : undefined
    const step = item?.steps.find((st) => ['failed', 'warning', 'interrupted'].includes(st.state))
    const why = step ? `${step.name}${step.detail ? `：${step.detail}` : ''}` : x.cell.detail
    return why ? `${x.row.name}（${clip(why, 40)}）` : x.row.name
  }
  const issueRows = [...m.failedRows, ...m.warnRows]
  // 只有一项问题时副行带原因；多项只列任务名保持一行，完整原因在悬停提示与运行抽屉
  const names = (list: typeof issueRows) => issueRows.length === 1 ? list.map(reason).join('、') : list.map((x) => x.row.name).join('、')
  const heroSub = [
    m.selectedClosed && m.conclusionDay !== m.selected ? `${md(m.selected)} 休市` : null,
    m.failedRows.length ? `失败：${names(m.failedRows)}` : null,
    m.warnRows.length ? `警告：${names(m.warnRows)}` : null,
    !m.failedRows.length && !m.warnRows.length && m.okRows.length
      ? `${m.okRows.length} 项已完成${m.pendingRows.length ? `，${m.pendingRows.length} 项未知或运行中` : ''}` : null,
  ].filter(Boolean).join(' · ') || '历史失败保留，后续成功显示已修复；网页不提供补跑'
  const heroInfo = [...issueRows.map(reason), '历史失败保留，后续成功显示已修复；网页不提供补跑。'].join('\n')

  const todayKpi = m.selectedClosed
    ? { k: '所选日', v: '休市', sub: `休市日任务 ${m.finished} / ${m.knownDue.length}${m.timelineDay ? ` · 上一交易日 ${md(m.timelineDay)}` : ''}`, color: p.muted }
    : { k: '所选日计划', v: m.knownDue.length ? `${m.finished} / ${m.knownDue.length}` : '—',
      sub: [m.running ? `${m.running} 运行中` : null, m.unknown ? `${m.unknown} 项未知` : null].filter(Boolean).join(' · ') || undefined }
  const kpis = [
    todayKpi,
    { k: '近 30 日成功率', v: m.ratio == null ? '缺失' : pct(m.ratio), color: m.ratio == null ? p.muted : m.ratio >= 0.95 ? p.ok : p.warn,
      sub: `未修复失败 ${m.failedDays} 次 · 已补跑 ${m.fixedDays} 次` },
    { k: '常驻进程', v: `${m.residentOk} / ${m.residents.length}`, color: m.residentBad ? p.bad : m.residentOk === m.residents.length ? p.ok : p.warn,
      sub: m.residentOk === m.residents.length ? '全部正常' : `${m.residents.length - m.residentKnown.length} 项无可信观测${m.residentBad ? ` · ${m.residentBad} 项异常` : ''}` },
    { k: '最慢任务', v: m.slowest ? duration(m.slowest.seconds) : '缺失',
      sub: m.slowest ? `${m.slowest.run.service_id} · ${md(m.timelineDay!)}` : undefined },
  ]

  const rows = [...m.timers, ...m.residents]
  // 常驻/守护进程不按次运行，耗时列不适用：显示“—”而不是告警色的“缺失”
  const notApplicable = <span title="常驻进程不按次运行，不适用" style={{ color: p.muted }}>—</span>
  const ratioOf = (r: ServiceRow) => r.completion_ratio_30d ?? -1
  const kindNote = (r: ServiceRow) => KIND_LABEL[r.kind] ?? CADENCE_LABEL[r.cadence]
  const table = <Table size="small" rowKey="id" pagination={false} dataSource={rows} scroll={{ x: 1080 }}
    expandable={{ expandedRowRender: (row) => row.recent_runs.length ? <Table size="small" pagination={false} rowKey="run_id" dataSource={row.recent_runs}
      onRow={(item) => ({ onClick: () => setRun(item), style: { cursor: 'pointer' } })} columns={[
        { title: '运行日', dataIndex: 'session', width: 110, render: (v, item) => <Num>{v ?? item.started_at.slice(0, 10)}</Num> },
        { title: '结果', dataIndex: 'state', width: 110, render: (v) => stateTag(runState(v)) },
        { title: '开始', dataIndex: 'started_at', width: 170, render: (v) => <Num color={p.muted}>{dateTime(v)}</Num> },
        { title: '耗时', key: 'duration', width: 100, align: 'right', render: (_, item) => <Num>{item.finished_at ? duration((Date.parse(item.finished_at) - Date.parse(item.started_at)) / 1000) : '运行中'}</Num> },
        { title: '说明', key: 'detail', render: (_, item) => item.error || item.note || '—' },
        { title: '', key: 'open', width: 110, render: () => <a>打开这次运行</a> },
      ]} /> : <Missing why="近 30 日没有运行记录" /> }}
    columns={[
      { title: '服务', dataIndex: 'name', fixed: 'left', width: 220, sorter: (a, b) => a.name.localeCompare(b.name, 'zh'),
        render: (v, row) => <span><span style={{ color: p.head }}>{v}</span>{kindNote(row) && <span style={{ marginLeft: 6, fontSize: 11, color: p.muted }}>{kindNote(row)}</span>}</span> },
      { title: '计划', dataIndex: 'schedule', width: 220, render: (v) => <span style={{ color: p.muted, fontSize: 12 }}>{v}</span> },
      { title: '最近结果', dataIndex: 'latest_state', width: 170,
        filters: (['ok', 'warning', 'failed', 'fixed', 'unknown'] as OpsState[]).map((x) => ({ text: STATE_LABEL[x].slice(2), value: x })),
        onFilter: (v, row) => row.latest_state === v,
        render: (v, row) => <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>{stateTag(v)}{row.latest_at && <Num size={11} color={p.muted}>{dateTime(row.latest_at).slice(5, 16)}</Num>}</span> },
      { title: '30 日完成率', dataIndex: 'completion_ratio_30d', width: 116, align: 'right', sorter: (a, b) => ratioOf(a) - ratioOf(b),
        render: (v) => v == null ? <Missing why="没有可判定运行" /> : <Num color={v < 0.9 ? p.warn : p.text}>{pct(v)}</Num> },
      { title: '耗时趋势', width: 145, render: (_, row) => row.kind === 'timer' ? <span style={{ color: p.line }}><DurationSpark row={row} /></span> : notApplicable },
      { title: '平均耗时', dataIndex: 'average_duration_seconds', width: 100, align: 'right',
        sorter: (a, b) => (a.average_duration_seconds ?? -1) - (b.average_duration_seconds ?? -1),
        render: (v, row) => row.kind !== 'timer' ? notApplicable : v == null ? <Missing why="没有完整区间" /> : <Num>{duration(v)}</Num> },
      { title: '版本', dataIndex: 'version', width: 100, render: (v) => v ? <Tag style={{ fontFamily: 'monospace' }}>{v.slice(0, 7)}</Tag> : <Missing why={data.missing.versions ?? '没有可信版本记录'} /> },
    ]} />

  const legend = <div aria-label="矩阵状态图例" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: '4px 12px', fontSize: 11, color: p.muted, marginBottom: 4 }}>
    {LEGEND.map((state) => <span key={state} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <i style={{ display: 'inline-block', width: 12, height: 10, borderRadius: 2, background: color(p, state), outline: state === 'not_due' ? `1px solid ${p.border}` : undefined }} />{STATE_LABEL[state]}
    </span>)}
  </div>

  return <>
    <Hero s={heroState} title={heroTitle} subtitle={<span title={heroInfo}>{heroSub}</span>} kpis={kpis} />
    <Panel title={view === 'matrix' ? '近 30 天每个任务都跑成功了吗？' : m.timelineDay ? `${m.timelineDay} 的时间花在哪？` : '时间花在哪？'}
      info={view === 'matrix'
        ? '每列一天、每行一个每日任务；休市依据 warehouse 交易日历（Tushare trade_cal 直取），缺日历的日期保持未知。月度任务见服务清单。点击有运行记录的格子查看步骤与证据。'
        : '只画 ServiceRun 中具有真实开始和结束时间的区间，没有结束时间的运行不补值。所选日是交易日且已有完整运行时画当日，否则画上一交易日。'}
      extra={<Segmented size="small" value={view} onChange={(v) => setView(v as View)} options={[{ label: '30 日矩阵', value: 'matrix' }, { label: m.timelineLabel, value: 'timeline' }]} />}>
      {view === 'matrix' ? <div data-testid="service-matrix" style={{ minWidth: 900 }}>
        {legend}
        <EChart option={matrix!} height={Math.max(220, m.matrixRows.length * 36 + 32)} onClick={openChartRun} />
        {data.missing.calendar && <div style={{ fontSize: 11, color: p.muted }}><Missing why={data.missing.calendar} /> 交易日历</div>}
      </div> : timeline ? <div data-testid="service-timeline" style={{ minWidth: 760 }}><EChart option={timeline} height={Math.max(220, m.timelineRuns.length * 30 + 55)} onClick={openChartRun} /></div>
        : <Missing why="所选日及此前没有具备真实开始、结束时间的 ServiceRun" />}
    </Panel>
    <Panel flush title="服务清单" info="定时任务在前、常驻/守护进程在后；列头可排序，“最近结果”可筛选。展开行查看最近 5 次运行，点击打开步骤与证据。耗时趋势为近 20 次完整运行。">
      {table}
    </Panel>
    {run && <RunDrawer run={run} runs={data.runs} onPick={setRun} onClose={() => setRun(null)} />}
  </>
}
