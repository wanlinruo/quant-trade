import { useCallback, useEffect } from 'react'
import { Result, Tabs } from 'antd'
import { fetchBacktests, type BacktestList } from '../../api/research'
import { Hero } from '../../page/Hero'
import { usePolling } from '../../page/usePolling'
import { setSearch, useSearch } from '../../shell/router'
import { BacktestDetailView } from './BacktestDetailView'
import { BacktestPicker } from './BacktestPicker'
import { ResearchJobs } from './ResearchJobs'

function BacktestTab() {
  const q = useSearch()
  const id = q.get('id')
  const fetcher = useCallback((s: AbortSignal) => fetchBacktests(s), [])
  const poll = usePolling(fetcher, { intervalMs: 60_000, enabled: false })
  const d = poll.data as BacktestList | null
  const firstReport = d && d.enabled ? d.rows.find((r) => r.has_report)?.id ?? d.rows[0]?.id ?? null : null
  useEffect(() => { if (!id && firstReport) setSearch({ id: firstReport }) }, [id, firstReport])

  if (!d) return poll.error ? <Hero s="bad" title="回测接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="回测控制台未启用" subTitle="当前部署没有接入回测结果目录。" />
  if (d.rows.length === 0) return <Hero s="idle" title="还没有回测" subtitle="在“策略”页选择策略后新建回测。" />
  return (
    <>
      <div style={{ marginBottom: 12 }}><BacktestPicker rows={d.rows} value={id} onChange={(v) => setSearch({ id: v })} /></div>
      {id && <BacktestDetailView key={id} id={id} onFinished={poll.refresh} />}
    </>
  )
}

/** 回测（§8.11）：回测报告与研究任务队列两个子标签，写进 ?tab=。 */
export function BacktestPage() {
  const q = useSearch()
  const tab = q.get('tab') === 'research' ? 'research' : 'backtest'
  return (
    <Tabs activeKey={tab} onChange={(k) => setSearch({ tab: k === 'backtest' ? null : k })}
      items={[
        { key: 'backtest', label: '回测', children: tab === 'backtest' ? <BacktestTab /> : null },
        { key: 'research', label: '研究任务', children: tab === 'research' ? <ResearchJobs /> : null },
      ]} />
  )
}
