import { useCallback, useEffect, useState } from 'react'
import { Button, Progress, Result } from 'antd'
import { cancelBacktest, fetchBacktest, type BacktestDetail } from '../../api/research'
import { useConfirmAction } from '../../page/ConfirmAction'
import { Hero } from '../../page/Hero'
import { Panel } from '../../page/Panel'
import { usePolling } from '../../page/usePolling'
import { usePalette } from '../../theme/context'
import { MONO } from '../../ui/Num'
import { BacktestReport } from './BacktestReport'

const STATUS: Record<string, string> = { queued: '排队中', running: '运行中', failed: '失败', cancelled: '已取消', interrupted: '已中断', succeeded: '已完成（报告尚未发布）' }
const ACTIVE = new Set(['queued', 'running'])

/** 单次回测：有报告时显示报告；排队/运行中显示进度并每 5 秒刷新，完成后停止。 */
export function BacktestDetailView({ id, onFinished }: { id: string; onFinished(): void }) {
  const p = usePalette()
  const { confirm, holder } = useConfirmAction()
  const fetcher = useCallback((s: AbortSignal) => fetchBacktest(id, s), [id])
  const [active, setActive] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const poll = usePolling(fetcher, { intervalMs: 5_000, enabled: active })
  const d = poll.data as BacktestDetail | null
  const running = !!d && d.enabled && d.report === false && ACTIVE.has(d.job.status)
  useEffect(() => {
    if (!d) return
    setActive(running)
    if (!running) onFinished()
  }, [d, running, onFinished])

  if (!d) return poll.error ? <Hero s="bad" title="回测接口无法访问" subtitle={poll.error} /> : <Hero s="running" title="加载中" />
  if (!d.enabled) return <Result status="info" title="回测控制台未启用" />
  if (d.report) return <BacktestReport d={d} />
  const j = d.job
  const cancel = async () => {
    if (!(await confirm('取消回测', `取消排队或运行中的回测 ${j.id.slice(0, 8)}，已产生的进度不会保留为报告。`))) return
    try { await cancelBacktest(j.id); setErr(null); poll.refresh() } catch (e) { setErr((e as Error).message) }
  }
  return (
    <>
      <Hero s={running ? 'running' : j.status === 'failed' ? 'bad' : 'warn'} statusText={STATUS[j.status] ?? j.status}
        title={`${j.strategy_name ?? ''} · ${String(j.config.start ?? '')} ~ ${String(j.config.end ?? '')}`}
        subtitle={`回测 ${j.id}${j.error ? ` · ${j.error}` : ''}`} />
      <Panel title="跑到哪了？" extra={running ? <Button size="small" danger onClick={cancel}>取消回测</Button> : undefined}>
        {err && <div role="alert" style={{ color: p.bad, marginBottom: 8 }}>{err}</div>}
        {j.total ? (
          <>
            <Progress percent={j.percent ?? 0} size="small" />
            <div style={{ fontSize: 12, color: p.muted }}>{j.progress} / {j.total} 个交易日 · 当前 {j.current_session ?? '未知'}</div>
          </>
        ) : <div style={{ color: p.muted }}>{STATUS[j.status] ?? j.status}，尚未开始计算交易日</div>}
        {d.log_tail && <pre style={{ fontFamily: MONO, fontSize: 11, maxHeight: 240, overflow: 'auto', background: p.panel2, padding: 8, marginTop: 8, whiteSpace: 'pre-wrap' }}>{d.log_tail}</pre>}
      </Panel>
      {holder}
    </>
  )
}
