import { Result, Table } from 'antd'
import type { CustomSeriesRenderItem } from 'echarts'
import { useMemo } from 'react'
import { fetchToday } from '../../api/today'
import type { TodayEvent, TodayPhase } from '../../api/contracts.gen'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { useFetch } from '../../page/useFetch'
import { axisHm, dateTime } from '../../shell/time'
import { navigate } from '../../shell/router'
import { usePalette } from '../../theme/context'
import type { Palette } from '../../theme/palette'
import { Missing } from '../../ui/Missing'
import { Num } from '../../ui/Num'
import { Spark } from '../../ui/Spark'
import { StatusTag } from '../../ui/StatusTag'
import { dayNote, label, useTradingDay, yuan } from '../trading/common'

type PhaseState = TodayPhase['state']
const PHASE_STATUS: Record<PhaseState, 'ok' | 'warn' | 'bad' | 'running' | 'idle'> = {
  ok: 'ok', warning: 'warn', failed: 'bad', running: 'running', not_started: 'idle', unknown: 'idle',
}
const PHASE_TEXT: Record<PhaseState, string> = { ok: '完成', warning: '警告', failed: '失败', running: '进行中', not_started: '未到', unknown: '未知' }
const phaseColor = (p: Palette, s: PhaseState) => s === 'ok' ? p.ok : s === 'warning' ? p.warn : s === 'failed' ? p.bad : s === 'running' ? p.info : p.idle
const hhmm = (iso: string | null) => iso ? dateTime(iso).slice(11, 16) : '—'

