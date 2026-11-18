import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from './client'
import type { ShellStatus } from './contracts.gen'

export type { ShellStatus } from './contracts.gen'
export type KillState = ShellStatus['kill']['state']

export interface ShellResult { data: ShellStatus | null; error: string | null }

const URL_STATUS = '/api/app/shell/status'

/** 读取失败一律返回 data=null（前端按“未知”显示），绝不沿用上一次成功的结果。 */
export async function fetchShellStatus(signal?: AbortSignal): Promise<ShellResult> {
  try {
    const body = await getJson<ShellStatus>(URL_STATUS, signal)
    if (!body || body.schema_version !== 1 || !body.kill || !body.live) return { data: null, error: '状态接口格式不符' }
    return { data: body, error: null }
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    return { data: null, error: (e as Error).message }
  }
}

export function useShellStatus(intervalMs = 5000, paused = false): ShellResult & { refresh(): void } {
  const [res, setRes] = useState<ShellResult>({ data: null, error: null })
  const ctl = useRef<AbortController | null>(null)
  const refresh = useCallback(() => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    fetchShellStatus(c.signal).then(setRes, () => {})
  }, [])
  useEffect(() => {
    if (paused) return
    refresh()
    const t = setInterval(refresh, intervalMs)
    return () => { clearInterval(t); ctl.current?.abort() }
  }, [intervalMs, paused, refresh])
  return { ...res, refresh }
}
