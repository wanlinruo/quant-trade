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
import { stateTag, type OpsState } from './common'

type View = 'matrix' | 'timeline'

const STATE_ORDER: OpsState[] = ['ok', 'warning', 'failed', 'fixed', 'running', 'not_due', 'paused', 'unknown']
const STATE_LABEL: Record<OpsState, string> = {
  ok: '正常', warning: '警告', failed: '失败', fixed: '已修复', running: '运行中',
  not_due: '未到期 / 休市', paused: '已暂停', unknown: '未知',
}

function color(p: Palette, state: OpsState) {
  return state === 'ok' ? p.ok : state === 'warning' ? p.warn : state === 'failed' ? p.bad
    : state === 'fixed' ? p.info : state === 'running' ? p.running
      : state === 'unknown' ? p.muted : p.idle
}

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
    const timers = data.services.filter((x) => x.kind === 'timer')
    const residents = data.services.filter((x) => x.kind !== 'timer')
    const byRun = new Map(data.runs.map((x) => [x.run_id, x]))
    const runDay = (x: ServiceRunItem) => x.session ?? x.started_at.slice(0, 10)
    const selectedRuns = data.runs.filter((x) => runDay(x) === data.selected_day)
    // 所选日没有完整运行（如周末）时，回退到此前最近一个有真实起止时间的运行日；只用 ServiceRun 事实，不推算交易日。
    const completeRuns = data.runs.filter((x) => x.finished_at)
    const timelineDay = completeRuns.map(runDay).filter((d) => d <= data.selected_day).sort().at(-1) ?? null
    const timelineRuns = timelineDay == null ? [] : completeRuns.filter((x) => runDay(x) === timelineDay)
    const selectedCells = timers.map((row) => row.cells.find((x) => x.day === data.selected_day)).filter((x) => x !== undefined)
    const knownDue = selectedCells.filter((x) => x.state !== 'not_due' && x.state !== 'unknown')
    const finished = knownDue.filter((x) => !['running', 'paused'].includes(x.state)).length
    const failed = selectedCells.filter((x) => x.state === 'failed').length
    const warning = selectedCells.filter((x) => x.state === 'warning').length
    const unknown = selectedCells.filter((x) => x.state === 'unknown').length
    const completedStates = new Set<OpsState>(['ok', 'warning', 'fixed'])
    const observed = timers.flatMap((x) => x.cells).filter((x) => !['not_due', 'unknown'].includes(x.state))
    const completed = observed.filter((x) => completedStates.has(x.state)).length
    const residentKnown = residents.filter((x) => !['unknown', 'not_due'].includes(x.latest_state))
    const residentOk = residentKnown.filter((x) => ['ok', 'fixed'].includes(x.latest_state)).length
    const timed = timelineRuns.map((x) => ({ run: x, seconds: (Date.parse(x.finished_at!) - Date.parse(x.started_at)) / 1000 })).filter((x) => x.seconds >= 0)
    const slowest = timed.sort((a, b) => b.seconds - a.seconds)[0]
    return { timers, residents, byRun, selectedRuns, timelineDay, timelineRuns, knownDue, finished, failed, warning, unknown,
      ratio: observed.length ? completed / observed.length : null, residentKnown, residentOk, slowest }
  }, [data])

  const matrix = useMemo<EChartsOption | null>(() => {
    if (!data?.enabled || !model) return null
    const cells = model.timers.flatMap((row, y) => row.cells.map((cell, x) => ({
      value: [x, y, STATE_ORDER.indexOf(cell.state)], state: cell.state, runId: cell.run_id,
      service: row.name, day: cell.day,
      itemStyle: { color: color(p, cell.state), borderColor: p.panel, borderWidth: 2 },
    })))
    return {
      animation: false,
      grid: { left: 150, right: 12, top: 8, bottom: 42 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const item = (raw as { data: typeof cells[number] }).data
        return `<b>${item.service}</b><br/>${item.day} · ${STATE_LABEL[item.state]}`
      } },
      xAxis: { type: 'category', data: data.days.map((x) => x.slice(5)), axisLabel: { color: p.muted, interval: 2, rotate: 35 }, axisLine: { lineStyle: { color: p.border } }, splitArea: { show: false } },
      yAxis: { type: 'category', inverse: true, data: model.timers.map((x) => x.name), axisLabel: { color: p.text, width: 138, overflow: 'truncate' }, axisLine: { lineStyle: { color: p.border } } },
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
  const table = (rows: ServiceRow[]) => <Table size="small" rowKey="id" pagination={false} dataSource={rows} scroll={{ x: 1080 }}
    expandable={{ expandedRowRender: (row) => row.recent_runs.length ? <Table size="small" pagination={false} rowKey="run_id" dataSource={row.recent_runs}
      onRow={(item) => ({ onClick: () => setRun(item), style: { cursor: 'pointer' } })} columns={[
        { title: '运行日', dataIndex: 'session', render: (v, item) => v ?? item.started_at.slice(0, 10) },
        { title: '结果', dataIndex: 'state' }, { title: '开始', dataIndex: 'started_at', render: dateTime },
        { title: '耗时', key: 'duration', render: (_, item) => item.finished_at ? duration((Date.parse(item.finished_at) - Date.parse(item.started_at)) / 1000) : '运行中' },
        { title: '说明', key: 'detail', render: (_, item) => item.error || item.note || '—' },
      ]} /> : <Missing why="近 30 日没有运行记录" /> }}
    columns={[
      { title: '服务', dataIndex: 'name', fixed: 'left', width: 180 },
      { title: '计划', dataIndex: 'schedule', width: 210 },
      { title: '最近结果', dataIndex: 'latest_state', width: 150, render: (v, row) => <span>{stateTag(v)} <small>{row.latest_at ? dateTime(row.latest_at) : ''}</small></span> },
      { title: '30 日完成率', dataIndex: 'completion_ratio_30d', width: 120, render: (v) => v == null ? <Missing why="没有可判定运行" /> : pct(v) },
      { title: '耗时趋势', width: 145, render: (_, row) => <DurationSpark row={row} /> },
      { title: '平均耗时', dataIndex: 'average_duration_seconds', width: 100, render: (v) => duration(v) ?? <Missing why="没有完整区间" /> },
      { title: '版本', dataIndex: 'version', width: 100, render: (v) => v ? <Tag style={{ fontFamily: 'monospace' }}>{v.slice(0, 7)}</Tag> : <Missing why={data.missing.versions ?? '没有可信版本记录'} /> },
    ]} />

  const heroState = model.failed ? 'bad' : model.warning || model.unknown ? 'warn' : 'ok'
  const heroTitle = model.failed ? `${data.selected_day} 有 ${model.failed} 个任务失败`
    : model.warning ? `${data.selected_day} 有 ${model.warning} 个任务警告`
      : model.knownDue.length ? `${data.selected_day} 已记录任务运行结论` : `${data.selected_day} 没有可判定的到期任务`
  return <>
    <Hero s={heroState} title={heroTitle}
      subtitle={`历史失败保留；后续成功显示已修复。${model.unknown ? ` ${model.unknown} 个任务的到期性或结果未知。` : ''} 网页不提供补跑。`}
      kpis={[
        { k: '当日计划进度', v: model.knownDue.length ? `${model.finished} / ${model.knownDue.length}` : '无可判定项', sub: model.unknown ? `${model.unknown} 项未知` : undefined },
        { k: '30 日成功率', v: model.ratio == null ? '缺失' : pct(model.ratio), sub: '只统计有运行事实的任务日' },
        { k: '常驻进程', v: `${model.residentOk} / ${model.residents.length}`, sub: `${model.residentKnown.length} 项有可信观测` },
        { k: '最慢任务', v: model.slowest ? duration(model.slowest.seconds) : '缺失', sub: model.slowest ? `${model.slowest.run.service_id} · ${model.timelineDay}` : undefined },
      ]} />
    <Panel title={view === 'matrix' ? '近 30 天每个定时任务都跑成功了吗？' : model.timelineDay ? `${model.timelineDay} 的任务时间线` : '最近任务时间线'}
      info={view === 'matrix' ? '每列一天、每行一个定时任务；未到期/休市只在有明确证据时标记，缺证据保持未知。点击有运行记录的格子查看步骤。' : '只画 ServiceRun 中具有真实开始和结束时间的区间；没有结束时间的运行不补值。所选日没有完整运行时（如周末），显示此前最近一个有完整运行的日期。'}
      extra={<Segmented size="small" value={view} onChange={(v) => setView(v as View)} options={[{ label: '30 日矩阵', value: 'matrix' }, { label: '任务时间线', value: 'timeline' }]} />}>
      {view === 'matrix' ? <div data-testid="service-matrix" style={{ minWidth: 900 }}>
        <EChart option={matrix!} height={Math.max(310, model.timers.length * 30 + 70)} onClick={openChartRun} />
        <div aria-label="矩阵状态图例" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, fontSize: 11, color: p.muted }}>
          {STATE_ORDER.map((state) => <span key={state}><i style={{ display: 'inline-block', width: 10, height: 10, marginRight: 4, background: color(p, state) }} />{STATE_LABEL[state]}</span>)}
        </div>
      </div> : timeline ? <div data-testid="service-timeline" style={{ minWidth: 760 }}><EChart option={timeline} height={Math.max(220, model.timelineRuns.length * 30 + 55)} onClick={openChartRun} /></div>
        : <Missing why="所选日及此前没有具备真实开始、结束时间的 ServiceRun" />}
    </Panel>
    <Panel title="服务清单" info="定时任务与常驻/守护进程分层展示；展开行查看最近 5 次运行。耗时趋势取近 20 次完整运行。">
      <Typography.Title level={5}>定时任务</Typography.Title>
      {table(model.timers)}
      <Typography.Title level={5} style={{ marginTop: 20 }}>常驻进程与守护任务</Typography.Title>
      {table(model.residents)}
    </Panel>
    {run && <RunDrawer run={run} runs={data.runs} onPick={setRun} onClose={() => setRun(null)} />}
  </>
}
