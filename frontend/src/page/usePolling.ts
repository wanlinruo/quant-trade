import { useCallback, useEffect, useRef, useState } from 'react'
import { registerPageRefresh } from '../shell/refresh'

export const MAX_BACKOFF_MS = 120_000

export interface PollingOptions {
  intervalMs: number; minIntervalMs?: number; paused?: boolean
  /** false 时只取一次、不自动续取（回看历史、定版数据）；R 键与“刷新”仍可手动取。 */
  enabled?: boolean
}

export interface Polling<T> {
  data: T | null; error: string | null; fetchedAt: Date | null; loading: boolean
  refresh(): void; paused: boolean; setPaused(b: boolean): void
}

/**
 * 页面数据轮询。失败时保留上一次结果并给出 error（页面须用 Freshness 标明“刷新失败”），
 * 下次间隔翻倍、上限 120 秒；标签页隐藏时不发请求；间隔不短于 minIntervalMs（§10）。
 * 间隔变化时按新间隔重新排期，不立即重取。
 */
export function usePolling<T>(fetcher: (signal: AbortSignal) => Promise<T>, opts: PollingOptions): Polling<T> {
  const base = Math.max(opts.intervalMs, opts.minIntervalMs ?? 0)
  const enabled = opts.enabled ?? true
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(false)
  const [paused, setPaused] = useState(opts.paused ?? false)

  const fn = useRef(fetcher)
  fn.current = fetcher
  const baseRef = useRef(base)
  const delay = useRef(base)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const ctl = useRef<AbortController | null>(null)
  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled
  const lastAt = useRef(0)
  const runRef = useRef<() => Promise<void>>(async () => {})

  const schedule = useCallback((ms: number) => {
    clearTimeout(timer.current)
    if (!pausedRef.current && enabledRef.current) timer.current = setTimeout(() => { void runRef.current() }, ms)
  }, [])

  const run = useCallback(async () => {
    clearTimeout(timer.current)
    if (pausedRef.current) return
    if (typeof document !== 'undefined' && document.hidden) { schedule(delay.current); return }
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    setLoading(true)
    try {
      const d = await fn.current(c.signal)
      if (ctl.current !== c) return
      setData(d); setError(null); setFetchedAt(new Date())
      lastAt.current = Date.now()
      delay.current = baseRef.current
    } catch (e) {
      if (ctl.current !== c || c.signal.aborted) return
      setError((e as Error)?.message || '刷新失败')
      delay.current = Math.min(delay.current * 2, MAX_BACKOFF_MS)
    } finally {
      if (ctl.current === c) { setLoading(false); schedule(delay.current) }
    }
  }, [schedule])
  runRef.current = run

  // 首次与暂停恢复：立即取
  useEffect(() => {
    if (paused) return
    void run()
    return () => { clearTimeout(timer.current); ctl.current?.abort(); ctl.current = null }
  }, [paused, run])

  // 间隔或启用状态变化：按剩余时间重新排期，不立即重取
  useEffect(() => {
    baseRef.current = base
    delay.current = base
    if (pausedRef.current || lastAt.current === 0 || ctl.current === null) return
    schedule(Math.max(0, base - (Date.now() - lastAt.current)))
  }, [base, enabled, schedule])

  useEffect(() => {
    const onVis = () => { if (!document.hidden && !pausedRef.current && enabledRef.current && Date.now() - lastAt.current >= baseRef.current) void run() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [run])

  const refresh = useCallback(() => { if (!pausedRef.current) void run() }, [run])
  useEffect(() => registerPageRefresh(refresh), [refresh])

  return { data, error, fetchedAt, loading, refresh, paused, setPaused }
}