/** 阶段条：各段连成一条；进度 = 已完成 / 已记录（实际计数，不按时间推算）。 */
function PhaseStrip({ phases }: { phases: TodayPhase[] }) {
  const p = usePalette()
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', marginBottom: 12 }}>
    {phases.map((x, i) => {
      const ratio = x.recorded ? x.completed / x.recorded : 0
      return <section key={x.id} data-testid="phase" style={{ background: p.panel, border: `1px solid ${p.border}`, borderLeftWidth: i ? 0 : 1, padding: '7px 12px',
        boxShadow: x.state === 'running' ? `inset 0 -2px 0 ${p.info}` : undefined }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
          <span style={{ color: p.head, fontWeight: 600 }}>{x.label}</span>
          <StatusTag s={PHASE_STATUS[x.state]} text={PHASE_TEXT[x.state]} />
        </div>
        <Num color={p.muted} size={11}>{x.time_label} · {x.completed}/{x.recorded} 记录完成</Num>
        <div role="progressbar" aria-label={`${x.label} 完成 ${x.completed}/${x.recorded}`} aria-valuenow={Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100}
          style={{ height: 3, background: p.idle, borderRadius: 2, marginTop: 5 }}>
          <div style={{ height: 3, borderRadius: 2, background: phaseColor(p, x.state), width: `${Math.round(ratio * 100)}%` }} />
        </div>
      </section>
    })}
  </div>
}

function EventList({ events }: { events: TodayEvent[] }) {
  const p = usePalette()
  if (!events.length) return <Missing why="当日没有策略事件记录" />
  return <Table size="small" pagination={false} rowKey="event_key" showHeader={false} dataSource={events}
    onRow={() => ({ onClick: () => navigate('runs'), style: { cursor: 'pointer' } })} columns={[
      { dataIndex: 'scheduled_at', width: 56, render: (v, x) => <Num color={p.muted}>{hhmm(v ?? x.started_at)}</Num> },
      { dataIndex: 'phase', render: (v, x) => <div><div style={{ color: p.head }}>{label(v)}</div>
        {(x.detail || x.summary) && <div style={{ color: p.muted, fontSize: 11 }}>{x.detail || x.summary}</div>}</div> },
      { dataIndex: 'state', width: 72, align: 'right', render: (v) => <StatusTag s={v === 'completed' ? 'ok' : 'bad'} text={v === 'completed' ? '完成' : '失败'} /> },
    ]} />
}

export function TodayPage() {
  const p = usePalette(); const day = useTradingDay(); const { data, error } = useFetch((s) => fetchToday(day, s), [day])

  /** 链路状态时间轴：每次实际采样画一道细刻度（按新鲜着色）；连续采样自然连成条，没有采样就留空，不推断在线。 */
  const linkTimeline = useMemo<EChartsOption | null>(() => {
    if (!data?.enabled) return null
    const links = data.links.filter((x) => x.points.length)
    if (!links.length) return null
    const points = links.flatMap((link, row) => link.points.map((pt) => ({
      value: [row, Date.parse(pt.sampled_at)], pt, link: link.source,
      itemStyle: { color: pt.fresh == null ? p.muted : pt.fresh ? p.ok : p.bad },
    })))
    const renderItem: CustomSeriesRenderItem = (_params, api) => {
      const c = api.coord([api.value(1), api.value(0)])
      const h = (api.size!([0, 1]) as number[])[1] * 0.6
      return { type: 'rect', shape: { x: c[0] - 1, y: c[1] - h / 2, width: 2, height: h }, style: api.style() }
    }
    return {
      animation: false,
      grid: { left: 150, right: 12, top: 6, bottom: 24 },
      tooltip: { trigger: 'item', formatter: (raw: unknown) => {
        const x = (raw as { data: typeof points[number] }).data
        return `<b>${x.link}</b><br/>${dateTime(x.pt.sampled_at)} · ${x.pt.fresh == null ? '未知' : x.pt.fresh ? '新鲜' : '过期'}${x.pt.age_seconds == null ? '' : `<br/>年龄 ${x.pt.age_seconds.toFixed(1)}s / 阈值 ${x.pt.threshold_seconds?.toFixed(1) ?? '—'}s`}`
      } },
      xAxis: { type: 'time', axisLabel: { color: p.muted, formatter: axisHm }, axisLine: { lineStyle: { color: p.border } }, splitLine: { show: false } },
      yAxis: { type: 'category', inverse: true, data: links.map((l) => {
        const last = l.points.at(-1)!
        return `${l.source} · ${last.fresh == null ? '未知' : last.fresh ? '新鲜' : '过期'}`
      }), axisLabel: { color: p.text, width: 140, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
      series: [{ type: 'custom', renderItem, encode: { x: 1, y: 0 }, data: points }],
    }
  }, [data, p])

  if (!data) return <Hero s={error ? 'bad' : 'running'} title={error ?? '加载中'} />
  if (!data.enabled) return <Result status="info" title="今日运行数据源未配置" />
  const d = data; const hero = d.state === 'running' ? 'running' : d.state === 'reduce_only' ? 'bad' : d.state === 'unknown' ? 'warn' : 'idle'
  const s = d.sentiment
  const prev = s.length > 1 ? s[s.length - 2] : null
  const last = s.at(-1) ?? null
  const ageSeries = d.links.flatMap((l) => l.points).sort((a, b) => a.sampled_at.localeCompare(b.sampled_at)).map((x) => x.age_seconds)
  const concerns = d.concerns.length ? <span data-testid="hero-concerns" style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '2px 14px', marginTop: 2 }}>
    {d.concerns.map((x) => <a key={x.id} onClick={() => navigate(x.page)} style={{ color: x.severity === 'critical' ? p.bad : x.severity === 'warning' ? p.warn : p.muted, cursor: 'pointer' }}>
      {x.severity === 'critical' ? '✕' : x.severity === 'warning' ? '▲' : '○'} {x.label}{x.detail ? ` · ${x.detail}` : ''}</a>)}
  </span> : null
  const sentimentRows = [
    { k: '涨停家数', v: last?.limit_up, prev: prev?.limit_up, series: s.map((x) => x.limit_up), color: p.up, fmt: (v: number) => String(v) },
    { k: '跌停家数', v: last?.limit_down, prev: prev?.limit_down, series: s.map((x) => x.limit_down), color: p.down, fmt: (v: number) => String(v) },
    { k: '炸板率', v: last?.broken_board_ratio, prev: prev?.broken_board_ratio, series: s.map((x) => x.broken_board_ratio), color: p.warn, fmt: (v: number) => pct(v) ?? '—' },
    { k: '昨涨停今日', v: last?.prior_limitup_return_ratio, prev: prev?.prior_limitup_return_ratio, series: s.map((x) => x.prior_limitup_return_ratio), color: p.info, fmt: (v: number) => pct(v) ?? '—' },
  ]

  return <>
    <Hero s={hero} title={`${d.title}${d.concerns.length ? ` · ${d.concerns.length} 项需要留意` : ''}`}
      subtitle={<>{dayNote({ selected: d.day, available: d.days })} · 页面只组合已有事实，不提供交易或运维控制。{concerns && <br />}{concerns}</>}
      kpis={[{ k: '账户总资产', v: d.kpis.account_total == null ? <Missing why={d.missing.live ?? '未记录'} /> : yuan(d.kpis.account_total), sub: '原始观测', onClick: () => navigate('live') },
        { k: d.kpis.index_name ?? '上证指数', v: d.kpis.index_price ?? <Missing why={d.missing.market ?? '未记录'} />, sub: d.kpis.index_change_ratio == null ? undefined : pct(d.kpis.index_change_ratio), onClick: () => navigate('market') },
        { k: '涨停 / 跌停', v: d.kpis.limit_up == null || d.kpis.limit_down == null ? <Missing why="市场快照缺失" /> : `${d.kpis.limit_up} / ${d.kpis.limit_down}`, sub: d.kpis.seal_ratio == null ? undefined : `封板率 ${pct(d.kpis.seal_ratio)}`, onClick: () => navigate('board'),
          spark: s.length > 1 ? s.map((x) => x.limit_up) : undefined, sparkColor: p.up },
        { k: '链路心跳', v: d.kpis.link_max_age_seconds == null ? <Missing why={d.missing.links ?? '未采样'} /> : `${d.kpis.link_max_age_seconds.toFixed(1)}s`, sub: d.kpis.link_total == null ? undefined : `${d.kpis.link_fresh}/${d.kpis.link_total} 新鲜`, onClick: () => navigate('nodes'),
          spark: ageSeries.length > 1 ? ageSeries : undefined, sparkColor: p.ok }]} />
    <PhaseStrip phases={d.phases} />
    <div className="qs-grid-21">
      <Panel title="大盘今天怎么走的？" info="上证 1 分钟分时；缺失时不从当前价生成曲线。"><Missing why={d.missing.index_intraday ?? '分时未采集'} /> <span style={{ color: p.muted }}>{d.missing.index_intraday}</span></Panel>
      <Panel title="策略事件按时完成了吗？" info="当日策略事件；点击进入运行记录查看同日证据。"
        extra={<span style={{ color: p.muted, fontSize: 12 }}>{d.events.filter((x) => x.state === 'completed').length} / {d.events.length} 完成</span>}>
        <EventList events={d.events} />
      </Panel>
    </div>
    <div className="qs-grid-21">
      <Panel title="交易链路一直在线吗？" info="每次实际采样画一道刻度：绿 = 新鲜，红 = 过期，灰 = 未知；没有采样的时段留空，不推断在线。来源：health-observer。">
        {linkTimeline ? <div data-testid="link-timeline" style={{ minWidth: 520 }}><EChart option={linkTimeline} height={Math.max(140, d.links.length * 32 + 40)} /></div>
          : <Missing why={d.missing.links ?? '当日没有链路采样'} />}
      </Panel>
      <Panel title="短线情绪热不热？" info="近 20 个交易日的直取 / 既有纯派生字段；不合成未定义的“情绪温度”。" extra={<span style={{ color: p.muted, fontSize: 12 }}>近 {s.length} 日</span>}>
        {s.length ? <div style={{ display: 'grid', gap: 10 }}>
          {sentimentRows.map((r) => <div key={r.k} data-testid="sentiment-row" style={{ display: 'grid', gridTemplateColumns: '78px 72px minmax(0, 1fr)', alignItems: 'center', gap: 8 }}>
            <span style={{ color: p.muted, fontSize: 12 }}>{r.k}</span>
            <div><Num size={15} weight={600} color={p.head}>{r.v == null ? '—' : r.fmt(r.v)}</Num>
              <div style={{ color: p.muted, fontSize: 11 }}>{r.prev == null ? '昨 —' : `昨 ${r.fmt(r.prev)}`}</div></div>
            <Spark data={r.series} color={r.color} height={30} label={`${r.k}近 ${s.length} 日`} />
          </div>)}
        </div> : <Missing why="市场情绪历史不可读" />}
      </Panel>
    </div>
  </>
}
