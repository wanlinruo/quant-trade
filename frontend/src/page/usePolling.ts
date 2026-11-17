import { useCallback, useEffect, useRef, useState } from 'react'
import { registerPageRefresh } from '../shell/refresh'

export const MAX_BACKOFF_MS = 120_000

export interface PollingOptions { intervalMs: number; minIntervalMs?: number; paused?: boolean }

export interface Polling<T> {
  data: T | null; error: string | null; fetchedAt: Date | null; loading: boolean
  refresh(): void; paused: boolean; setPaused(b: boolean): void
}

/**
 * 页面数据轮询。失败时保留上一次结果并给出 error（页面须用 Freshness 标明“刷新失败”），
 * 下次间隔翻倍、上限 120 秒；标签页隐藏时不发请求；间隔不短于 minIntervalMs（§10）。
 */
export function usePolling<T>(fetcher: (signal: AbortSignal) => Promise<T>, opts: PollingOptions): Polling<T> {
  const base = Math.max(opts.intervalMs, opts.minIntervalMs ?? 0)
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(false)
  const [paused, setPaused] = useState(opts.paused ?? false)

  const fn = useRef(fetcher)
  fn.current = fetcher
  const delay = useRef(base)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const ctl = useRef<AbortController | null>(null)
  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const lastAt = useRef(0)

  const run = useCallback(async () => {
    clearTimeout(timer.current)
    if (pausedRef.current) return
    const schedule = () => {
      clearTimeout(timer.current)
      if (!pausedRef.current) timer.current = setTimeout(() => { void run() }, delay.current)
    }
    if (typeof document !== 'undefined' && document.hidden) { schedule(); return }
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    setLoading(true)
    try {
      const d = await fn.current(c.signal)
      if (ctl.current !== c) return
      setData(d); setError(null); setFetchedAt(new Date())
      lastAt.current = Date.now()
      delay.current = base
    } catch (e) {
      if (ctl.current !== c || c.signal.aborted) return
      setError((e as Error)?.message || '刷新失败')
      delay.current = Math.min(delay.current * 2, MAX_BACKOFF_MS)
    } finally {
      if (ctl.current === c) { setLoading(false); schedule() }
    }
  }, [base])

  useEffect(() => {
    if (paused) return
    delay.current = base
    void run()
    return () => { clearTimeout(timer.current); ctl.current?.abort(); ctl.current = null }
  }, [paused, base, run])

  useEffect(() => {
    const onVis = () => { if (!document.hidden && !pausedRef.current && Date.now() - lastAt.current >= base) void run() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [base, run])

  const refresh = useCallback(() => { if (!pausedRef.current) void run() }, [run])
  useEffect(() => registerPageRefresh(refresh), [refresh])

  return { data, error, fetchedAt, loading, refresh, paused, setPaused }
}
