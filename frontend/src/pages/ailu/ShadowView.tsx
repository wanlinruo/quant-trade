import { useCallback, useMemo } from 'react'
import { Button, Space } from 'antd'
import { fetchShadow, type ShadowBoard as View, type ViewState } from '../../api/ailu'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Freshness } from '../../page/Freshness'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { clipFuture, toSeries } from '../../page/series'
import { usePolling } from '../../page/usePolling'
import { Value } from '../../page/Value'
import { pct } from '../../page/format'
import { usePalette } from '../../theme/context'
import type { Status } from '../../theme/status'
import { Missing } from '../../ui/Missing'
import { EntryTable } from './EntryTable'

const STATE: Record<ViewState, { s: Status; text?: string }> = {
  ready: { s: 'ok' }, stale: { s: 'warn', text: '已过期' }, generating: { s: 'running' },
  waiting: { s: 'idle' }, failed: { s: 'bad' }, unavailable: { s: 'idle' },
}

const closeOf = (x: string) => new Date(`${x}T15:00:00+08:00`)

function PrecisionChart({ view }: { view: View }) {
  const p = usePalette()
  const option = useMemo<EChartsOption>(() => {
    const rows = [...view.history].sort((a, b) => a.target_session.localeCompare(b.target_session))
    const pts = clipFuture(toSeries(rows, (r) => r.prediction_date, (r) => r.precision_at_20), new Date(),
      (x) => closeOf(rows.find((r) => r.prediction_date === x)!.target_session))
    return {
      grid: { left: 44, right: 16, top: 16, bottom: 24 },
      xAxis: { type: 'category', data: pts.map(([x]) => x), axisLabel: { color: p.muted } },
      yAxis: { type: 'value', min: 0, max: 1, splitLine: { lineStyle: { color: p.grid } }, axisLabel: { formatter: (v: number) => `${Math.round(v * 100)}%` } },
      series: [{ name: 'P@20', type: 'line', data: pts, connectNulls: false, showSymbol: true, symbolSize: 4, lineStyle: { color: p.line } }],
    }
  }, [view.history, p])
  return <EChart option={option} height={200} />
}

export function ShadowView({ day, onPickDay }: { day: string | null; onPickDay(d: string | null): void }) {
  const p = usePalette()
  const fetcher = useCallback((signal: AbortSignal) => fetchShadow(day, signal), [day])
  const poll = usePolling(fetcher, { intervalMs: 60_000 })
  const v = poll.data
  const fresh = <Freshness at={poll.fetchedAt} paused={poll.paused} onTogglePause={() => poll.setPaused(!poll.paused)} onRefresh={poll.refresh} error={poll.error} />
  const head = (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 8 }}>
      {day && <Space size={8}><span style={{ color: p.warn }}>▲ 正在查看 {day}</span><Button size="small" onClick={() => onPickDay(null)}>回到最新</Button></Space>}
      <span style={{ flex: 1 }} />{fresh}
    </div>
  )
  if (!v) return <>{head}{poll.error ? <Hero s="bad" title="影子榜单接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />}</>

  const st = STATE[v.state]
  const snap = v.snapshot
  const showTable = !!snap && (v.state === 'ready' || v.state === 'stale') && snap.entries.length > 0
  const pending = snap ? new Date() < closeOf(snap.target_session) : false
  return (
    <>
      {head}
      {showTable
        ? <Hero s={st.s} statusText={st.text} title={`预测 ${snap!.prediction_date} → 目标 ${snap!.target_session}`} subtitle={v.message}
            kpis={[
              { k: '模型', v: snap!.model_version },
              { k: '可选股票', v: snap!.eligible_count },
              { k: '滚动 P@20', v: <Value v={pct(snap!.rolling_precision_at_20)} why="该快照没有滚动 P@20" /> },
              { k: '校准误差', v: <Value v={snap!.calibration_error?.toFixed(3) ?? null} why="该快照没有校准误差" /> },
              { k: '漂移分数', v: <Value v={snap!.drift_score?.toFixed(3) ?? null} why="该快照没有漂移分数" /> },
            ]} />
        : <Hero s={st.s} statusText={st.text} title={v.message} />}
      {showTable && (
        <Panel title="今天模型看好谁？" info="三列概率均为模型输出：收盘封板 = 触板概率 × 触板后封板。实际结果在目标交易日收盘后回填。">
          <EntryTable entries={snap!.entries} resultTitle={pending ? '实际结果（盘中快照 / 未出结果）' : '实际结果'} />
        </Panel>
      )}
      <Panel title="模型最近准不准？" info="P@20：榜单前 20 只中收盘封板的比例，逐日来自影子榜单记录；未出结果的日子断开。">
        {v.history.length === 0 ? <div style={{ color: p.muted }}>暂无历史</div> : <PrecisionChart view={v} />}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px', marginTop: 8, fontSize: 12, color: p.muted }}>
          <span>基线 <Missing why={v.missing.baseline ?? '后端未给出原因'} /></span>
          <span>10 日滚动 <Missing why={v.missing.rolling_10d ?? '后端未给出原因'} /></span>
          <span>校准曲线 <Missing why={v.missing.calibration_curve ?? '后端未给出原因'} /></span>
          <span>特征漂移序列 <Missing why={v.missing.drift_series ?? '后端未给出原因'} /></span>
        </div>
      </Panel>
      <Panel title="历史榜单">
        {v.history.length === 0 ? <div style={{ color: p.muted }}>暂无历史</div> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {v.history.map((h) => (
              <div key={h.prediction_date} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', fontSize: 12 }}>
                <Button size="small" type={day === h.prediction_date ? 'primary' : 'default'} onClick={() => onPickDay(h.prediction_date)}>{h.prediction_date}</Button>
                <span style={{ color: p.muted }}>→ {h.target_session} · {h.status} · {h.entry_count} 只 · P@20 </span>
                <Value v={pct(h.precision_at_20)} why="目标交易日结果未回填" />
              </div>
            ))}
          </div>
        )}
      </Panel>
    </>
  )
}
