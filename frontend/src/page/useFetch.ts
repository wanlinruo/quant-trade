import { useEffect, useState } from 'react'
import { registerPageRefresh } from '../shell/refresh'

/** 一次性读取（随依赖变化重取），不轮询；读取失败给出 error，不保留伪造值。 */
export function useFetch<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; fetchedAt: Date | null; refresh(): void } {
  const [s, setS] = useState<{ data: T | null; error: string | null; fetchedAt: Date | null }>({ data: null, error: null, fetchedAt: null })
  const [generation, setGeneration] = useState(0)
  useEffect(() => registerPageRefresh(() => setGeneration((n) => n + 1)), [])
  useEffect(() => {
    const c = new AbortController()
    setS({ data: null, error: null, fetchedAt: null })
    fn(c.signal).then((data) => setS({ data, error: null, fetchedAt: new Date() }), (e: Error) => { if (e.name !== 'AbortError') setS({ data: null, error: e.message, fetchedAt: null }) })
    return () => c.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, generation])
  return { ...s, refresh: () => setGeneration((n) => n + 1) }
}
