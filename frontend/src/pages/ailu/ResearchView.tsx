import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button } from 'antd'
import { fetchResearch, type ResearchView as View } from '../../api/ailu'
import { EChart, type EChartsOption } from '../../page/EChart'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { pct } from '../../page/format'
import { registerPageRefresh } from '../../shell/refresh'
import { usePalette } from '../../theme/context'
import { EntryTable } from './EntryTable'

/** 研究回测是冻结数据，不轮询；R 键或切换日期时重新读取。 */
function useResearch(day: string | null) {
  const [state, setState] = useState<{ data: View | null; error: string | null }>({ data: null, error: null })
  const ctl = useRef<AbortController | null>(null)
  const load = useCallback(() => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    fetchResearch(day, c.signal).then((data) => setState({ data, error: null }),
      (e: Error) => { if (e.name !== 'AbortError') setState((s) => ({ data: s.data, error: e.message })) })
  }, [day])
  useEffect(() => { load(); return () => ctl.current?.abort() }, [load])
  useEffect(() => registerPageRefresh(load), [load])
  return state
}

export function ResearchView({ day, onPickDay }: { day: string | null; onPickDay(d: string | null): void }) {
  const p = usePalette()
  const { data: v, error } = useResearch(day)
  const option = useMemo<EChartsOption | null>(() => {
    if (!v) return null
    const months = [...new Set(v.monthly.map((m) => m.month.slice(0, 7)))].sort()
    const series = (['TEST', 'OBS'] as const).map((split) => ({
      name: split, type: 'bar' as const,
      data: v.monthly.filter((m) => m.split === split).map((m) => [m.month.slice(0, 7), m.hits / m.predictions] as [string, number]),
      itemStyle: { color: split === 'OBS' ? p.line : p.muted },
    }))
    return {
      grid: { left: 44, right: 16, top: 28, bottom: 24 }, legend: { top: 0, textStyle: { color: p.muted } },
      xAxis: { type: 'category', data: months, axisLabel: { color: p.muted } },
      yAxis: { type: 'value', min: 0, splitLine: { lineStyle: { color: p.grid } }, axisLabel: { formatter: (x: number) => `${Math.round(x * 100)}%` } },
      series,
    }
  }, [v, p])

  if (!v) return error ? <Hero s="bad" title="研究回测接口无法访问" subtitle={error} /> : <Hero s="running" title="加载中" />
  const s = v.summary
  return (
    <>
      {error && <div style={{ color: p.warn, fontSize: 12, marginBottom: 8 }}>▲ 刷新失败，显示的是上一次结果</div>}
      {s
        ? <Hero s={v.state === 'ready' ? 'ok' : 'warn'} title={`样本 ${s.first_prediction_date} ~ ${s.last_prediction_date}`} subtitle={v.message}
            kpis={[
              { k: 'TEST / OBS 会话', v: `${s.test_sessions} / ${s.obs_sessions}` },
              { k: 'OBS P@20', v: pct(s.obs_precision_at_20) },
              { k: 'OBS 基线 P@20', v: pct(s.obs_baseline_precision_at_20) },
              { k: 'OBS 月胜率', v: pct(s.obs_month_win_rate) },
              { k: '已晋级影子', v: s.promoted_to_shadow ? '是' : '否' },
            ]} />
        : <Hero s="idle" title={v.message} />}
      {v.selected && (
        <Panel title={`${v.selected.prediction_date} → ${v.selected.target_session} 的榜单（${v.selected.split}）`}
          extra={<Button size="small" onClick={() => onPickDay(null)}>关闭</Button>}>
          <EntryTable entries={v.selected.entries} resultTitle="实际结果" />
        </Panel>
      )}
      {option && v.monthly.length > 0 && (
        <Panel title="月度稳定吗？" info="每月命中数 ÷ 预测数（即月度 P@20），按 TEST / OBS 分色；来自冻结的研究回测结果。">
          <EChart option={option} height={220} />
        </Panel>
      )}
      <Panel title="逐日结果">
        {v.history.length === 0 ? <div style={{ color: p.muted }}>暂无逐日结果</div> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxHeight: 320, overflow: 'auto' }}>
            {v.history.map((h) => (
              <div key={h.prediction_date} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', fontSize: 12 }}>
                <Button size="small" type={day === h.prediction_date ? 'primary' : 'default'} onClick={() => onPickDay(h.prediction_date)}>{h.prediction_date}</Button>
                <span style={{ color: p.muted }}>{h.split} · P@20 {pct(h.precision_at_20)} · 触板 P@20 {pct(h.touch_precision_at_20)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>
      {s && <div style={{ fontSize: 11, color: p.muted, overflowWrap: 'anywhere' }}>收据：source_sha {s.source_sha} · data_sha {s.data_sha} · 生成于 {s.generated_at}</div>}
    </>
  )
}
