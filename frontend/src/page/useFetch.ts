import { useEffect, useState } from 'react'

/** 一次性读取（随依赖变化重取），不轮询；读取失败给出 error，不保留伪造值。 */
export function useFetch<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): { data: T | null; error: string | null } {
  const [s, setS] = useState<{ data: T | null; error: string | null }>({ data: null, error: null })
  useEffect(() => {
    const c = new AbortController()
    setS({ data: null, error: null })
    fn(c.signal).then((data) => setS({ data, error: null }), (e: Error) => { if (e.name !== 'AbortError') setS({ data: null, error: e.message }) })
    return () => c.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return s
}
