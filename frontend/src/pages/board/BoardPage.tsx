import { useCallback, useEffect, useRef, useState } from 'react'
import { Segmented } from 'antd'
import { fetchBoard, type BoardData } from '../../api/board'
import { Freshness } from '../../page/Freshness'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { pct } from '../../page/format'
import { hms, parseLocal } from '../../shell/time'
import { setSearch, useSearch } from '../../shell/router'
import type { Status } from '../../theme/status'
import { DayControls } from './DayControls'
import { LadderMatrix } from './LadderMatrix'
import { SentimentBar } from './SentimentBar'
import { StockDrawer } from './StockDrawer'

export const STALE_SECONDS = 45
const DAY = /^\d{4}-\d{2}-\d{2}$/
const TIME = /^\d{2}:\d{2}:\d{2}$/

export function heroOf(d: BoardData, now: Date): { s: Status; statusText?: string; title: string; subtitle: string } {
  if (!d.available) return { s: 'idle', title: d.message, subtitle: d.day ? `交易日 ${d.day}` : '' }
  const s = d.sentiment
  const title = s
    ? `涨停 ${s.sealed} · 跌停 ${s.limit_down} · 最高 ${s.max_height ?? '未知'} 板 · 封板率 ${pct(s.seal_rate) ?? '未知'}`
    : d.message
  const at = parseLocal(d.at ?? null)
  const reasons: string[] = []
  const meta = (d.meta ?? {}) as NonNullable<BoardData['meta']> & Record<string, string | null>
  if (meta.concept_error) reasons.push(`题材维度不可用：${meta.concept_error}`)
  if (meta.heights_error) reasons.push(`昨日连板高度不可用：${meta.heights_error}`)
  const subtitle = [`交易日 ${d.day}`, at ? `快照 ${d.mode === 'live' ? '≈' : ''}${hms(at)}` : null, d.message, ...reasons].filter(Boolean).join(' · ')
  let st: Status, text: string
  if (d.mode === 'live') {
    const stale = !!d.schedule?.is_open && !!at && (now.getTime() - at.getTime()) / 1000 > STALE_SECONDS
    ;[st, text] = stale ? ['warn', '快照已过期'] : ['ok', '盘中 · 暂计']
  } else if (d.mode === 'final') {
    ;[st, text] = d.mismatch_count ? ['warn', `盘后定版 · ≠ 直取 ${d.mismatch_count} 处`] : ['ok', '盘后定版']
  } else {
    ;[st, text] = ['closed', '历史 · 只读']
  }
  if (reasons.length && st !== 'closed') st = 'warn'
  return { s: st, statusText: text, title, subtitle }
}

export function BoardPage() {
  const q = useSearch()
  const rawDay = q.get('day'), rawAt = q.get('at')
  const day = rawDay && DAY.test(rawDay) ? rawDay : null
  const at = day && rawAt && TIME.test(rawAt) ? rawAt : null
  const view = q.get('view') === 'compare' ? 'compare' : 'ladder'

  const fetcher = useCallback((signal: AbortSignal) => fetchBoard(day, at, signal), [day, at])
  // 刷新节奏取决于最近一次数据：只有盘中实时视图续取；休市时 60 秒（§10）
  const [cadence, setCadence] = useState({ liveData: true, closed: false })
  const live = !day && !at && cadence.liveData
  const poll = usePolling(fetcher, { intervalMs: cadence.closed ? 60_000 : 15_000, minIntervalMs: 15_000, enabled: live })
  useEffect(() => {
    const x = poll.data
    if (x) setCadence({ liveData: x.mode === 'live', closed: x.schedule ? !x.schedule.is_open : false })
  }, [poll.data])

  // 日期或回放时刻变化：重新取数（保留旧数据直到新数据到达）
  const first = useRef(true)
  const { refresh } = poll
  useEffect(() => {
    if (first.current) { first.current = false; return }
    refresh()
  }, [day, at, refresh])

  const [picked, setPicked] = useState<string | null>(null)
  const fresh = <Freshness at={poll.fetchedAt} paused={poll.paused} onTogglePause={() => poll.setPaused(!poll.paused)} onRefresh={poll.refresh} error={poll.error} />
  const d = poll.data
  if (!d) {
    return <><div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>{fresh}</div>
      {poll.error ? <Hero s="bad" title="打板看板接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />}</>
  }
  const h = heroOf(d, new Date())
  return (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        {d.available && <div style={{ flex: '1 1 320px', minWidth: 0 }}><DayControls data={d} urlDay={day} urlAt={at} /></div>}
        <div style={{ marginLeft: 'auto', marginBottom: 8 }}>{fresh}</div>
      </div>
      <Hero {...h} />
      {d.available && d.sentiment && <SentimentBar s={d.sentiment} provisional={!!d.provisional} />}
      {d.available && (
        <Panel title={view === 'ladder' ? '谁在封板、梯队如何？' : '昨日涨停今天怎样？'}
          info={view === 'ladder' ? '列为同花顺概念（每只票只归一列：当日涨停家数最多的概念），行为今日连板高度；≈ 为 15 秒快照观测值。' : undefined}
          extra={<Segmented size="small" value={view} onChange={(v) => setSearch({ view: v === 'ladder' ? null : String(v) })}
            options={[{ value: 'ladder', label: '题材梯队' }, { value: 'compare', label: '昨日涨停今日表现' }]} />}>
          {view === 'ladder' ? <LadderMatrix data={d} onPick={setPicked} /> : <div data-testid="view-compare" />}
        </Panel>
      )}
      {d.available && d.day && <StockDrawer day={d.day} symbol={picked} card={picked ? d.cards?.[picked] : undefined} onClose={() => setPicked(null)} />}
    </>
  )
}
